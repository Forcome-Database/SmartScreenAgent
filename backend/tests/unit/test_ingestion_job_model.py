from backend.app.models import IngestionJob, Score


def test_ingestion_job_columns_exist():
    cols = set(IngestionJob.__table__.columns.keys())
    assert {
        "id", "batch_id", "state", "source", "jd_code",
        "raw_file_key", "raw_file_sha256", "candidate_id", "score_id",
        "attempts", "last_error_code", "lease_expires_at", "trace_id", "actor",
    } <= cols


def test_scores_have_unique_business_constraint():
    names = {c.name for c in Score.__table__.constraints}
    assert "uq_scores_candidate_jd_rule" in names
