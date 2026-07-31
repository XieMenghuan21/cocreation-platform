"""Create database asset library publication relationships.

Revision ID: 20260726_0003
Revises: 20260724_0002
Create Date: 2026-07-26
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "20260726_0003"
down_revision: str | None = "20260724_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "cocreation_asset_library_entries",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.String(length=120), nullable=False),
        sa.Column("version_history_id", sa.Integer(), nullable=False),
        sa.Column("asset_id", sa.Uuid(), nullable=False),
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
            "asset_id",
            name="uq_cocreation_asset_library_entries_asset",
        ),
    )
    op.create_index(
        "ix_cocreation_asset_library_entries_user_id",
        "cocreation_asset_library_entries",
        ["user_id"],
    )
    op.create_index(
        "ix_cocreation_asset_library_entries_user_version",
        "cocreation_asset_library_entries",
        ["user_id", "version_history_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_cocreation_asset_library_entries_user_version",
        table_name="cocreation_asset_library_entries",
    )
    op.drop_index(
        "ix_cocreation_asset_library_entries_user_id",
        table_name="cocreation_asset_library_entries",
    )
    op.drop_table("cocreation_asset_library_entries")
