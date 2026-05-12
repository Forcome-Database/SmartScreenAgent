from backend.app.models import (
    JD,
    AuditLog,
    Base,
    Candidate,
    CandidateEmbedding,
    Feedback,
    GoldenSet,
    RuleVersion,
    Score,
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
