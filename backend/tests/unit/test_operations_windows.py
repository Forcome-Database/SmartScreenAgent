from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from backend.app.services.operations.reporting import (
    MAX_USAGE_RANGE,
    InvalidOperationsWindow,
    InvalidUsageRange,
    UsageRangeTooLarge,
    resolve_window,
    validate_usage_range,
)

# 13:00 in Shanghai, so 13 hours have elapsed since local midnight.
NOW = datetime(2026, 7, 26, 5, 0, tzinfo=timezone.utc)
LOCAL_MIDNIGHT = datetime(2026, 7, 25, 16, 0, tzinfo=timezone.utc)


def test_today_compares_the_same_elapsed_duration_of_the_previous_local_day() -> None:
    window = resolve_window("today", NOW)

    assert (window.current_start, window.current_end) == (LOCAL_MIDNIGHT, NOW)
    # Previous local midnight, then the SAME 13 hours — not the whole prior day,
    # otherwise a partial day would always look cheaper than a complete one.
    assert window.previous_start == LOCAL_MIDNIGHT - timedelta(days=1)
    assert window.previous_end == window.previous_start + timedelta(hours=13)


@pytest.mark.parametrize(("name", "days"), [("7d", 7), ("30d", 30)])
def test_n_day_windows_compare_the_immediately_preceding_equal_interval(
    name: str, days: int
) -> None:
    window = resolve_window(name, NOW)

    assert window.current_start == NOW - timedelta(days=days)
    assert window.current_end == NOW
    assert window.previous_start == NOW - timedelta(days=days * 2)
    assert window.previous_end == window.current_start


@pytest.mark.parametrize("name", ["", "today ", "1d", "90d", "TODAY", "yesterday"])
def test_unsupported_window_names_are_rejected(name: str) -> None:
    with pytest.raises(InvalidOperationsWindow):
        resolve_window(name, NOW)


def test_window_rejects_a_naive_reference_time() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        resolve_window("7d", datetime(2026, 7, 26, 5, 0))


def test_usage_range_accepts_an_increasing_aware_range() -> None:
    validate_usage_range(NOW - timedelta(days=1), NOW)


@pytest.mark.parametrize(
    ("start", "end"),
    [
        (datetime(2026, 7, 25, 5, 0), NOW),
        (NOW - timedelta(days=1), datetime(2026, 7, 26, 5, 0)),
    ],
)
def test_naive_usage_bounds_are_rejected(start: datetime, end: datetime) -> None:
    with pytest.raises(InvalidUsageRange):
        validate_usage_range(start, end)


@pytest.mark.parametrize("end", [NOW, NOW - timedelta(seconds=1)])
def test_inverted_or_empty_usage_range_is_rejected(end: datetime) -> None:
    with pytest.raises(InvalidUsageRange):
        validate_usage_range(NOW, end)


def test_range_at_the_limit_is_allowed_but_beyond_it_is_not() -> None:
    validate_usage_range(NOW - MAX_USAGE_RANGE, NOW)

    with pytest.raises(UsageRangeTooLarge):
        validate_usage_range(
            NOW - MAX_USAGE_RANGE - timedelta(microseconds=1), NOW
        )
