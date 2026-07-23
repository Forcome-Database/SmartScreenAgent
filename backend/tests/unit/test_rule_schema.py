import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from backend.app.rules.schema import JudgeDimension, RuleDimension, RuleSchema

FIXTURE = Path(__file__).parents[1] / "fixtures" / "sample_rule_v1.json"


def _sample_rule_data() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_loads_sample_rule_v1():
    data = _sample_rule_data()
    rule = RuleSchema.model_validate(data)
    assert rule.version == "v1"
    assert rule.jd_code == "FOREIGN_TRADE"
    assert rule.total_score == 100
    assert sum(d.weight for d in rule.rule_dimensions) + sum(
        d.weight for d in rule.judge_dimensions
    ) == 100


def test_rejects_weight_mismatch():
    data = {
        "version": "v1",
        "jd_code": "FOREIGN_TRADE",
        "total_score": 100,
        "passing_threshold": 40,
        "hard_filters": [],
        "rule_dimensions": [
            {
                "id": "x",
                "name": "x",
                "weight": 50,
                "method": "lookup",
                "table": {"a": 10},
            }
        ],
        "judge_dimensions": [],
        "grade_thresholds": [],
    }
    with pytest.raises(ValidationError):
        RuleSchema.model_validate(data)


def test_rejects_unknown_method():
    data = _sample_rule_data()
    data["rule_dimensions"][0]["method"] = "bogus"
    with pytest.raises(ValidationError):
        RuleSchema.model_validate(data)


@pytest.mark.parametrize("invalid_weight", [-1, float("inf"), float("nan"), True, False])
def test_rule_dimension_rejects_invalid_weight(invalid_weight):
    data = _sample_rule_data()["rule_dimensions"][0]
    data["weight"] = invalid_weight

    with pytest.raises(ValidationError):
        RuleDimension.model_validate(data)


@pytest.mark.parametrize("invalid_weight", [-1, float("inf"), float("nan"), True, False])
def test_judge_dimension_rejects_invalid_weight(invalid_weight):
    data = _sample_rule_data()["judge_dimensions"][0]
    data["weight"] = invalid_weight

    with pytest.raises(ValidationError):
        JudgeDimension.model_validate(data)


def test_zero_dimension_weight_remains_valid_when_total_is_recomputed():
    data = _sample_rule_data()
    previous_weight = data["rule_dimensions"][0]["weight"]
    data["rule_dimensions"][0]["weight"] = 0
    data["total_score"] -= previous_weight

    rule = RuleSchema.model_validate(data)

    assert rule.rule_dimensions[0].weight == 0
