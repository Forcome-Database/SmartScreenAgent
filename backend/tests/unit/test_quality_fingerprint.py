from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from backend.app.services.quality.releases import (
    DEFAULT_RELEASE_WINDOW,
    MAX_RELEASE_WINDOW,
    QUALITY_METRIC_SCHEMA_VERSION,
    GoldenRow,
    InvalidReleaseWindow,
    JDBinding,
    ReleaseWindowTooLarge,
    canonical_json,
    golden_content_hash,
    input_fingerprint,
    resolve_release_window,
    target_snapshot,
    targets_for_persistence,
)

NOW = datetime(2026, 7, 26, 5, 0, tzinfo=timezone.utc)
WINDOW_START = NOW - timedelta(days=30)


def _bindings() -> list[JDBinding]:
    return [
        JDBinding(jd_id=2, jd_code="LOGISTICS", rule_version_id=20),
        JDBinding(jd_id=1, jd_code="TRADE", rule_version_id=10),
    ]


def _rows() -> list[GoldenRow]:
    return [
        GoldenRow(jd_id=2, candidate_id=7, label="reject"),
        GoldenRow(jd_id=1, candidate_id=3, label="advance"),
        GoldenRow(jd_id=1, candidate_id=2, label="borderline"),
    ]


def _fingerprint(**overrides) -> str:
    payload = {
        "golden_hash": golden_content_hash(_rows()),
        "bindings": _bindings(),
        "window_start": WINDOW_START,
        "window_end": NOW,
        "targets": target_snapshot(),
    }
    payload.update(overrides)
    return input_fingerprint(**payload)


# --- canonical number tokens ------------------------------------------------


@pytest.mark.parametrize(
    ("value", "token"),
    [
        (Decimal("0.75"), "0.75"),
        (Decimal("0.80"), "0.8"),
        (Decimal("1.0"), "1"),
        (Decimal("1"), "1"),
        (Decimal("0"), "0"),
        (Decimal("-0"), "0"),
        (Decimal("-0.0"), "0"),
        (Decimal("0.950000"), "0.95"),
        # Fixed point, never exponent notation.
        (Decimal("1E-7"), "0.0000001"),
        (Decimal("1E+3"), "1000"),
    ],
)
def test_numbers_render_as_normalized_fixed_point_tokens(
    value: Decimal, token: str
) -> None:
    assert canonical_json(value) == token


@pytest.mark.parametrize("value", [Decimal("NaN"), Decimal("Infinity")])
def test_non_finite_numbers_are_refused(value: Decimal) -> None:
    with pytest.raises(ValueError):
        canonical_json(value)


def test_canonical_json_sorts_keys_and_stays_compact() -> None:
    rendered = canonical_json({"b": 1, "a": [1, 2], "c": {"z": None, "y": True}})

    assert rendered == '{"a":[1,2],"b":1,"c":{"y":true,"z":null}}'


# --- golden content hash ----------------------------------------------------


def test_golden_hash_ignores_input_ordering() -> None:
    assert golden_content_hash(_rows()) == golden_content_hash(list(reversed(_rows())))


@pytest.mark.parametrize(
    "mutated",
    [
        GoldenRow(jd_id=1, candidate_id=3, label="reject"),
        GoldenRow(jd_id=1, candidate_id=4, label="advance"),
        GoldenRow(jd_id=9, candidate_id=3, label="advance"),
    ],
)
def test_golden_hash_changes_with_any_row_field(mutated: GoldenRow) -> None:
    changed = [mutated, *(_rows()[:2])]

    assert golden_content_hash(changed) != golden_content_hash(_rows())


# --- window resolution ------------------------------------------------------


def test_window_defaults_to_the_preceding_thirty_days() -> None:
    start, end = resolve_release_window(None, None, NOW)

    assert end == NOW
    assert start == NOW - DEFAULT_RELEASE_WINDOW
    assert DEFAULT_RELEASE_WINDOW == timedelta(days=30)


