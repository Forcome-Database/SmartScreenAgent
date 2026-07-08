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

    dims = [
        RuleDimension(
            id="e",
            name="学历",
            weight=12,
            method="lookup",
            table={"本科": 12, "大专": 6},
        )
    ]
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


def test_tiered_keyword_high_tier_hits():
    from backend.app.rules.schema import RuleDimension, Tier
    from backend.app.scoring.rule_engine import score_dimensions
    dims = [
        RuleDimension(
            id="na",
            name="北美市场",
            weight=30,
            method="tiered_keyword_match",
            tiers=[
                Tier(label="high", score=30, keywords=["北美 五金", "深耕北美"], min_years=2),
                Tier(label="mid", score=15, keywords=["北美 外贸"], min_years=1),
                Tier(label="low", score=0, keywords=[]),
            ],
        )
    ]
    candidate = {
        "experiences": [
            {
                "company": "Acme",
                "title": "外贸业务",
                "description": "深耕北美五金市场 5 年",
                "start": "2019-01",
                "end": "2024-01",
            }
        ]
    }
    out = score_dimensions(candidate, dims)
    assert out[0]["tier"] == "high"
    assert out[0]["score"] == 30


def test_tiered_keyword_falls_back_to_low_when_no_hits():
    from backend.app.rules.schema import RuleDimension, Tier
    from backend.app.scoring.rule_engine import score_dimensions
    dims = [
        RuleDimension(
            id="na",
            name="北美市场",
            weight=30,
            method="tiered_keyword_match",
            tiers=[
                Tier(label="high", score=30, keywords=["北美 五金"], min_years=2),
                Tier(label="mid", score=15, keywords=["北美 外贸"], min_years=1),
                Tier(label="low", score=0, keywords=[]),
            ],
        )
    ]
    candidate = {
        "experiences": [
            {
                "company": "X",
                "title": "Y",
                "description": "欧洲电子市场销售",
                "start": None,
                "end": None,
            }
        ]
    }
    out = score_dimensions(candidate, dims)
    assert out[0]["tier"] == "low"
    assert out[0]["score"] == 0


def test_experience_years_high_tier():
    from backend.app.rules.schema import RuleDimension, Tier
    from backend.app.scoring.rule_engine import score_dimensions
    dims = [
        RuleDimension(
            id="trade",
            name="外贸全流程",
            weight=25,
            method="experience_years",
            tiers=[
                Tier(
                    label="high",
                    score=25,
                    min_years=3,
                    required_keywords=["报关", "订舱", "单证"],
                ),
                Tier(label="mid", score=12, min_years=1),
                Tier(label="low", score=0),
            ],
        )
    ]
    candidate = {
        "experiences": [
            {
                "company": "X",
                "title": "外贸",
                "description": "全面负责报关、订舱、单证",
                "start": "2019-01",
                "end": "2024-01",
            }
        ]
    }
    out = score_dimensions(candidate, dims)
    assert out[0]["tier"] == "high"
    assert out[0]["score"] == 25


def test_experience_years_total_years_helper_handles_present():
    from backend.app.scoring.methods.experience_years import total_years_for_keywords
    candidate = {
        "experiences": [
            {"description": "北美五金", "start": "2022-05", "end": None},
        ]
    }
    y = total_years_for_keywords(candidate, ["北美五金"], today="2024-05-01")
    assert 1.9 < y < 2.1
