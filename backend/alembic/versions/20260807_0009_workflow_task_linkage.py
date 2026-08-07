"""Add conversation/node linkage to workflow tasks.

Revision ID: 20260807_0009
Revises: 20260807_0008
Create Date: 2026-08-07
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "20260807_0009"
down_revision: str | None = "20260807_0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("workflow_tasks", recreate="auto") as batch_op:
        batch_op.add_column(sa.Column("conversation_id", sa.Uuid(), nullable=True))
        batch_op.add_column(sa.Column("workspace_node_id", sa.Uuid(), nullable=True))
        batch_op.create_index("ix_workflow_tasks_conversation_id", ["conversation_id"])
        batch_op.create_index("ix_workflow_tasks_workspace_node_id", ["workspace_node_id"])


def downgrade() -> None:
    with op.batch_alter_table("workflow_tasks", recreate="auto") as batch_op:
        batch_op.drop_index("ix_workflow_tasks_workspace_node_id")
        batch_op.drop_index("ix_workflow_tasks_conversation_id")
        batch_op.drop_column("workspace_node_id")
        batch_op.drop_column("conversation_id")
