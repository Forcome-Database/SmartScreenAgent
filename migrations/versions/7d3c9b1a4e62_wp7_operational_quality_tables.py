"""wp7 operational quality tables

Revision ID: 7d3c9b1a4e62
Revises: 25954dc70368
Create Date: 2026-07-23 15:16:24.084699

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "7d3c9b1a4e62"
down_revision: str | Sequence[str] | None = "25954dc70368"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "operations_reconciliation_state",
        sa.Column("key", sa.String(length=32), nullable=False),
        sa.Column("next_period_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("key"),
    )
    op.add_column(
        "scores",
        sa.Column(
            "llm_judge_call_group_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_scores_llm_judge_call_group_id",
        "scores",
        ["llm_judge_call_group_id"],
        unique=False,
    )
    op.create_table(
        "llm_usage_attempts",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("call_group_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("trace_id", sa.String(length=64), nullable=True),
        sa.Column("ingestion_job_id", sa.BigInteger(), nullable=True),
        sa.Column("score_id", sa.BigInteger(), nullable=True),
        sa.Column("jd_id", sa.BigInteger(), nullable=True),
        sa.Column("rule_version_id", sa.BigInteger(), nullable=True),
        sa.Column("operation", sa.String(length=32), nullable=False),
        sa.Column("attempt_role", sa.String(length=16), nullable=False),
        sa.Column("requested_model", sa.String(length=128), nullable=False),
        sa.Column("actual_model", sa.String(length=128), nullable=True),
        sa.Column("prompt_version", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        sa.Column(
            "input_price_cny_per_million",
            sa.Numeric(precision=18, scale=6),
            nullable=False,
        ),
        sa.Column(
            "output_price_cny_per_million",
            sa.Numeric(precision=18, scale=6),
            nullable=False,
        ),
        sa.Column("estimated_cost_cny", sa.Numeric(precision=24, scale=12), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "operation IN ('extract', 'judge', 'cross_check', 'lightweight')",
            name="ck_llm_usage_operation",
        ),
        sa.CheckConstraint(
            "attempt_role IN ('primary', 'fallback', 'secondary')",
            name="ck_llm_usage_attempt_role",
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'succeeded', 'unavailable', 'invalid_response', "
            "'configuration_error', 'abandoned')",
            name="ck_llm_usage_status",
        ),
        sa.CheckConstraint(
            "(status = 'pending') = (finished_at IS NULL)",
            name="ck_llm_usage_terminal_time",
        ),
        sa.CheckConstraint(
            "input_tokens IS NULL OR input_tokens >= 0",
            name="ck_llm_usage_input_tokens",
        ),
        sa.CheckConstraint(
            "output_tokens IS NULL OR output_tokens >= 0",
            name="ck_llm_usage_output_tokens",
        ),
        sa.CheckConstraint(
            "input_price_cny_per_million >= 0 AND output_price_cny_per_million >= 0",
            name="ck_llm_usage_prices",
        ),
        sa.CheckConstraint(
            "estimated_cost_cny IS NULL OR estimated_cost_cny >= 0",
            name="ck_llm_usage_cost",
        ),
        sa.CheckConstraint(
            "latency_ms IS NULL OR latency_ms >= 0",
            name="ck_llm_usage_latency",
        ),
        sa.ForeignKeyConstraint(["ingestion_job_id"], ["ingestion_jobs.id"]),
        sa.ForeignKeyConstraint(["score_id"], ["scores.id"]),
        sa.ForeignKeyConstraint(["jd_id"], ["jds.id"]),
        sa.ForeignKeyConstraint(["rule_version_id"], ["rule_versions.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    for index_name, column_name in (
        ("ix_llm_usage_attempts_call_group_id", "call_group_id"),
        ("ix_llm_usage_attempts_trace_id", "trace_id"),
        ("ix_llm_usage_attempts_ingestion_job_id", "ingestion_job_id"),
        ("ix_llm_usage_attempts_score_id", "score_id"),
        ("ix_llm_usage_attempts_jd_id", "jd_id"),
        ("ix_llm_usage_attempts_rule_version_id", "rule_version_id"),
    ):
        op.create_index(index_name, "llm_usage_attempts", [column_name], unique=False)
    op.create_index(
        "ix_llm_usage_started_id", "llm_usage_attempts", ["started_at", "id"], unique=False
    )
    op.create_index(
        "ix_llm_usage_status_started",
        "llm_usage_attempts",
        ["status", "started_at"],
        unique=False,
    )
    op.create_index(
        "ix_llm_usage_jd_rule_started",
        "llm_usage_attempts",
        ["jd_id", "rule_version_id", "started_at"],
        unique=False,
    )
    op.create_index(
        "ix_llm_usage_operation_started",
        "llm_usage_attempts",
        ["operation", "started_at"],
        unique=False,
    )
    op.create_table(
        "score_cross_checks",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("score_id", sa.BigInteger(), nullable=False),
        sa.Column("secondary_model", sa.String(length=128), nullable=False),
        sa.Column("prompt_version", sa.String(length=64), nullable=False),
        sa.Column("sample_reasons", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("state", sa.String(length=32), nullable=False),
        sa.Column("attempts", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lease_token", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("last_error_code", sa.String(length=64), nullable=True),
        sa.Column("secondary_total_score", sa.Numeric(precision=6, scale=2), nullable=True),
        sa.Column(
            "secondary_dimensions",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column("absolute_diff", sa.Numeric(precision=6, scale=2), nullable=True),
        sa.Column("threshold_snapshot", sa.Numeric(precision=6, scale=2), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.CheckConstraint(
            "state IN ('queued', 'running', 'completed', 'retryable_failed', "
            "'terminal_failed')",
            name="ck_cross_checks_state",
        ),
        sa.CheckConstraint("attempts >= 0", name="ck_cross_checks_attempts"),
        sa.ForeignKeyConstraint(["score_id"], ["scores.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "score_id",
            "secondary_model",
            "prompt_version",
            name="uq_cross_checks_score_model_prompt",
        ),
    )
    op.create_index(
        "ix_cross_checks_state_lease",
        "score_cross_checks",
        ["state", "lease_expires_at"],
        unique=False,
    )
    op.create_index(
        "ix_cross_checks_score_id_id",
        "score_cross_checks",
        ["score_id", "id"],
        unique=False,
    )
    op.create_index(
        "ix_cross_checks_completed_diff",
        "score_cross_checks",
        ["completed_at", "absolute_diff"],
        unique=False,
    )
    op.create_table(
        "golden_set_snapshots",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("content_sha256", sa.String(length=64), nullable=False),
        sa.Column("item_count", sa.Integer(), nullable=False),
        sa.Column("created_by_user_id", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("item_count >= 0", name="ck_golden_snapshot_item_count"),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "content_sha256", name="uq_golden_snapshots_content_sha256"
        ),
    )
    op.create_table(
        "golden_set_snapshot_entries",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("snapshot_id", sa.BigInteger(), nullable=False),
        sa.Column("candidate_id", sa.BigInteger(), nullable=False),
        sa.Column("jd_id", sa.BigInteger(), nullable=False),
        sa.Column("label", sa.String(length=32), nullable=False),
        sa.CheckConstraint(
            "label IN ('advance', 'reject', 'borderline')",
            name="ck_golden_snapshot_label",
        ),
        sa.ForeignKeyConstraint(
            ["snapshot_id"], ["golden_set_snapshots.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["candidate_id"], ["candidates.id"]),
        sa.ForeignKeyConstraint(["jd_id"], ["jds.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "snapshot_id",
            "candidate_id",
            "jd_id",
            name="uq_golden_snapshot_candidate_jd",
        ),
    )
    op.create_table(
        "quality_releases",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("golden_snapshot_id", sa.BigInteger(), nullable=False),
        sa.Column("window_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("window_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("metrics_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("targets_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_by_user_id", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('meets_target', 'below_target')",
            name="ck_quality_release_status",
        ),
        sa.ForeignKeyConstraint(["golden_snapshot_id"], ["golden_set_snapshots.id"]),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_quality_release_created_id",
        "quality_releases",
        ["created_at", "id"],
        unique=False,
    )
    op.create_table(
        "quality_release_jds",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("quality_release_id", sa.BigInteger(), nullable=False),
        sa.Column("jd_id", sa.BigInteger(), nullable=False),
        sa.Column("rule_version_id", sa.BigInteger(), nullable=False),
        sa.Column("metrics_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.ForeignKeyConstraint(
            ["quality_release_id"], ["quality_releases.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["jd_id"], ["jds.id"]),
        sa.ForeignKeyConstraint(["rule_version_id"], ["rule_versions.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "quality_release_id", "jd_id", name="uq_quality_release_jd"
        ),
    )
    op.create_index(
        "ix_quality_release_jds_jd_release",
        "quality_release_jds",
        ["jd_id", "quality_release_id"],
        unique=False,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("quality_release_jds")
    op.drop_table("quality_releases")
    op.drop_table("golden_set_snapshot_entries")
    op.drop_table("golden_set_snapshots")
    op.drop_table("score_cross_checks")
    op.drop_table("llm_usage_attempts")
    op.drop_index("ix_scores_llm_judge_call_group_id", table_name="scores")
    op.drop_column("scores", "llm_judge_call_group_id")
    op.drop_table("operations_reconciliation_state")
