from __future__ import annotations

import json
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from hashlib import sha256
from typing import Any

from pydantic import ValidationError
from sqlalchemy import and_, func, or_, select
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.config import get_settings
from backend.app.models import (
    JD,
    AuditLog,
    Feedback,
    GoldenSet,
    GoldenSetSnapshot,
    GoldenSetSnapshotEntry,
    QualityRelease,
    QualityReleaseJD,
    RuleVersion,
    Score,
    User,
)
from backend.app.rules.schema import RuleSchema
from backend.app.services.quality.metrics import (
    AgreementObservation,
    BoundJudgeDimension,
    JudgeObservation,
    QualityItem,
    agreement_metrics,
    classification_metrics,
    confidence_metrics,
    evidence_metrics,
    release_rollup,
    target_result,
)

MAX_RELEASE_ATTEMPTS = 3
# PostgreSQL: serialization failure and deadlock detected.
RETRYABLE_SQLSTATES = {"40001", "40P01"}
SNAPSHOT_UNIQUE_CONSTRAINT = "uq_golden_snapshots_content_sha256"

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


class GoldenSetEmpty(Exception):
    """Nothing is labelled for the selected JDs, so there is nothing to measure."""


class ActiveRuleMissing(Exception):
    """A selected JD has no active rule version to bind the release to."""


class InvalidActiveRule(Exception):
    """A selected JD's active rule schema cannot produce a meaningful metric."""


class ReleaseInputChanged(Exception):
    """Inputs moved between preview and create; the preview is no longer truthful."""


class ReleaseTransactionConflict(Exception):
    """Concurrent writers kept conflicting for every permitted attempt."""


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


@dataclass(frozen=True)
class ReleaseRequest:
    window_start: datetime | None = None
    window_end: datetime | None = None
    jd_codes: list[str] | None = None
    expected_input_fingerprint: str | None = None


@dataclass(frozen=True)
class GatheredRelease:
    """Everything a release is computed from, read inside a single transaction."""

    window_start: datetime
    window_end: datetime
    bindings: list[JDBinding]
    golden_rows: list[GoldenRow]
    golden_hash: str
    targets: dict[str, Any]
    fingerprint: str
    items: list[QualityItem]
    expected_dimensions: list[BoundJudgeDimension]
    agreement: list[AgreementObservation]
    score_covered: int
    score_uncovered: int


def _safe_decimal(value: object) -> Decimal | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return parsed if parsed.is_finite() else None


def _observations(payload: dict | None) -> list[JudgeObservation]:
    """Reduce a persisted judge payload to content-free observations.

    Quotes and reasoning collapse to a boolean here, at the boundary, so no
    downstream metric or response code is even able to leak them.
    """
    entries = (payload or {}).get("dimensions")
    if not isinstance(entries, list):
        return []
    observations: list[JudgeObservation] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        quotes = entry.get("evidence_quotes")
        observations.append(
            JudgeObservation(
                dimension_id=str(entry.get("id", "")),
                tier=str(entry.get("tier") or "unknown"),
                score=_safe_decimal(entry.get("score")),
                confidence=_safe_decimal(entry.get("confidence")),
                has_validated_evidence=bool(isinstance(quotes, list) and quotes),
            )
        )
    return observations


