from decimal import Decimal
from typing import get_type_hints

from sqlalchemy.orm import Mapped

from backend.app.models import (
    JD,
    AuditLog,
    Base,
    Candidate,
    CandidateEmbedding,
    Feedback,
    GoldenSet,
    GoldenSetSnapshot,
    GoldenSetSnapshotEntry,
    LLMUsageAttempt,
    OperationsReconciliationState,
    QualityRelease,
    QualityReleaseJD,
    RuleVersion,
    Score,
    ScoreCrossCheck,
    User,
)


def test_user_model_has_required_columns():
    table = User.__table__
    cols = {c.name for c in table.columns}
    assert {"id", "dingtalk_userid", "display_name", "role", "created_at", "last_login_at"} <= cols


def test_base_registers_user():
    assert "users" in Base.metadata.tables


def test_jd_columns():
    cols = {c.name for c in JD.__table__.columns}
    assert {"id", "code", "name", "description", "status", "active_rule_version_id"} <= cols


def test_rule_version_columns():
    cols = {c.name for c in RuleVersion.__table__.columns}
    assert {
        "id",
        "jd_id",
        "version",
        "schema_json",
        "published_at",
        "published_by_user_id",
        "notes",
        "golden_set_metrics",
    } <= cols


def test_candidate_columns():
    cols = {c.name for c in Candidate.__table__.columns}
    expected = {
        "id",
        "source",
        "source_external_id",
        "name_cipher",
        "phone_cipher",
        "email_cipher",
        "raw_file_key",
        "raw_file_sha256",
        "raw_file_size_bytes",
        "raw_file_content_type",
        "raw_file_original_name_cipher",
        "parsed_markdown",
        "extracted_json",
        "pii_hash",
    }
    assert expected <= cols


def test_score_columns():
    cols = {c.name for c in Score.__table__.columns}
    assert {
        "id",
        "candidate_id",
        "jd_id",
        "rule_version_id",
        "total_score",
        "grade",
        "hard_filter_result",
        "rule_dimensions",
        "judge_dimensions",
        "cross_engine_diff",
        "is_suspicious",
        "llm_model_main",
        "llm_model_extract",
        "cost_tokens",
        "cost_cny",
    } <= cols


def test_feedback_columns():
    cols = {c.name for c in Feedback.__table__.columns}
    assert {"id", "score_id", "reviewer_user_id", "decision", "reason", "ai_agreed"} <= cols


def test_golden_set_columns():
    cols = {c.name for c in GoldenSet.__table__.columns}
    assert {"id", "candidate_id", "jd_id", "label", "imported_at", "imported_by_user_id"} <= cols


def test_audit_log_columns():
    cols = {c.name for c in AuditLog.__table__.columns}
    assert {
        "id",
        "event_type",
        "actor",
        "target_type",
        "target_id",
        "payload",
        "rule_version_id",
        "created_at",
    } <= cols


def test_candidate_embedding_columns():
    cols = {c.name for c in CandidateEmbedding.__table__.columns}
    assert {"candidate_id", "embedding", "model_name", "created_at"} <= cols


def test_llm_usage_attempt_columns():
    cols = {c.name for c in LLMUsageAttempt.__table__.columns}
    assert {
        "call_group_id",
        "trace_id",
        "ingestion_job_id",
        "score_id",
        "jd_id",
        "rule_version_id",
        "operation",
        "attempt_role",
        "requested_model",
        "actual_model",
        "prompt_version",
        "status",
        "input_tokens",
        "output_tokens",
        "input_price_cny_per_million",
        "output_price_cny_per_million",
        "estimated_cost_cny",
        "latency_ms",
        "error_code",
        "started_at",
        "finished_at",
    } <= cols


def test_operations_reconciliation_state_columns():
    cols = {c.name for c in OperationsReconciliationState.__table__.columns}
    assert {"key", "next_period_start", "updated_at"} <= cols


def test_score_cross_check_columns():
    cols = {c.name for c in ScoreCrossCheck.__table__.columns}
    assert {
        "id",
        "score_id",
        "secondary_model",
        "prompt_version",
        "sample_reasons",
        "state",
        "attempts",
        "lease_expires_at",
        "lease_token",
        "last_error_code",
        "secondary_total_score",
        "secondary_dimensions",
        "absolute_diff",
        "threshold_snapshot",
        "completed_at",
    } <= cols


def test_golden_set_snapshot_columns_and_entry_foreign_keys():
    snapshot_cols = {c.name for c in GoldenSetSnapshot.__table__.columns}
    assert {"id", "content_sha256", "item_count", "created_by_user_id", "created_at"} <= (
        snapshot_cols
    )
    entry_fks = {fk.target_fullname for fk in GoldenSetSnapshotEntry.__table__.foreign_keys}
    assert {
        "golden_set_snapshots.id",
        "candidates.id",
        "jds.id",
    } <= entry_fks
    entry_constraint_names = {
        constraint.name for constraint in GoldenSetSnapshotEntry.__table__.constraints
    }
    assert "ck_golden_snapshot_label" in entry_constraint_names


def test_quality_release_columns_and_jd_foreign_keys():
    release_cols = {c.name for c in QualityRelease.__table__.columns}
    assert {
        "id",
        "golden_snapshot_id",
        "window_start",
        "window_end",
        "status",
        "metrics_json",
        "targets_json",
        "created_by_user_id",
        "created_at",
    } <= release_cols
    release_jd_fks = {fk.target_fullname for fk in QualityReleaseJD.__table__.foreign_keys}
    assert {"quality_releases.id", "jds.id", "rule_versions.id"} <= release_jd_fks


def test_score_has_llm_judge_call_group_id():
    assert "llm_judge_call_group_id" in Score.__table__.columns


def test_llm_usage_numeric_annotations_use_decimal():
    hints = get_type_hints(LLMUsageAttempt)
    assert hints["input_price_cny_per_million"] == Mapped[Decimal]
    assert hints["output_price_cny_per_million"] == Mapped[Decimal]
    assert hints["estimated_cost_cny"] == Mapped[Decimal | None]


def test_cross_check_numeric_annotations_use_decimal():
    hints = get_type_hints(ScoreCrossCheck)
    assert hints["secondary_total_score"] == Mapped[Decimal | None]
    assert hints["absolute_diff"] == Mapped[Decimal | None]
    assert hints["threshold_snapshot"] == Mapped[Decimal]
