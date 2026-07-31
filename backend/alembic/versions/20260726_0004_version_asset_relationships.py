"""Normalize version assets and project cover references.

Revision ID: 20260726_0004
Revises: 20260726_0003
Create Date: 2026-07-26
"""
from collections.abc import Sequence
from datetime import datetime, timezone
import json
import re
from uuid import UUID

from alembic import op
import sqlalchemy as sa

revision: str = "20260726_0004"
down_revision: str | None = "20260726_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_ASSET_DOWNLOAD_PATTERN = re.compile(
    r"^/api/v1/assets/([0-9a-fA-F-]{36})/download$"
)


def _uuid(value: object) -> UUID | None:
    if isinstance(value, UUID):
        return value
    if not isinstance(value, str):
        return None
    try:
        return UUID(value)
    except ValueError:
        return None


def _json_list(value: object) -> list[dict[str, object]]:
    decoded = value
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except json.JSONDecodeError:
            return []
    if not isinstance(decoded, list):
        return []
    return [item for item in decoded if isinstance(item, dict)]


def _tables() -> tuple[sa.TableClause, ...]:
    assets = sa.table(
        "assets",
        sa.column("id", sa.Uuid()),
        sa.column("user_id", sa.String()),
    )
    projects = sa.table(
        "cocreation_project_histories",
        sa.column("id", sa.Integer()),
        sa.column("user_id", sa.String()),
        sa.column("last_image_url", sa.Text()),
        sa.column("last_image_asset_id", sa.Uuid()),
    )
    versions = sa.table(
        "cocreation_project_version_histories",
        sa.column("id", sa.Integer()),
        sa.column("user_id", sa.String()),
        sa.column("script_asset_id", sa.Uuid()),
        sa.column("output_asset_id", sa.Uuid()),
        sa.column("generated_assets", sa.JSON()),
    )
    entries = sa.table(
        "cocreation_version_asset_entries",
        sa.column("user_id", sa.String()),
        sa.column("version_history_id", sa.Integer()),
        sa.column("asset_id", sa.Uuid()),
        sa.column("role", sa.String()),
        sa.column("kind", sa.String()),
        sa.column("created_at", sa.DateTime(timezone=True)),
    )
    return assets, projects, versions, entries


def _upgrade_relationship_data() -> None:
    bind = op.get_bind()
    assets, projects, versions, entries = _tables()
    asset_owners = {
        asset_id: user_id
        for asset_id, user_id in bind.execute(
            sa.select(assets.c.id, assets.c.user_id)
        )
    }
    rows: list[dict[str, object]] = []
    seen: set[tuple[int, UUID, str]] = set()
    migrated_at = datetime.now(timezone.utc)

    def add_entry(
        version_history_id: int,
        user_id: str,
        asset_value: object,
        role: str,
        kind: str,
    ) -> None:
        asset_id = _uuid(asset_value)
        key = (version_history_id, asset_id, role) if asset_id is not None else None
        if (
            asset_id is None
            or asset_owners.get(asset_id) != user_id
            or key in seen
        ):
            return
        seen.add(key)
        rows.append(
            {
                "user_id": user_id,
                "version_history_id": version_history_id,
                "asset_id": asset_id,
                "role": role,
                "kind": kind,
                "created_at": migrated_at,
            }
        )

    for row in bind.execute(sa.select(versions)):
        add_entry(row.id, row.user_id, row.script_asset_id, "script", "script")
        add_entry(row.id, row.user_id, row.output_asset_id, "output", "output")
        for generated in _json_list(row.generated_assets):
            add_entry(
                row.id,
                row.user_id,
                generated.get("assetId"),
                "generated",
                str(generated.get("kind") or generated.get("assetType") or "generated")[:64],
            )
    if rows:
        bind.execute(entries.insert(), rows)

    for row in bind.execute(
        sa.select(
            projects.c.id,
            projects.c.user_id,
            projects.c.last_image_url,
        )
    ):
        if not isinstance(row.last_image_url, str):
            continue
        matched = _ASSET_DOWNLOAD_PATTERN.fullmatch(row.last_image_url)
        asset_id = _uuid(matched.group(1)) if matched else None
        if asset_id is None or asset_owners.get(asset_id) != row.user_id:
            continue
        bind.execute(
            projects.update()
            .where(projects.c.id == row.id)
            .values(last_image_asset_id=asset_id)
        )


def _downgrade_relationship_data() -> None:
    bind = op.get_bind()
    _, projects, versions, entries = _tables()
    grouped: dict[int, list[object]] = {}
    for row in bind.execute(sa.select(entries)):
        grouped.setdefault(row.version_history_id, []).append(row)
    for (version_id,) in bind.execute(sa.select(versions.c.id)):
        asset_entries = grouped.get(version_id, [])
        values: dict[str, object] = {
            "script_asset_id": None,
            "output_asset_id": None,
            "generated_assets": [],
        }
        generated: list[dict[str, object]] = []
        for entry in asset_entries:
            if entry.role == "script":
                values["script_asset_id"] = entry.asset_id
            elif entry.role == "output":
                values["output_asset_id"] = entry.asset_id
            elif entry.role == "generated":
                generated.append(
                    {
                        "assetId": str(entry.asset_id),
                        "kind": entry.kind,
                    }
                )
        values["generated_assets"] = generated
        bind.execute(
            versions.update()
            .where(versions.c.id == version_id)
            .values(**values)
        )

    for project_id, asset_id in bind.execute(
        sa.select(projects.c.id, projects.c.last_image_asset_id)
    ):
        bind.execute(
            projects.update()
            .where(projects.c.id == project_id)
            .values(
                last_image_url=(
                    f"/api/v1/assets/{asset_id}/download"
                    if asset_id is not None
                    else None
                )
            )
        )