async def _resolve_bindings(
    db: AsyncSession, jd_codes: list[str] | None
) -> tuple[list[JDBinding], dict[int, RuleSchema]]:
    statement = select(JD)
    if jd_codes is not None:
        statement = statement.where(JD.code.in_(jd_codes))
    else:
        statement = statement.where(JD.id.in_(select(GoldenSet.jd_id).distinct()))
    jds = list((await db.execute(statement.order_by(JD.id))).scalars())
    if not jds:
        raise GoldenSetEmpty("no labelled JD to release")

    bindings: list[JDBinding] = []
    schemas: dict[int, RuleSchema] = {}
    for jd in jds:
        if not jd.active_rule_version_id:
            raise ActiveRuleMissing(f"JD {jd.code} has no active rule version")
        version = (
            await db.execute(
                select(RuleVersion).where(RuleVersion.id == jd.active_rule_version_id)
            )
        ).scalar_one()
        try:
            schemas[jd.id] = RuleSchema.model_validate(version.schema_json)
        except ValidationError as exc:
            raise InvalidActiveRule(f"JD {jd.code} has an invalid active rule") from exc
        bindings.append(
            JDBinding(jd_id=jd.id, jd_code=jd.code, rule_version_id=version.id)
        )
    return bindings, schemas


async def _matched_scores(
    db: AsyncSession, bindings: list[JDBinding], start: datetime, end: datetime
) -> dict[tuple[int, int], Any]:
    """Newest in-window score per (candidate, JD) that used the bound version."""
    bound = or_(
        *[
            and_(Score.jd_id == b.jd_id, Score.rule_version_id == b.rule_version_id)
            for b in bindings
        ]
    )
    ranked = (
        select(
            Score.id.label("id"),
            Score.candidate_id.label("candidate_id"),
            Score.jd_id.label("jd_id"),
            Score.grade.label("grade"),
            Score.judge_dimensions.label("judge_dimensions"),
            func.row_number()
            .over(
                partition_by=(Score.candidate_id, Score.jd_id),
                order_by=(Score.created_at.desc(), Score.id.desc()),
            )
            .label("rn"),
        )
        .where(bound, Score.created_at >= start, Score.created_at < end)
        .subquery()
    )
    rows = (await db.execute(select(ranked).where(ranked.c.rn == 1))).all()
    return {(row.candidate_id, row.jd_id): row for row in rows}


async def _gather(
    db: AsyncSession, request: ReleaseRequest, now: datetime
) -> GatheredRelease:
    window_start, window_end = resolve_release_window(
        request.window_start, request.window_end, now
    )
    bindings, schemas = await _resolve_bindings(db, request.jd_codes)
    jd_ids = [binding.jd_id for binding in bindings]

    golden = list(
        (
            await db.execute(
                select(GoldenSet)
                .where(GoldenSet.jd_id.in_(jd_ids))
                .order_by(GoldenSet.jd_id, GoldenSet.candidate_id)
            )
        ).scalars()
    )
    if not golden:
        raise GoldenSetEmpty("no golden labels for the selected JDs")

    rows = [
        GoldenRow(jd_id=row.jd_id, candidate_id=row.candidate_id, label=row.label)
        for row in golden
    ]
    scores = await _matched_scores(db, bindings, window_start, window_end)

    items = [
        QualityItem(
            jd_id=row.jd_id,
            candidate_id=row.candidate_id,
            golden_label=row.label,  # type: ignore[arg-type]
            grade=None if match is None else match.grade,
            reached_judge=match is not None and match.judge_dimensions is not None,
            judge=[] if match is None else _observations(match.judge_dimensions),
        )
        for row, match in (
            (row, scores.get((row.candidate_id, row.jd_id))) for row in golden
        )
    ]

    expected_dimensions = [
        BoundJudgeDimension(
            jd_id=jd_id, id=dimension.id, weight=Decimal(str(dimension.weight))
        )
        for jd_id, schema in schemas.items()
        for dimension in schema.judge_dimensions
    ]

    matched_ids = [row.id for row in scores.values()]
    agreement: list[AgreementObservation] = []
    if matched_ids:
        stamped = func.coalesce(Feedback.updated_at, Feedback.created_at)
        agreement = [
            AgreementObservation(jd_id=jd_id, ai_agreed=agreed)
            for agreed, jd_id in (
                await db.execute(
                    select(Feedback.ai_agreed, Score.jd_id)
                    .join(Score, Score.id == Feedback.score_id)
                    .where(
                        Feedback.score_id.in_(matched_ids),
                        stamped >= window_start,
                        stamped < window_end,
                    )
                )
            ).all()
        ]

    targets = target_snapshot()
    golden_hash = golden_content_hash(rows)
    return GatheredRelease(
        window_start=window_start,
        window_end=window_end,
        bindings=bindings,
        golden_rows=rows,
        golden_hash=golden_hash,
        targets=targets,
        fingerprint=input_fingerprint(
            golden_hash=golden_hash,
            bindings=bindings,
            window_start=window_start,
            window_end=window_end,
            targets=targets,
        ),
        items=items,
        expected_dimensions=expected_dimensions,
        agreement=agreement,
        score_covered=sum(1 for item in items if item.grade is not None),
        score_uncovered=sum(1 for item in items if item.grade is None),
    )


