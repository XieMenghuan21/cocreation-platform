"""Create database-only persistence tables.

Revision ID: 20260723_0001
Revises:
Create Date: 2026-07-23
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "20260723_0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

sqlite_compatible_bigint = sa.BigInteger().with_variant(sa.Integer(), "sqlite")


def upgrade() -> None:
    op.create_table(
        "cocreation_project_histories",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.String(length=120), nullable=False),
        sa.Column("project_id", sa.String(length=160), nullable=False),
        sa.Column("project_name", sa.String(length=255), nullable=False),
        sa.Column("industry", sa.String(length=120), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("input_mode", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_task_id", sa.String(length=160), nullable=True),
        sa.Column("last_status", sa.String(length=120), nullable=True),
        sa.Column("last_result_text", sa.Text(), nullable=True),
        sa.Column("last_image_url", sa.Text(), nullable=True),
        sa.Column("project_data", sa.JSON(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "user_id",
            "project_id",
            name="uq_cocreation_project_histories_user_project",
        ),
    )
    op.create_index(
        "ix_cocreation_project_histories_project_id",
        "cocreation_project_histories",
        ["project_id"],
    )
    op.create_index(
        "ix_cocreation_project_histories_user_id",
        "cocreation_project_histories",
        ["user_id"],
    )

    op.create_table(
        "user_sessions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.String(length=120), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("client_metadata", sa.JSON(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_user_sessions_expires_at", "user_sessions", ["expires_at"])
    op.create_index(
        "ix_user_sessions_token_hash",
        "user_sessions",
        ["token_hash"],
        unique=True,
    )
    op.create_index("ix_user_sessions_user_id", "user_sessions", ["user_id"])

    op.create_table(
        "sso_authorization_states",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("state_hash", sa.String(length=64), nullable=False),
        sa.Column("browser_binding_hash", sa.String(length=64), nullable=False),
        sa.Column("request_ip_hash", sa.String(length=64), nullable=False),
        sa.Column("redirect_uri", sa.String(length=2048), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_sso_authorization_states_state_hash",
        "sso_authorization_states",
        ["state_hash"],
        unique=True,
    )
    op.create_index(
        "ix_sso_authorization_states_browser_binding_hash",
        "sso_authorization_states",
        ["browser_binding_hash"],
    )
    op.create_index(
        "ix_sso_authorization_states_request_ip_hash",
        "sso_authorization_states",
        ["request_ip_hash"],
    )
    op.create_index(
        "ix_sso_authorization_states_expires_at",
        "sso_authorization_states",
        ["expires_at"],
    )

    op.create_table(
        "workflow_tasks",
        sa.Column("id", sa.String(length=160), nullable=False),
        sa.Column("user_id", sa.String(length=120), nullable=False),
        sa.Column("project_id", sa.String(length=160), nullable=True),
        sa.Column("version_id", sa.String(length=160), nullable=True),
        sa.Column("status", sa.String(length=64), nullable=False),
        sa.Column("progress", sa.Integer(), nullable=False),
        sa.Column("current_step", sa.String(length=120), nullable=False),
        sa.Column("input_payload", sa.JSON(), nullable=False),
        sa.Column("design_spec", sa.JSON(), nullable=False),
        sa.Column("outputs", sa.JSON(), nullable=False),
        sa.Column("diagnostics", sa.JSON(), nullable=False),
        sa.Column("error_code", sa.String(length=120), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("recoverable", sa.Boolean(), nullable=False),
        sa.Column("history_persisted", sa.Boolean(), nullable=False),
        sa.Column("lease_owner", sa.String(length=160), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_workflow_tasks_status", "workflow_tasks", ["status"])
    op.create_index(
        "ix_workflow_tasks_history_persisted",
        "workflow_tasks",
        ["history_persisted"],
    )
    op.create_index("ix_workflow_tasks_user_id", "workflow_tasks", ["user_id"])

    op.create_table(
        "assets",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.String(length=120), nullable=False),
        sa.Column("project_id", sa.String(length=160), nullable=True),
        sa.Column("version_id", sa.String(length=160), nullable=True),
        sa.Column("task_id", sa.String(length=160), nullable=True),
        sa.Column("kind", sa.String(length=64), nullable=False),
        sa.Column("filename", sa.String(length=255), nullable=False),
        sa.Column("extension", sa.String(length=32), nullable=True),
        sa.Column("content_type", sa.String(length=160), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("chunk_size", sa.Integer(), nullable=False),
        sa.Column("chunk_count", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=64), nullable=False),
        sa.Column("source", sa.String(length=64), nullable=False),
        sa.Column("metadata", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["task_id"],
            ["workflow_tasks.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_assets_sha256", "assets", ["sha256"])
    op.create_index("ix_assets_status", "assets", ["status"])
    op.create_index("ix_assets_task_id", "assets", ["task_id"])
    op.create_index(
        "ix_assets_user_created_id",
        "assets",
        ["user_id", "created_at", "id"],
    )

    op.create_table(
        "workspace_states",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.String(length=120), nullable=False),
        sa.Column("selected_project_id", sa.String(length=160), nullable=True),
        sa.Column(
            "selected_reference_version_id",
            sa.String(length=160),
            nullable=True,
        ),
        sa.Column("selected_reference_asset_id", sa.Uuid(), nullable=True),
        sa.Column("active_scenario", sa.String(length=120), nullable=False),
        sa.Column("active_workflow_stage", sa.String(length=120), nullable=False),
        sa.Column("view_mode", sa.String(length=64), nullable=False),
        sa.Column("scene_mode", sa.String(length=64), nullable=False),
        sa.Column("selected_industry", sa.String(length=120), nullable=False),
        sa.Column("generation_prompt", sa.Text(), nullable=False),
        sa.Column("active_step_index", sa.Integer(), nullable=False),
        sa.Column("state_data", sa.JSON(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["selected_reference_asset_id"],
            ["assets.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_workspace_states_selected_reference_asset_id",
        "workspace_states",
        ["selected_reference_asset_id"],
    )
    op.create_index(
        "ix_workspace_states_user_id",
        "workspace_states",
        ["user_id"],
        unique=True,
    )

    op.create_table(
        "cocreation_project_version_histories",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("project_history_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.String(length=120), nullable=False),
        sa.Column("version_id", sa.String(length=120), nullable=False),
        sa.Column("label", sa.String(length=255), nullable=False),
        sa.Column("status", sa.String(length=120), nullable=False),
        sa.Column("note", sa.Text(), nullable=False),
        sa.Column("project_id", sa.String(length=160), nullable=True),
        sa.Column("project_name", sa.String(length=255), nullable=True),
        sa.Column("version_number", sa.Integer(), nullable=True),
        sa.Column("is_finalized", sa.Boolean(), nullable=False),
        sa.Column("source_project_id", sa.String(length=160), nullable=True),
        sa.Column("prompt", sa.Text(), nullable=True),
        sa.Column("optimized_prompt", sa.Text(), nullable=True),
        sa.Column("result_text", sa.Text(), nullable=True),
        sa.Column("preview_image_url", sa.Text(), nullable=True),
        sa.Column("generated_image_urls", sa.JSON(), nullable=False),
        sa.Column("change_type", sa.String(length=160), nullable=True),
        sa.Column("source_object", sa.String(length=255), nullable=True),
        sa.Column("task_id", sa.String(length=160), nullable=True),
        sa.Column("script_path", sa.Text(), nullable=True),
        sa.Column("work_dir", sa.Text(), nullable=True),
        sa.Column("output_path", sa.Text(), nullable=True),
        sa.Column("download_url", sa.Text(), nullable=True),
        sa.Column("execution_summary", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("cli_executed", sa.Boolean(), nullable=True),
        sa.Column("export_format", sa.String(length=64), nullable=True),
        sa.Column("model_objects", sa.JSON(), nullable=False),
        sa.Column("parameters", sa.JSON(), nullable=False),
        sa.Column("generated_assets", sa.JSON(), nullable=False),
        sa.Column("diagnostics", sa.JSON(), nullable=False),
        sa.Column("snapshot_data", sa.JSON(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["project_history_id"],
            ["cocreation_project_histories.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "user_id",
            "project_history_id",
            "version_id",
            name="uq_cocreation_project_version_histories_user_project_version",
        ),
    )
    op.create_index(
        "ix_cocreation_project_version_histories_project_history_id",
        "cocreation_project_version_histories",
        ["project_history_id"],
    )
    op.create_index(
        "ix_cocreation_project_version_histories_user_id",
        "cocreation_project_version_histories",
        ["user_id"],
    )

    op.create_table(
        "workflow_task_events",
        sa.Column("id", sqlite_compatible_bigint, autoincrement=True, nullable=False),
        sa.Column("task_id", sa.String(length=160), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=64), nullable=False),
        sa.Column("progress", sa.Integer(), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("event_data", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["task_id"],
            ["workflow_tasks.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "task_id",
            "sequence",
            name="uq_workflow_task_events_task_sequence",
        ),
    )
    op.create_table(
        "asset_blob_chunks",
        sa.Column("id", sqlite_compatible_bigint, autoincrement=True, nullable=False),
        sa.Column("asset_id", sa.Uuid(), nullable=False),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("content", sa.LargeBinary(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["asset_id"],
            ["assets.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "asset_id",
            "chunk_index",
            name="uq_asset_blob_chunks_asset_chunk",
        ),
    )

def downgrade() -> None:
    op.drop_table("asset_blob_chunks")
    op.drop_table("workflow_task_events")
    op.drop_table("cocreation_project_version_histories")
    op.drop_index(
        "ix_workspace_states_selected_reference_asset_id",
        table_name="workspace_states",
    )
    op.drop_table("workspace_states")
    op.drop_index("ix_assets_user_created_id", table_name="assets")
    op.drop_table("assets")
    op.drop_table("workflow_tasks")
    op.drop_table("sso_authorization_states")
    op.drop_table("user_sessions")
    op.drop_table("cocreation_project_histories")