@pytest.mark.parametrize(
    ("start", "end"),
    [
        (datetime(2026, 7, 1), NOW),
        (WINDOW_START, datetime(2026, 7, 26, 5, 0)),
        (NOW, WINDOW_START),
        (NOW, NOW),
        # An end in the future cannot describe observed history.
        (WINDOW_START, NOW + timedelta(seconds=1)),
    ],
)
def test_invalid_windows_are_refused(start: datetime, end: datetime) -> None:
    with pytest.raises(InvalidReleaseWindow):
        resolve_release_window(start, end, NOW)


def test_window_at_the_maximum_is_allowed_but_beyond_it_is_not() -> None:
    resolve_release_window(NOW - MAX_RELEASE_WINDOW, NOW, NOW)

    with pytest.raises(ReleaseWindowTooLarge):
        resolve_release_window(
            NOW - MAX_RELEASE_WINDOW - timedelta(microseconds=1), NOW, NOW
        )


# --- target snapshot --------------------------------------------------------


def test_target_snapshot_carries_the_exact_immutable_fields() -> None:
    assert set(target_snapshot()) == {
        "metric_schema_version",
        "f1_target",
        "evidence_coverage_target",
        "confidence_bin_boundaries",
        "confidence_min_bucket_size",
        "evidence_definition",
        "classification_labels",
    }
    snapshot = target_snapshot()
    assert snapshot["metric_schema_version"] == QUALITY_METRIC_SCHEMA_VERSION
    assert snapshot["confidence_bin_boundaries"] == [
        Decimal("0"),
        Decimal("0.2"),
        Decimal("0.4"),
        Decimal("0.6"),
        Decimal("0.8"),
        Decimal("1"),
    ]
    assert (
        snapshot["evidence_definition"]
        == "expected_non_unknown_numeric_with_validated_evidence"
    )
    assert snapshot["classification_labels"] == {
        "positive": "advance",
        "negative": "reject",
        "excluded": ["borderline"],
        "predict_positive": "grade_not_rejected",
    }


def test_persisted_targets_are_json_numbers_not_strings() -> None:
    persisted = targets_for_persistence(target_snapshot())
    round_tripped = json.loads(json.dumps(persisted))

    assert round_tripped["f1_target"] == 0.75
    assert not isinstance(round_tripped["f1_target"], str)
    assert round_tripped["confidence_bin_boundaries"] == [0, 0.2, 0.4, 0.6, 0.8, 1]
    assert isinstance(round_tripped["confidence_min_bucket_size"], int)


# --- fingerprint sensitivity ------------------------------------------------


def test_fingerprint_is_stable_under_binding_ordering() -> None:
    assert _fingerprint() == _fingerprint(bindings=list(reversed(_bindings())))


def test_fingerprint_changes_with_the_golden_content() -> None:
    other = golden_content_hash([GoldenRow(jd_id=1, candidate_id=99, label="advance")])

    assert _fingerprint(golden_hash=other) != _fingerprint()


@pytest.mark.parametrize(
    "bindings",
    [
        [JDBinding(jd_id=1, jd_code="TRADE", rule_version_id=11)],
        [JDBinding(jd_id=1, jd_code="TRADE", rule_version_id=10)],
    ],
)
def test_fingerprint_changes_with_the_selected_rule_versions(
    bindings: list[JDBinding],
) -> None:
    assert _fingerprint(bindings=bindings) != _fingerprint()


@pytest.mark.parametrize(
    "overrides",
    [
        {"window_start": WINDOW_START - timedelta(seconds=1)},
        {"window_end": NOW - timedelta(seconds=1)},
    ],
)
def test_fingerprint_changes_with_the_window(overrides: dict) -> None:
    assert _fingerprint(**overrides) != _fingerprint()


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("metric_schema_version", "wp7_v2"),
        ("f1_target", Decimal("0.76")),
        ("evidence_coverage_target", Decimal("0.9")),
        ("confidence_min_bucket_size", 11),
        ("evidence_definition", "something_else"),
        ("confidence_bin_boundaries", [Decimal("0"), Decimal("0.5"), Decimal("1")]),
        (
            "classification_labels",
            {
                "positive": "advance",
                "negative": "reject",
                "excluded": [],
                "predict_positive": "grade_not_rejected",
            },
        ),
    ],
)
def test_every_target_field_changes_the_fingerprint(key: str, value: object) -> None:
    mutated = {**target_snapshot(), key: value}

    assert _fingerprint(targets=mutated) != _fingerprint()
