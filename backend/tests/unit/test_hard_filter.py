import json
from pathlib import Path

from backend.app.rules.schema import RuleSchema
from backend.app.scoring.hard_filter import run_hard_filters

FIXTURE = Path(__file__).parents[1] / "fixtures" / "sample_rule_v1.json"
RULE = RuleSchema.model_validate(json.loads(FIXTURE.read_text(encoding="utf-8")))


def test_age_over_45_rejected():
    result = run_hard_filters(
        candidate={"age": 46, "education": "本科"},
        filters=RULE.hard_filters,
    )
    assert result.rejected
    assert result.failed_filter_ids == ["age_max"]
    assert result.audit_entries[0]["audit_tag"] == "AGE"


def test_age_under_45_passes():
    result = run_hard_filters(
        candidate={"age": 30, "education": "本科"},
        filters=RULE.hard_filters,
    )
    assert not result.rejected
    assert result.failed_filter_ids == []


def test_missing_age_treated_as_unknown_not_rejected():
    result = run_hard_filters(
        candidate={"age": None, "education": "本科"},
        filters=RULE.hard_filters,
    )
    assert not result.rejected
    assert result.unknown_filter_ids == ["age_max"]
