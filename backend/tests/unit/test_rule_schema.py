import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from backend.app.rules.schema import RuleSchema

FIXTURE = Path(__file__).parents[1] / "fixtures" / "sample_rule_v1.json"


def test_loads_sample_rule_v1():
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
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
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    data["rule_dimensions"][0]["method"] = "bogus"
    with pytest.raises(ValidationError):
        RuleSchema.model_validate(data)
