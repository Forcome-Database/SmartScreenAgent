from backend.app.models import Base, JD, RuleVersion, User


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
    assert {"id", "jd_id", "version", "schema_json", "published_at", "published_by_user_id",
            "notes", "golden_set_metrics"} <= cols


from backend.app.models import Candidate


def test_candidate_columns():
    cols = {c.name for c in Candidate.__table__.columns}
    expected = {"id", "source", "source_external_id", "name_cipher", "phone_cipher",
                "email_cipher", "raw_file_key", "parsed_markdown", "extracted_json", "pii_hash"}
    assert expected <= cols
