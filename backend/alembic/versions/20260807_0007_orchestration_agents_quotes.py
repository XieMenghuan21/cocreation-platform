"""Add orchestration agent runs and quote tables.

Revision ID: 20260807_0007
Revises: 20260807_0006
Create Date: 2026-08-07
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "20260807_0007"
down_revision: str | None = "20260807_0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "workflow_instances",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.String(length=120), nullable=False),
        sa.Column("project_id", sa.String(length=160), nullable=False),
        sa.Column("conversation_id", sa.Uuid(), nullable=True),
        sa.Column("status", sa.String(length=64), nullable=False),
        sa.Column("input_snapshot", sa.JSON(), nullable=False),
        sa.Column("output_snapshot", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_workflow_instances_user_id", "workflow_instances", ["user_id"])
    op.create_index("ix_workflow_instances_project_id", "workflow_instances", ["project_id"])
    op.create_index(
        "ix_workflow_instances_project_created",
        "workflow_instances",
        ["project_id", "created_at"],
    )

    op.create_table(
        "agent_runs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workflow_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.String(length=120), nullable=False),
        sa.Column("project_id", sa.String(length=160), nullable=False),
        sa.Column("conversation_id", sa.Uuid(), nullable=True),
        sa.Column("agent_type", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=64), nullable=False),
        sa.Column("input_snapshot", sa.JSON(), nullable=False),
        sa.Column("output_snapshot", sa.JSON(), nullable=False),
        sa.Column("error_code", sa.String(length=120), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("retry_count", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["workflow_id"], ["workflow_instances.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_agent_runs_workflow_id", "agent_runs", ["workflow_id"])
    op.create_index("ix_agent_runs_user_id", "agent_runs", ["user_id"])
    op.create_index("ix_agent_runs_project_id", "agent_runs", ["project_id"])
    op.create_index("ix_agent_runs_conversation_id", "agent_runs", ["conversation_id"])
    op.create_index("ix_agent_runs_agent_type", "agent_runs", ["agent_type"])
    op.create_index("ix_agent_runs_status", "agent_runs", ["status"])
    op.create_index("ix_agent_runs_workflow_created", "agent_runs", ["workflow_id", "created_at"])
    op.create_index("ix_agent_runs_project_created", "agent_runs", ["project_id", "created_at"])

    op.create_table(
        "agent_run_events",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("workflow_id", sa.Uuid(), nullable=False),
        sa.Column("agent_run_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.String(length=120), nullable=False),
        sa.Column("project_id", sa.String(length=160), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=64), nullable=False),
        sa.Column("progress", sa.Integer(), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("event_data", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["agent_run_id"], ["agent_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_agent_run_events_workflow_id", "agent_run_events", ["workflow_id"])
    op.create_index("ix_agent_run_events_agent_run_id", "agent_run_events", ["agent_run_id"])
    op.create_index("ix_agent_run_events_user_id", "agent_run_events", ["user_id"])
    op.create_index("ix_agent_run_events_project_id", "agent_run_events", ["project_id"])
    op.create_index(
        "ix_agent_run_events_agent_sequence",
        "agent_run_events",
        ["agent_run_id", "sequence"],
    )
    op.create_index(
        "ix_agent_run_events_project_created",
        "agent_run_events",
        ["project_id", "created_at"],
    )

    op.create_table(
        "agent_artifact_links",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("workflow_id", sa.Uuid(), nullable=False),
        sa.Column("agent_run_id", sa.Uuid(), nullable=False),
        sa.Column("asset_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.String(length=120), nullable=False),
        sa.Column("project_id", sa.String(length=160), nullable=False),
        sa.Column("role", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["agent_run_id"], ["agent_runs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["asset_id"], ["assets.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_agent_artifact_links_workflow_id", "agent_artifact_links", ["workflow_id"])
    op.create_index("ix_agent_artifact_links_agent_run_id", "agent_artifact_links", ["agent_run_id"])
    op.create_index("ix_agent_artifact_links_asset_id", "agent_artifact_links", ["asset_id"])
    op.create_index("ix_agent_artifact_links_user_id", "agent_artifact_links", ["user_id"])
    op.create_index("ix_agent_artifact_links_project_id", "agent_artifact_links", ["project_id"])
    op.create_index(
        "ix_agent_artifact_links_project_created",
        "agent_artifact_links",
        ["project_id", "created_at"],
    )

    op.create_table(
        "quote_records",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.String(length=120), nullable=False),
        sa.Column("project_id", sa.String(length=160), nullable=False),
        sa.Column("workflow_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(length=64), nullable=False),
        sa.Column("pricing_source", sa.String(length=120), nullable=False),
        sa.Column("currency", sa.String(length=16), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("material_cost", sa.Numeric(14, 2), nullable=False),
        sa.Column("process_cost", sa.Numeric(14, 2), nullable=False),
        sa.Column("labor_cost", sa.Numeric(14, 2), nullable=False),
        sa.Column("loss_rate", sa.Numeric(8, 4), nullable=False),
        sa.Column("overhead_rate", sa.Numeric(8, 4), nullable=False),
        sa.Column("margin_rate", sa.Numeric(8, 4), nullable=False),
        sa.Column("subtotal", sa.Numeric(14, 2), nullable=False),
        sa.Column("final_quote", sa.Numeric(14, 2), nullable=False),
        sa.Column("input_snapshot", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["workflow_id"], ["workflow_instances.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_quote_records_user_id", "quote_records", ["user_id"])
    op.create_index("ix_quote_records_project_id", "quote_records", ["project_id"])
    op.create_index("ix_quote_records_workflow_id", "quote_records", ["workflow_id"])
    op.create_index("ix_quote_records_status", "quote_records", ["status"])
    op.create_index("ix_quote_records_project_created", "quote_records", ["project_id", "created_at"])

    op.create_table(
        "quote_line_items",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("quote_id", sa.Uuid(), nullable=True),
        sa.Column("user_id", sa.String(length=120), nullable=False),
        sa.Column("project_id", sa.String(length=160), nullable=False),
        sa.Column("workflow_id", sa.Uuid(), nullable=False),
        sa.Column("category", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("unit", sa.String(length=32), nullable=False),
        sa.Column("quantity", sa.Numeric(14, 4), nullable=False),
        sa.Column("unit_price", sa.Numeric(14, 4), nullable=False),
        sa.Column("total_price", sa.Numeric(14, 2), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("item_metadata", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["quote_id"], ["quote_records.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_quote_line_items_quote_id", "quote_line_items", ["quote_id"])
    op.create_index("ix_quote_line_items_user_id", "quote_line_items", ["user_id"])
    op.create_index("ix_quote_line_items_project_id", "quote_line_items", ["project_id"])
    op.create_index("ix_quote_line_items_workflow_id", "quote_line_items", ["workflow_id"])
    op.create_index("ix_quote_line_items_project_quote", "quote_line_items", ["project_id", "quote_id"])


def downgrade() -> None:
    op.drop_table("quote_line_items")
    op.drop_table("quote_records")
    op.drop_table("agent_artifact_links")
    op.drop_table("agent_run_events")
    op.drop_table("agent_runs")
    op.drop_table("workflow_instances")
