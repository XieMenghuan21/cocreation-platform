"""Add workspace graph tables.

Revision ID: 20260807_0008
Revises: 20260807_0007
Create Date: 2026-08-07
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "20260807_0008"
down_revision: str | None = "20260807_0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "workspace_nodes",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.String(length=120), nullable=False),
        sa.Column("conversation_id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.String(length=160), nullable=True),
        sa.Column("parent_id", sa.Uuid(), nullable=True),
        sa.Column("branch_id", sa.Uuid(), nullable=True),
        sa.Column("node_type", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=64), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("agent_key", sa.String(length=120), nullable=True),
        sa.Column("task_id", sa.String(length=160), nullable=True),
        sa.Column("version_id", sa.String(length=160), nullable=True),
        sa.Column("input_data", sa.JSON(), nullable=False),
        sa.Column("output_data", sa.JSON(), nullable=False),
        sa.Column("ui_data", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["conversation_id"], ["conversations.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["parent_id"], ["workspace_nodes.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_workspace_nodes_user_id", "workspace_nodes", ["user_id"])
    op.create_index("ix_workspace_nodes_conversation_id", "workspace_nodes", ["conversation_id"])
    op.create_index("ix_workspace_nodes_status", "workspace_nodes", ["status"])
    op.create_index("ix_workspace_nodes_node_type", "workspace_nodes", ["node_type"])
    op.create_index("ix_workspace_nodes_project_id", "workspace_nodes", ["project_id"])
    op.create_index("ix_workspace_nodes_task_id", "workspace_nodes", ["task_id"])
    op.create_index("ix_workspace_nodes_parent_id", "workspace_nodes", ["parent_id"])
    op.create_index(
        "ix_workspace_nodes_conv_status",
        "workspace_nodes",
        ["conversation_id", "status"],
    )
    op.create_index(
        "ix_workspace_nodes_project_created",
        "workspace_nodes",
        ["project_id", "created_at"],
    )

    op.create_table(
        "workspace_node_assets",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("node_id", sa.Uuid(), nullable=False),
        sa.Column("asset_id", sa.Uuid(), nullable=False),
        sa.Column("role", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["node_id"], ["workspace_nodes.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["asset_id"], ["assets.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "node_id", "asset_id", "role", name="uq_workspace_node_assets_node_asset_role"
        ),
    )
    op.create_index("ix_workspace_node_assets_node_id", "workspace_node_assets", ["node_id"])
    op.create_index("ix_workspace_node_assets_asset_id", "workspace_node_assets", ["asset_id"])


def downgrade() -> None:
    op.drop_table("workspace_node_assets")
    op.drop_table("workspace_nodes")
