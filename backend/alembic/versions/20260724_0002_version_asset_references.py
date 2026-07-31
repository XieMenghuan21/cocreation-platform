"""Add generated asset references to persisted project versions.

Revision ID: 20260724_0002
Revises: 20260723_0001
Create Date: 2026-07-24
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "20260724_0002"
down_revision: str | None = "20260723_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("cocreation_project_version_histories") as batch_op:
        batch_op.add_column(sa.Column("script_asset_id", sa.Uuid(), nullable=True))
        batch_op.add_column(sa.Column("output_asset_id", sa.Uuid(), nullable=True))
        batch_op.create_foreign_key(
            "fk_cocreation_version_script_asset",
            "assets",
            ["script_asset_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch_op.create_foreign_key(
            "fk_cocreation_version_output_asset",
            "assets",
            ["output_asset_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch_op.create_index(
            "ix_cocreation_project_version_histories_script_asset_id",
            ["script_asset_id"],
        )
        batch_op.create_index(
            "ix_cocreation_project_version_histories_output_asset_id",
            ["output_asset_id"],
        )


def downgrade() -> None:
    with op.batch_alter_table("cocreation_project_version_histories") as batch_op:
        batch_op.drop_index(
            "ix_cocreation_project_version_histories_output_asset_id",
        )
        batch_op.drop_index(
            "ix_cocreation_project_version_histories_script_asset_id",
        )
        batch_op.drop_constraint(
            "fk_cocreation_version_output_asset",
            type_="foreignkey",
        )
        batch_op.drop_constraint(
            "fk_cocreation_version_script_asset",
            type_="foreignkey",
        )
        batch_op.drop_column("output_asset_id")
        batch_op.drop_column("script_asset_id")
