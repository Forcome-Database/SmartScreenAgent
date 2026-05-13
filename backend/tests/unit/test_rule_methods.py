def test_dispatcher_raises_when_method_not_registered(monkeypatch):
    """合法 method 字段但运行时没注册函数（例如忘记 import 子模块）→ 显式报错而非静默跳过."""
    import pytest

    from backend.app.rules.schema import RuleDimension
    from backend.app.scoring import rule_engine

    monkeypatch.setattr(rule_engine, "METHODS", {})
    dim = RuleDimension(
        id="x", name="x", weight=1, method="lookup", table={"a": 1}
    )
    with pytest.raises(NotImplementedError, match="lookup"):
        rule_engine.score_dimensions({"education": "a"}, [dim])
