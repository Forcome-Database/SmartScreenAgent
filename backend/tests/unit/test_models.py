from backend.app.models import Base, User


def test_user_model_has_required_columns():
    table = User.__table__
    cols = {c.name for c in table.columns}
    assert {"id", "dingtalk_userid", "display_name", "role", "created_at", "last_login_at"} <= cols


def test_base_registers_user():
    assert "users" in Base.metadata.tables
