# backend/tests/unit/test_feedback_report.py
from backend.app.services.feedback import agreement_stats


def test_agreement_rate_excludes_hold_and_handles_zero():
    # (agreed, disagreed, hold)
    assert agreement_stats(3, 1, 2) == {
        "total": 6, "agreed": 3, "disagreed": 1, "hold": 2, "agreement_rate": 0.75,
    }
    assert agreement_stats(0, 0, 0)["agreement_rate"] is None
    assert agreement_stats(0, 0, 5)["agreement_rate"] is None
