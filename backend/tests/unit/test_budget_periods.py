from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from backend.app.services.operations.budgets import (
    budget_state,
    local_periods,
    thresholds_crossed,
)

SHANGHAI_OFFSET = timedelta(hours=8)


def _utc(year: int, month: int, day: int, hour: int = 0, minute: int = 0) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=timezone.utc)


def _shanghai_midnight_as_utc(year: int, month: int, day: int) -> datetime:
    """Local Shanghai midnight expressed in timezone.utc (Shanghai is a fixed timezone.utc+8)."""
    return datetime(year, month, day, tzinfo=timezone.utc) - SHANGHAI_OFFSET


def test_local_periods_are_half_open_utc_ranges_around_shanghai_midnight() -> None:
    # Exactly Shanghai midnight on 2026-07-26 == 2026-07-25T16:00Z.
    daily, monthly = local_periods(_shanghai_midnight_as_utc(2026, 7, 26))

    assert (daily.scope, monthly.scope) == ("daily", "monthly")
    # Half-open: the instant of local midnight belongs to the NEW day.
    assert daily.start == _shanghai_midnight_as_utc(2026, 7, 26)
    assert daily.end == _shanghai_midnight_as_utc(2026, 7, 27)
    assert monthly.start == _shanghai_midnight_as_utc(2026, 7, 1)
    assert monthly.end == _shanghai_midnight_as_utc(2026, 8, 1)


def test_instant_before_local_midnight_belongs_to_previous_day() -> None:
    one_second_earlier = _shanghai_midnight_as_utc(2026, 7, 26) - timedelta(seconds=1)
    daily, _monthly = local_periods(one_second_earlier)

    assert daily.start == _shanghai_midnight_as_utc(2026, 7, 25)
    assert daily.end == _shanghai_midnight_as_utc(2026, 7, 26)


def test_month_rollover_crosses_into_the_new_calendar_month() -> None:
    daily, monthly = local_periods(_shanghai_midnight_as_utc(2026, 8, 1))

    assert daily.start == _shanghai_midnight_as_utc(2026, 8, 1)
    assert monthly.start == _shanghai_midnight_as_utc(2026, 8, 1)
    assert monthly.end == _shanghai_midnight_as_utc(2026, 9, 1)


def test_december_monthly_period_rolls_into_the_next_year() -> None:
    _daily, monthly = local_periods(_utc(2026, 12, 20, 5))

    assert monthly.start == _shanghai_midnight_as_utc(2026, 12, 1)
    assert monthly.end == _shanghai_midnight_as_utc(2027, 1, 1)


def test_utc_instant_midday_maps_to_the_local_day_that_contains_it() -> None:
    # 2026-07-25T20:00Z is 2026-07-26T04:00 in Shanghai.
    daily, _monthly = local_periods(_utc(2026, 7, 25, 20))

    assert daily.start == _shanghai_midnight_as_utc(2026, 7, 26)
    assert daily.end == _shanghai_midnight_as_utc(2026, 7, 27)


def test_naive_datetime_is_rejected() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        local_periods(datetime(2026, 7, 26, 0, 0))


@pytest.mark.parametrize(
    ("spend", "budget", "expected"),
    [
        # A zero budget with zero spend is normal, not a division-by-zero warning.
        (Decimal("0"), Decimal("0"), "normal"),
        (Decimal("0.000000000001"), Decimal("0"), "exceeded"),
        (Decimal("0"), Decimal("100"), "normal"),
        (Decimal("79.99"), Decimal("100"), "normal"),
        # Equality at the warn ratio is already a warning.
        (Decimal("80"), Decimal("100"), "warning"),
        (Decimal("99.99"), Decimal("100"), "warning"),
        # Equality at the budget is already exceeded.
        (Decimal("100"), Decimal("100"), "exceeded"),
        (Decimal("150"), Decimal("100"), "exceeded"),
    ],
)
def test_budget_state_boundaries(spend: Decimal, budget: Decimal, expected: str) -> None:
    assert budget_state(spend, budget, 0.8) == expected


@pytest.mark.parametrize(
    ("spend", "budget", "expected"),
    [
        (Decimal("0"), Decimal("100"), ()),
        (Decimal("80"), Decimal("100"), ("warning",)),
        # A direct normal-to-exceeded jump must emit BOTH thresholds, because
        # each dedupe key is evaluated independently.
        (Decimal("500"), Decimal("100"), ("warning", "exceeded")),
        (Decimal("0"), Decimal("0"), ()),
        (Decimal("1"), Decimal("0"), ("warning", "exceeded")),
    ],
)
def test_thresholds_crossed(
    spend: Decimal, budget: Decimal, expected: tuple[str, ...]
) -> None:
    assert thresholds_crossed(spend, budget, 0.8) == expected
