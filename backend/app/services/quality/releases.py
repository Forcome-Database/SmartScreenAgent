from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from hashlib import sha256
from typing import Any

from backend.app.config import get_settings

QUALITY_METRIC_SCHEMA_VERSION = "wp7_v1"
EVIDENCE_DEFINITION = "expected_non_unknown_numeric_with_validated_evidence"

DEFAULT_RELEASE_WINDOW = timedelta(days=30)
MAX_RELEASE_WINDOW = timedelta(days=365)

CONFIDENCE_BIN_BOUNDARIES = [
    Decimal("0"),
    Decimal("0.2"),
    Decimal("0.4"),
    Decimal("0.6"),
    Decimal("0.8"),
    Decimal("1"),
]


class InvalidReleaseWindow(ValueError):
    """Window is naive, not increasing, or ends in the future."""


class ReleaseWindowTooLarge(ValueError):
    """Window spans more than `MAX_RELEASE_WINDOW`."""


@dataclass(frozen=True)
class JDBinding:
    jd_id: int
    jd_code: str
    rule_version_id: int


@dataclass(frozen=True)
class GoldenRow:
    jd_id: int
    candidate_id: int
    label: str


def _number_token(value: Decimal) -> str:
    """A stable fixed-point JSON number token.

    Binary-float formatting is never used: the same target must hash identically
    on every host and Python version, and `repr(float)` does not guarantee that.
    """
    if not value.is_finite():
        raise ValueError("fingerprint numbers must be finite")
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return "0" if text in {"-0", "", "-"} else text


def canonical_json(value: Any) -> str:
    """Compact JSON with sorted keys and normalized numeric tokens."""
    if isinstance(value, Decimal):
        return _number_token(value)
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return "null"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, dict):
        body = ",".join(
            f"{json.dumps(str(key), ensure_ascii=False)}:{canonical_json(item)}"
            for key, item in sorted(value.items())
        )
        return "{" + body + "}"
    if isinstance(value, (list, tuple)):
        return "[" + ",".join(canonical_json(item) for item in value) + "]"
    raise TypeError(f"cannot canonicalize {type(value).__name__}")


def golden_content_hash(rows: list[GoldenRow]) -> str:
    """Content address of the selected golden rows, independent of input order."""
    ordered = sorted(
        ([row.jd_id, row.candidate_id, row.label] for row in rows),
        key=lambda row: (row[0], row[1], row[2]),
    )
    return sha256(canonical_json(ordered).encode()).hexdigest()


def target_snapshot() -> dict[str, Any]:
    """The metric definitions this release is judged by, frozen at creation."""
    settings = get_settings()
    return {
        "metric_schema_version": QUALITY_METRIC_SCHEMA_VERSION,
        "f1_target": Decimal(str(settings.QUALITY_F1_TARGET)),
        "evidence_coverage_target": Decimal(
            str(settings.QUALITY_EVIDENCE_COVERAGE_TARGET)
        ),
        "confidence_bin_boundaries": list(CONFIDENCE_BIN_BOUNDARIES),
        "confidence_min_bucket_size": settings.QUALITY_CONFIDENCE_MIN_BUCKET_SIZE,
        "evidence_definition": EVIDENCE_DEFINITION,
        "classification_labels": {
            "positive": "advance",
            "negative": "reject",
            "excluded": ["borderline"],
            "predict_positive": "grade_not_rejected",
        },
    }


def targets_for_persistence(targets: dict[str, Any]) -> dict[str, Any]:
    """The very bytes that were hashed, parsed back into JSON numbers for JSONB."""
    parsed: dict[str, Any] = json.loads(canonical_json(targets))
    return parsed


def resolve_release_window(
    start: datetime | None, end: datetime | None, now: datetime
) -> tuple[datetime, datetime]:
    resolved_end = now if end is None else end
    resolved_start = (
        resolved_end - DEFAULT_RELEASE_WINDOW if start is None else start
    )

    for bound in (resolved_start, resolved_end):
        if bound.tzinfo is None or bound.tzinfo.utcoffset(bound) is None:
            raise InvalidReleaseWindow("release window must be timezone-aware")
    if resolved_start >= resolved_end:
        raise InvalidReleaseWindow("release window must be increasing")
    if resolved_end > now:
        raise InvalidReleaseWindow("release window cannot end in the future")
    if resolved_end - resolved_start > MAX_RELEASE_WINDOW:
        raise ReleaseWindowTooLarge("release window is too large")

    return resolved_start, resolved_end


def input_fingerprint(
    *,
    golden_hash: str,
    bindings: list[JDBinding],
    window_start: datetime,
    window_end: datetime,
    targets: dict[str, Any],
) -> str:
    """Identity of everything a release is computed from.

    Two previews producing the same fingerprint describe the same inputs, so a
    create can refuse to proceed when the world moved underneath it.
    """
    payload = {
        "metric_schema_version": QUALITY_METRIC_SCHEMA_VERSION,
        "golden_content_sha256": golden_hash,
        "bindings": [
            {
                "jd_id": binding.jd_id,
                "jd_code": binding.jd_code,
                "rule_version_id": binding.rule_version_id,
            }
            for binding in sorted(bindings, key=lambda item: item.jd_id)
        ],
        "window_start": window_start.astimezone(UTC).isoformat(),
        "window_end": window_end.astimezone(UTC).isoformat(),
        "targets": targets,
    }
    return sha256(canonical_json(payload).encode()).hexdigest()
