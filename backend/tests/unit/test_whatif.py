import pytest
from sqlalchemy.exc import IntegrityError

from backend.app.models import JD
from backend.app.rules.schema import RuleSchema
from backend.app.services.rule_publication import (
    VersionExists,
    bucket,
    create_draft,
    whatif_grade,
)

_SCHEMA = {
    "version": "v2",
    "jd_code": "FT",
    "total_score": 10.0,
    "passing_threshold": 6.0,
    "hard_filters": [
        {
            "id": "age",
            "rule": "age <= 40",
            "action": "reject",
            "audit_tag": "age",
        }
    ],
    "rule_dimensions": [
        {
            "id": "exp",
            "name": "experience",
            "weight": 4.0,
            "method": "experience_years",
            "tiers": [
                {"label": "high", "score": 4.0, "min_years": 3.0},
                {"label": "low", "score": 1.0, "min_years": 0.0},
            ],
        }
    ],
    "judge_dimensions": [
        {
            "id": "fit",
            "name": "fit",
            "weight": 6.0,
            "prompt_hint": "fit",
            "tiers": [{"label": "high", "score": 6.0}],
        }
    ],
    "grade_thresholds": [{"grade": "L1", "min": 6.0, "label": "pass"}],
}


def _schema() -> RuleSchema:
    return RuleSchema.model_validate(_SCHEMA)


def _experienced_candidate(*, age: int) -> dict:
    return {
        "age": age,
        "experiences": [{"start": "2019-01", "end": "2024-01"}],
    }


def test_hard_reject_grade() -> None:
    assert (
        whatif_grade(
            _schema(),
            _experienced_candidate(age=50),
            stored_rule_subtotal=0,
            stored_total=0,
            stored_hard_rejected=False,
        )
        == "rejected"
    )


def test_deterministic_rescore_reuses_judge_subtotal() -> None:
    # Stored total 9 - stored rule subtotal 3 = reused judge subtotal 6.
    grade = whatif_grade(
        _schema(),
        _experienced_candidate(age=30),
        stored_rule_subtotal=3.0,
        stored_total=9.0,
        stored_hard_rejected=False,
    )

    assert grade == "L1"


def test_indeterminate_when_stored_hard_rejected_and_draft_does_not_reject() -> None:
    assert (
        whatif_grade(
            _schema(),
            _experienced_candidate(age=30),
            stored_rule_subtotal=0,
            stored_total=0,
            stored_hard_rejected=True,
        )
        is None
    )


def test_bucket_quadrants() -> None:
    assert bucket("advance", "L1") == "tp"
    assert bucket("advance", "rejected") == "fn"
    assert bucket("reject", "L1") == "fp"
    assert bucket("reject", "rejected") == "tn"


class _EmptyResult:
    def scalar_one_or_none(self) -> None:
        return None


class _ConflictingSession:
    rolled_back = False

    async def execute(self, _statement):
        return _EmptyResult()

    def add(self, _value) -> None:
        return None

    async def commit(self) -> None:
        raise IntegrityError(
            "INSERT",
            {},
            Exception("duplicate key violates uq_rule_versions_jd_version"),
        )

    async def rollback(self) -> None:
        self.rolled_back = True


async def test_create_draft_maps_unique_constraint_race_to_version_exists() -> None:
    session = _ConflictingSession()
    jd = JD(id=1, code="FT", name="Foreign Trade", status="active")

    with pytest.raises(VersionExists):
        await create_draft(
            session,  # type: ignore[arg-type]
            jd=jd,
            schema_json=_SCHEMA,
            notes=None,
        )

    assert session.rolled_back is True
