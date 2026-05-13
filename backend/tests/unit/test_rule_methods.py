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


def test_dispatcher_calls_lookup_method():
    from backend.app.rules.schema import RuleDimension
    from backend.app.scoring.rule_engine import score_dimensions
    dims = [
        RuleDimension(
            id="edu",
            name="学历",
            weight=12,
            method="lookup",
            table={"本科": 12, "大专": 6},
        )
    ]
    candidate = {"education": "本科", "experiences": []}
    results = score_dimensions(candidate, dims)
    assert results[0]["id"] == "edu"
    assert results[0]["score"] == 12
    assert results[0]["tier"] == "high"


def test_lookup_education_bachelor():
    from backend.app.rules.schema import RuleDimension
    from backend.app.scoring.rule_engine import score_dimensions
    dims = [RuleDimension(id="e", name="学历", weight=12, method="lookup", table={"本科": 12, "大专": 6})]
    out = score_dimensions({"education": "大专"}, dims)
    assert out[0]["score"] == 6
    assert out[0]["tier"] == "high"


def test_lookup_missing_education():
    from backend.app.rules.schema import RuleDimension
    from backend.app.scoring.rule_engine import score_dimensions
    dims = [RuleDimension(id="e", name="学历", weight=12, method="lookup", table={"本科": 12})]
    out = score_dimensions({"education": None}, dims)
    assert out[0]["score"] == 0
    assert out[0]["tier"] == "low"
