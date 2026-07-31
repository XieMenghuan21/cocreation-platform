"""Persist workspace references through internal version history ids.

Revision ID: 20260726_0005
Revises: 20260726_0004
Create Date: 2026-07-26
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "20260726_0005"
down_revision: str | None = "20260726_0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _backfill_reference_ids() -> None:
    bind = op.get_bind()
    workspaces = sa.table(
        "workspace_states",
        sa.column("id", sa.Uuid()),
        sa.column("user_id", sa.String()),
        sa.column("selected_project_id", sa.String()),
        sa.column("selected_reference_version_id", sa.String()),
        sa.column("selected_reference_version_history_id", sa.Integer()),
    )
    projects = sa.table(
        "cocreation_project_histories",
        sa.column("id", sa.Integer()),
        sa.column("user_id", sa.String()),
        sa.column("project_id", sa.String()),
    )
    versions = sa.table(
        "cocreation_project_version_histories",
        sa.column("id", sa.Integer()),
        sa.column("project_history_id", sa.Integer()),
        sa.column("user_id", sa.String()),
        sa.column("version_id", sa.String()),
    )
    for workspace in bind.execute(
        sa.select(
            workspaces.c.id,
            workspaces.c.user_id,
            workspaces.c.selected_project_id,
            workspaces.c.selected_reference_version_id,
        ).where(workspaces.c.selected_reference_version_id.is_not(None))
    ):
        statement = (
            sa.select(versions.c.id)
            .select_from(
                versions.join(
                    projects,
                    versions.c.project_history_id == projects.c.id,
                )
            )
            .where(
                versions.c.user_id == workspace.user_id,
                projects.c.user_id == workspace.user_id,
                versions.c.version_id
                == workspace.selected_reference_version_id,
            )
        )
        if workspace.selected_project_id is not None:
            statement = statement.where(
                projects.c.project_id == workspace.selected_project_id
            )
        matches = list(bind.execute(statement.limit(2)).scalars())
        if len(matches) != 1:
            continue
        bind.execute(
            workspaces.update()
            .where(workspaces.c.id == workspace.id)
            .values(selected_reference_version_history_id=matches[0])
        )


def upgrade() -> None:
    if op.get_bind().dialect.name == "sqlite":
        with op.batch_alter_table("workspace_states") as batch_op:
            batch_op.add_column(
                sa.Column(
                    "selected_reference_version_history_id",
                    sa.Integer(),
                    nullable=True,
                )
            )
            batch_op.create_foreign_key(
                "fk_workspace_selected_reference_version_history",
                "cocreation_project_version_histories",
                ["selected_reference_version_history_id"],
                ["id"],
                ondelete="SET NULL",
            )
            batch_op.create_index(
                "ix_workspace_states_selected_reference_version_history_id",
                ["selected_reference_version_history_id"],
            )
    else:
        op.add_column(
            "workspace_states",
            sa.Column(
                "selected_reference_version_history_id",
                sa.Integer(),
                sa.ForeignKey(
                    "cocreation_project_version_histories.id",
                    name="fk_workspace_selected_reference_version_history",
                    ondelete="SET NULL",
                ),
                nullable=True,
            ),
        )
        op.create_index(
            "ix_workspace_states_selected_reference_version_history_id",
            "workspace_states",
            ["selected_reference_version_history_id"],
        )
    _backfill_reference_ids()


def downgrade() -> None:
    if op.get_bind().dialect.name == "sqlite":
        with op.batch_alter_table("workspace_states") as batch_op:
            batch_op.drop_index(
                "ix_workspace_states_selected_reference_version_history_id"
            )
            batch_op.drop_constraint(
                "fk_workspace_selected_reference_version_history",
                type_="foreignkey",
            )
            batch_op.drop_column("selected_reference_version_history_id")
    else:
        op.drop_index(
            "ix_workspace_states_selected_reference_version_history_id",
            table_name="workspace_states",
        )
        op.drop_constraint(
            "fk_workspace_selected_reference_version_history",
            "workspace_states",
            type_="foreignkey",
        )
        op.drop_column(
            "workspace_states",
            "selected_reference_version_history_id",
        )