def _metric_block(
    items: list[QualityItem],
    dimensions: list[BoundJudgeDimension],
    agreement: list[AgreementObservation],
    targets: dict[str, Any],
) -> dict[str, Any]:
    """Compute one metric family set and its target results, JSON-ready."""
    minimum_bucket = int(targets["confidence_min_bucket_size"])
    classification = classification_metrics(items)
    evidence = evidence_metrics(items, dimensions)
    confidence = confidence_metrics(items, dimensions, minimum_bucket)
    agreed = agreement_metrics(agreement)

    f1_result = target_result(
        classification["f1"], float(targets["f1_target"]), "insufficient_data"
    )
    evidence_result = target_result(
        evidence["value"],
        float(targets["evidence_coverage_target"]),
        "not_applicable" if evidence["status"] == "not_applicable" else "insufficient_data",
    )

    return {
        "classification": classification,
        "evidence": evidence,
        "confidence": confidence,
        "agreement": agreed,
        "f1_target_result": f1_result,
        "evidence_target_result": evidence_result,
    }


def _json_ready(value: Any) -> Any:
    """Decimals become JSON numbers using the same tokens the fingerprint hashed."""
    if isinstance(value, Decimal):
        return json.loads(canonical_json(value))
    if isinstance(value, dict):
        return {key: _json_ready(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_ready(item) for item in value]
    return value


def compute_release_metrics(gathered: GatheredRelease) -> tuple[dict, list[dict], str]:
    """Aggregate metrics, per-JD metrics, and the resulting release status."""
    aggregate = _metric_block(
        gathered.items,
        gathered.expected_dimensions,
        gathered.agreement,
        gathered.targets,
    )
    status = release_rollup(
        aggregate["f1_target_result"], aggregate["evidence_target_result"]
    )

    per_jd: list[dict] = []
    for binding in gathered.bindings:
        block = _metric_block(
            [item for item in gathered.items if item.jd_id == binding.jd_id],
            [d for d in gathered.expected_dimensions if d.jd_id == binding.jd_id],
            [a for a in gathered.agreement if a.jd_id == binding.jd_id],
            gathered.targets,
        )
        per_jd.append(
            {
                "jd_id": binding.jd_id,
                "jd_code": binding.jd_code,
                "rule_version_id": binding.rule_version_id,
                **block,
            }
        )

    return _json_ready(aggregate), _json_ready(per_jd), status


async def _upsert_snapshot(
    db: AsyncSession, gathered: GatheredRelease, actor_user_id: int, now: datetime
) -> int:
    """Reuse the snapshot for identical content; otherwise write it and its rows."""
    existing = (
        await db.execute(
            select(GoldenSetSnapshot.id).where(
                GoldenSetSnapshot.content_sha256 == gathered.golden_hash
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        return int(existing)

    snapshot = GoldenSetSnapshot(
        content_sha256=gathered.golden_hash,
        item_count=len(gathered.golden_rows),
        created_by_user_id=actor_user_id,
        created_at=now,
    )
    db.add(snapshot)
    await db.flush()
    db.add_all(
        [
            GoldenSetSnapshotEntry(
                snapshot_id=snapshot.id,
                candidate_id=row.candidate_id,
                jd_id=row.jd_id,
                label=row.label,
            )
            for row in gathered.golden_rows
        ]
    )
    await db.flush()
    return snapshot.id


def _write_release_audits(
    db: AsyncSession,
    release: QualityRelease,
    gathered: GatheredRelease,
    aggregate: dict,
    actor_user_id: int,
) -> None:
    payload = {
        "golden_snapshot_sha256": gathered.golden_hash,
        "bindings": [
            {"jd_id": b.jd_id, "rule_version_id": b.rule_version_id}
            for b in gathered.bindings
        ],
        "window_start": gathered.window_start.isoformat(),
        "window_end": gathered.window_end.isoformat(),
        "f1_status": aggregate["f1_target_result"]["status"],
        "evidence_status": aggregate["evidence_target_result"]["status"],
        "status": release.status,
    }
    events = ["quality_release_created"]
    if release.status == "below_target":
        events.append("quality_release_below_target")
    for event in events:
        db.add(
            AuditLog(
                event_type=event,
                actor=f"user:{actor_user_id}",
                target_type="quality_release",
                target_id=release.id,
                payload=payload,
            )
        )


def _is_retryable(exc: BaseException) -> bool:
    if isinstance(exc, IntegrityError):
        return SNAPSHOT_UNIQUE_CONSTRAINT in str(exc)
    if isinstance(exc, DBAPIError):
        sqlstate = getattr(getattr(exc, "orig", None), "sqlstate", None)
        return sqlstate in RETRYABLE_SQLSTATES
    return False


def _preview_payload(gathered: GatheredRelease) -> dict[str, Any]:
    labels = [row.label for row in gathered.golden_rows]
    return {
        "window_start": gathered.window_start,
        "window_end": gathered.window_end,
        "selected": [
            {
                "jd_id": b.jd_id,
                "jd_code": b.jd_code,
                "rule_version_id": b.rule_version_id,
            }
            for b in gathered.bindings
        ],
        "golden_total": len(labels),
        "golden_advance": labels.count("advance"),
        "golden_reject": labels.count("reject"),
        "golden_borderline": labels.count("borderline"),
        "score_covered": gathered.score_covered,
        "score_uncovered": gathered.score_uncovered,
        "targets": targets_for_persistence(gathered.targets),
        "input_fingerprint": gathered.fingerprint,
    }


async def preview_release(
    db: AsyncSession, request: ReleaseRequest, now: datetime
) -> dict[str, Any]:
    """Resolve and measure the release inputs without writing anything."""
    return _preview_payload(await _gather(db, request, now))


def _detail_payload(
    release: QualityRelease,
    creator: User,
    snapshot: GoldenSetSnapshot,
    aggregate: dict,
    per_jd: list[dict],
) -> dict[str, Any]:
    return {
        "id": release.id,
        "status": release.status,
        "golden_snapshot_sha256": snapshot.content_sha256,
        "golden_snapshot_item_count": snapshot.item_count,
        "window_start": release.window_start,
        "window_end": release.window_end,
        "created_at": release.created_at,
        "created_by": {
            "user_id": creator.id,
            "display_name": creator.display_name,
        },
        "targets": release.targets_json,
        **aggregate,
        "by_jd": per_jd,
    }


SessionFactory = Callable[[], AbstractAsyncContextManager[AsyncSession]]


async def create_release(
    session_factory: SessionFactory,
    request: ReleaseRequest,
    actor_user_id: int,
    now: datetime,
) -> dict[str, Any]:
    """Create one immutable release, retrying only genuine write conflicts.

    Every attempt uses a fresh REPEATABLE READ transaction and repeats every read
    from scratch, so a retry can never mix rows observed under two snapshots.
    """
    for _attempt in range(MAX_RELEASE_ATTEMPTS):
        try:
            async with session_factory() as session:
                await session.connection(
                    execution_options={"isolation_level": "REPEATABLE READ"}
                )
                gathered = await _gather(session, request, now)
                if (
                    request.expected_input_fingerprint is not None
                    and request.expected_input_fingerprint != gathered.fingerprint
                ):
                    raise ReleaseInputChanged("release inputs changed since preview")

                aggregate, per_jd, status = compute_release_metrics(gathered)
                snapshot_id = await _upsert_snapshot(
                    session, gathered, actor_user_id, now
                )

                release = QualityRelease(
                    golden_snapshot_id=snapshot_id,
                    window_start=gathered.window_start,
                    window_end=gathered.window_end,
                    status=status,
                    metrics_json={"aggregate": aggregate, "by_jd": per_jd},
                    targets_json=targets_for_persistence(gathered.targets),
                    created_by_user_id=actor_user_id,
                    created_at=now,
                )
                session.add(release)
                await session.flush()
                session.add_all(
                    [
                        QualityReleaseJD(
                            quality_release_id=release.id,
                            jd_id=binding.jd_id,
                            rule_version_id=binding.rule_version_id,
                            metrics_json=metrics,
                        )
                        for binding, metrics in zip(gathered.bindings, per_jd, strict=True)
                    ]
                )
                _write_release_audits(
                    session, release, gathered, aggregate, actor_user_id
                )
                await session.flush()

                snapshot = await session.get(GoldenSetSnapshot, snapshot_id)
                creator = await session.get(User, actor_user_id)
                assert snapshot is not None and creator is not None
                detail = _detail_payload(release, creator, snapshot, aggregate, per_jd)
                await session.commit()
                return detail
        except Exception as exc:
            if not _is_retryable(exc):
                raise
    raise ReleaseTransactionConflict("release creation kept conflicting")


async def get_release(db: AsyncSession, release_id: int) -> dict[str, Any] | None:
    release = await db.get(QualityRelease, release_id)
    if release is None:
        return None
    snapshot = await db.get(GoldenSetSnapshot, release.golden_snapshot_id)
    creator = await db.get(User, release.created_by_user_id)
    assert snapshot is not None and creator is not None
    stored = release.metrics_json
    return _detail_payload(
        release, creator, snapshot, stored["aggregate"], stored["by_jd"]
    )


async def list_releases(
    db: AsyncSession,
    *,
    offset: int,
    limit: int,
    jd_code: str | None = None,
    status: str | None = None,
) -> tuple[list[dict[str, Any]], int]:
    statement = select(QualityRelease)
    if status is not None:
        statement = statement.where(QualityRelease.status == status)
    if jd_code is not None:
        statement = statement.where(
            QualityRelease.id.in_(
                select(QualityReleaseJD.quality_release_id)
                .join(JD, JD.id == QualityReleaseJD.jd_id)
                .where(JD.code == jd_code)
            )
        )

    total = (
        await db.execute(
            select(func.count()).select_from(statement.subquery())
        )
    ).scalar_one()
    rows = list(
        (
            await db.execute(
                statement.order_by(
                    QualityRelease.created_at.desc(), QualityRelease.id.desc()
                )
                .offset(offset)
                .limit(limit)
            )
        ).scalars()
    )

    items: list[dict[str, Any]] = []
    for release in rows:
        snapshot = await db.get(GoldenSetSnapshot, release.golden_snapshot_id)
        creator = await db.get(User, release.created_by_user_id)
        assert snapshot is not None and creator is not None
        items.append(
            {
                "id": release.id,
                "status": release.status,
                "window_start": release.window_start,
                "window_end": release.window_end,
                "created_at": release.created_at,
                "created_by": {
                    "user_id": creator.id,
                    "display_name": creator.display_name,
                },
                "golden_snapshot_sha256": snapshot.content_sha256,
            }
        )
    return items, int(total)
