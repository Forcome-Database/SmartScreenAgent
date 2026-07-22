# backend/tests/unit/test_feedback_service.py
from backend.app.services.feedback import FeedbackReasonRequired, derive_ai_agreed


def test_derive_ai_agreed_quadrants():
    # AI advance = grade != "rejected"
    assert derive_ai_agreed("L4", "advance") is True
    assert derive_ai_agreed("L4", "reject") is False
    assert derive_ai_agreed("rejected", "reject") is True
    assert derive_ai_agreed("rejected", "advance") is False


def test_hold_is_none():
    assert derive_ai_agreed("L4", "hold") is None
    assert derive_ai_agreed("rejected", "hold") is None


def test_reason_required_symbol_exists():
    assert issubclass(FeedbackReasonRequired, Exception)