def upgrade() -> None:
    if op.get_bind().dialect.name == "sqlite":
        op.execute(
            "CREATE TEMP TABLE _v4_versions_backup AS "
            "SELECT * FROM cocreation_project_version_histories"
        )
        op.execute(
            "CREATE TEMP TABLE _v4_library_backup AS "
            "SELECT * FROM cocreation_asset_library_entries"
        )
        with op.batch_alter_table("cocreation_project_histories") as batch_op:
            batch_op.add_column(
                sa.Column("last_image_asset_id", sa.Uuid(), nullable=True)
            )
            batch_op.create_foreign_key(
                "fk_cocreation_project_last_image_asset",
                "assets",
                ["last_image_asset_id"],
                ["id"],
                ondelete="SET NULL",
            )
            batch_op.create_index(
                "ix_cocreation_project_histories_last_image_asset_id",
                ["last_image_asset_id"],
            )
        op.execute(
            "INSERT OR IGNORE INTO cocreation_project_version_histories "
            "SELECT * FROM _v4_versions_backup"
        )
        op.execute(
            "INSERT OR IGNORE INTO cocreation_asset_library_entries "
            "SELECT * FROM _v4_library_backup"
        )
        op.execute("DROP TABLE _v4_library_backup")
        op.execute("DROP TABLE _v4_versions_backup")
    else:
        op.add_column(
            "cocreation_project_histories",
            sa.Column(
                "last_image_asset_id",
                sa.Uuid(),
                sa.ForeignKey(
                    "assets.id",
                    name="fk_cocreation_project_last_image_asset",
                    ondelete="SET NULL",
                ),
                nullable=True,
            ),
        )
        op.create_index(
            "ix_cocreation_project_histories_last_image_asset_id",
            "cocreation_project_histories",
            ["last_image_asset_id"],
        )

    op.create_table(
        "cocreation_version_asset_entries",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.String(length=120), nullable=False),
        sa.Column("version_history_id", sa.Integer(), nullable=False),
        sa.Column("asset_id", sa.Uuid(), nullable=False),
        sa.Column("role", sa.String(length=32), nullable=False),
        sa.Column("kind", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["asset_id"],
            ["assets.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["version_history_id"],
            ["cocreation_project_version_histories.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "version_history_id",
            "asset_id",
            "role",
            name="uq_cocreation_version_asset_entries_version_asset_role",
        ),
    )
    op.create_index(
        "ix_cocreation_version_asset_entries_user_id",
        "cocreation_version_asset_entries",
        ["user_id"],
    )
    op.create_index(
        "ix_cocreation_version_asset_entries_user_version",
        "cocreation_version_asset_entries",
        ["user_id", "version_history_id"],
    )
    op.create_index(
        "ix_cocreation_version_asset_entries_asset",
        "cocreation_version_asset_entries",
        ["asset_id"],
    )
    _upgrade_relationship_data()


def downgrade() -> None:
    _downgrade_relationship_data()
    op.drop_index(
        "ix_cocreation_version_asset_entries_asset",
        table_name="cocreation_version_asset_entries",
    )
    op.drop_index(
        "ix_cocreation_version_asset_entries_user_version",
        table_name="cocreation_version_asset_entries",
    )
    op.drop_index(
        "ix_cocreation_version_asset_entries_user_id",
        table_name="cocreation_version_asset_entries",
    )
    op.drop_table("cocreation_version_asset_entries")
    if op.get_bind().dialect.name == "sqlite":
        op.execute(
            "CREATE TEMP TABLE _v4_versions_backup AS "
            "SELECT * FROM cocreation_project_version_histories"
        )
        op.execute(
            "CREATE TEMP TABLE _v4_library_backup AS "
            "SELECT * FROM cocreation_asset_library_entries"
        )
        with op.batch_alter_table("cocreation_project_histories") as batch_op:
            batch_op.drop_index(
                "ix_cocreation_project_histories_last_image_asset_id"
            )
            batch_op.drop_constraint(
                "fk_cocreation_project_last_image_asset",
                type_="foreignkey",
            )
            batch_op.drop_column("last_image_asset_id")
        op.execute(
            "INSERT OR IGNORE INTO cocreation_project_version_histories "
            "SELECT * FROM _v4_versions_backup"
        )
        op.execute(
            "INSERT OR IGNORE INTO cocreation_asset_library_entries "
            "SELECT * FROM _v4_library_backup"
        )
        op.execute("DROP TABLE _v4_library_backup")
        op.execute("DROP TABLE _v4_versions_backup")
    else:
        op.drop_index(
            "ix_cocreation_project_histories_last_image_asset_id",
            table_name="cocreation_project_histories",
        )
        op.drop_constraint(
            "fk_cocreation_project_last_image_asset",
            "cocreation_project_histories",
            type_="foreignkey",
        )
        op.drop_column("cocreation_project_histories", "last_image_asset_id")
