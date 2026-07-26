from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.models import (
    JD,
    AuditLog,
    Candidate,
    Feedback,
    GoldenSet,
    GoldenSetSnapshot,
    GoldenSetSnapshotEntry,
    LLMUsageAttempt,
    QualityRelease,
    RuleVersion,
    Score,
    User,
)
from backend.app.security.crypto import encrypt_pii

pytestmark = pytest.mark.integration

PREVIEW = "/api/v1/quality/releases/preview"
RELEASES = "/api/v1/quality/releases"

FIXTURE = Path(__file__).parents[1] / "fixtures" / "sample_rule_v1.json"
NOW = datetime.now(timezone.utc)
IN_WINDOW = NOW - timedelta(days=1)


def _schema(code: str, *, judge_weight: float = 10) -> dict:
    schema = json.loads(FIXTURE.read_text(encoding="utf-8"))
    schema["jd_code"] = code
    schema["judge_dimensions"][0]["weight"] = judge_weight
    schema["total_score"] = 90 + judge_weight
    return schema


async def _seed_jd(
    db: AsyncSession, code: str, *, schema: dict | None = None, activate: bool = True
) -> tuple[JD, RuleVersion]:
    jd = JD(code=code, name=code, description="", status="active")
    db.add(jd)
    await db.flush()
    version = RuleVersion(
        jd_id=jd.id,
        version="v1",
        published_at=NOW - timedelta(days=60),
        schema_json=schema if schema is not None else _schema(code),
    )
    db.add(version)
    await db.flush()
    if activate:
        jd.active_rule_version_id = version.id
    await db.flush()
    return jd, version


async def _seed_candidate(db: AsyncSession) -> Candidate:
    candidate = Candidate(
        source="upload",
        name_cipher=encrypt_pii("private-name"),
        pii_hash=uuid4().hex,
        extracted_json={},
    )
    db.add(candidate)
    await db.flush()
    return candidate


async def _seed_golden(
    db: AsyncSession, candidate: Candidate, jd: JD, label: str, user_id: int
) -> None:
    db.add(
        GoldenSet(
            candidate_id=candidate.id,
            jd_id=jd.id,
            label=label,
            imported_at=NOW - timedelta(days=40),
            imported_by_user_id=user_id,
        )
    )
    await db.flush()


def _judge_payload(*, tier: str = "high", confidence: float | None = 0.9,
                   quotes: list[str] | None = None) -> dict:
    return {
        "dimensions": [
            {
                "id": "independence",
                "tier": tier,
                "score": 10 if tier != "unknown" else None,
                "evidence_quotes": ["quotable evidence"] if quotes is None else quotes,
                "reasoning": "private reasoning",
                "confidence": confidence,
                "suggested_interview_questions": [],
            }
        ],
        "model": "test-judge",
        "tokens": 10,
        "prompt_version": "resume_judge_v1",
    }


async def _seed_score(
    db: AsyncSession,
    candidate: Candidate,
    jd: JD,
    version: RuleVersion,
    *,
    grade: str = "L1",
    judge: dict | None = None,
    created_at: datetime | None = None,
) -> Score:
    score = Score(
        candidate_id=candidate.id,
        jd_id=jd.id,
        rule_version_id=version.id,
        total_score=70,
        grade=grade,
        hard_filter_result={"rejected": grade == "rejected"},
        rule_dimensions={},
        judge_dimensions=judge if judge is not None else _judge_payload(),
        created_at=created_at or IN_WINDOW,
    )
    db.add(score)
    await db.flush()
    return score


async def _seed_release_fixture(db: AsyncSession, code: str, user_id: int) -> JD:
    jd, version = await _seed_jd(db, code)
    for label, grade in (("advance", "L1"), ("reject", "rejected")):
        candidate = await _seed_candidate(db)
        await _seed_golden(db, candidate, jd, label, user_id)
        await _seed_score(db, candidate, jd, version, grade=grade)
    await db.commit()
    return jd


async def _seed_attempt(
    db: AsyncSession,
    *,
    operation: str,
    jd_id: int | None = None,
    rule_version_id: int | None = None,
    latency_ms: int = 100,
    cost: Decimal = Decimal("1"),
) -> None:
    db.add(
        LLMUsageAttempt(
            call_group_id=uuid4(),
            trace_id=f"trace-{uuid4().hex[:8]}",
            jd_id=jd_id,
            rule_version_id=rule_version_id,
            operation=operation,
            attempt_role="primary",
            requested_model="test-judge",
            actual_model="test-judge",
            prompt_version="resume_judge_v1",
            status="succeeded",
            input_tokens=10,
            output_tokens=5,
            input_price_cny_per_million=Decimal("1.000000"),
            output_price_cny_per_million=Decimal("2.000000"),
            estimated_cost_cny=cost,
            latency_ms=latency_ms,
            error_code=None,
            started_at=IN_WINDOW,
            finished_at=IN_WINDOW + timedelta(seconds=1),
        )
    )
    await db.flush()


async def _importer_id(db: AsyncSession) -> int:
    """A user to own the golden rows, independent of when auth_headers runs."""
    user = User(
        dingtalk_userid=f"importer-{uuid4().hex}",
        display_name="Importer",
        role="admin",
    )
    db.add(user)
    await db.flush()
    return user.id


async def _seeded_jd(db: AsyncSession, prefix: str) -> JD:
    """A JD with a unique code, its golden labels, and matching scores."""
    return await _seed_release_fixture(
        db, f"{prefix}_{uuid4().hex[:6]}", await _importer_id(db)
    )


# --- preview ----------------------------------------------------------------


async def test_preview_is_read_only_and_deterministic(client, db_session, auth_headers):
    headers = await auth_headers("hr_lead")
    await _seeded_jd(db_session, "PRV")

    # Determinism is "same inputs, same fingerprint". A DEFAULTED window is
    # anchored to now, so it legitimately moves between calls — which is exactly
    # why preview returns its resolved window for create to echo back.
    window = {
        "window_start": (NOW - timedelta(days=30)).isoformat(),
        "window_end": NOW.isoformat(),
    }
    first = await client.post(PREVIEW, json=window, headers=headers)
    second = await client.post(PREVIEW, json=window, headers=headers)

    assert first.status_code == 200
    assert first.json()["input_fingerprint"] == second.json()["input_fingerprint"]
    assert first.json()["golden_total"] == 2

    defaulted = (await client.post(PREVIEW, json={}, headers=headers)).json()
    assert defaulted["window_start"] and defaulted["window_end"]
    # Nothing was written.
    assert (
        await db_session.execute(select(func.count()).select_from(QualityRelease))
    ).scalar_one() == 0
    assert (
        await db_session.execute(select(func.count()).select_from(GoldenSetSnapshot))
    ).scalar_one() == 0


async def test_preview_fingerprint_tracks_golden_and_window_changes(
    client, db_session, auth_headers
):
    headers = await auth_headers("hr_lead")
    user_id = await _importer_id(db_session)
    jd = await _seed_release_fixture(db_session, f"PRV_{uuid4().hex[:6]}", user_id)

    baseline = (await client.post(PREVIEW, json={}, headers=headers)).json()

    narrower = (
        await client.post(
            PREVIEW,
            json={"window_start": (NOW - timedelta(days=3)).isoformat()},
            headers=headers,
        )
    ).json()
    assert narrower["input_fingerprint"] != baseline["input_fingerprint"]

    extra = await _seed_candidate(db_session)
    await _seed_golden(db_session, extra, jd, "advance", user_id)
    await db_session.commit()

    after_golden = (await client.post(PREVIEW, json={}, headers=headers)).json()
    assert after_golden["input_fingerprint"] != baseline["input_fingerprint"]
    assert after_golden["golden_total"] == 3


async def test_preview_reports_score_coverage_against_the_bound_version(
    client, db_session, auth_headers
):
    headers = await auth_headers("hr_lead")
    user_id = await _importer_id(db_session)
    jd, version = await _seed_jd(db_session, f"COV_{uuid4().hex[:6]}")

    covered = await _seed_candidate(db_session)
    await _seed_golden(db_session, covered, jd, "advance", user_id)
    await _seed_score(db_session, covered, jd, version)

    # Scored before the window opened.
    stale = await _seed_candidate(db_session)
    await _seed_golden(db_session, stale, jd, "advance", user_id)
    await _seed_score(db_session, stale, jd, version, created_at=NOW - timedelta(days=45))

    # Never scored at all.
    unscored = await _seed_candidate(db_session)
    await _seed_golden(db_session, unscored, jd, "reject", user_id)
    await db_session.commit()

    body = (await client.post(PREVIEW, json={}, headers=headers)).json()

    assert body["score_covered"] == 1
    assert body["score_uncovered"] == 2


# --- selection and precondition errors --------------------------------------

async def test_empty_golden_set_is_refused(client, db_session, auth_headers):
    response = await client.post(PREVIEW, json={}, headers=await auth_headers("hr_lead"))

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "golden_set_empty"


async def test_missing_active_rule_is_refused(client, db_session, auth_headers):
    user_id = await _importer_id(db_session)
    jd, _version = await _seed_jd(db_session, f"NOACT_{uuid4().hex[:6]}", activate=False)
    candidate = await _seed_candidate(db_session)
    await _seed_golden(db_session, candidate, jd, "advance", user_id)
    await db_session.commit()

    response = await client.post(PREVIEW, json={}, headers=await auth_headers("hr_lead"))

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "active_rule_missing"


async def test_malformed_active_rule_is_refused(client, db_session, auth_headers):
    user_id = await _importer_id(db_session)
    code = f"BADRULE_{uuid4().hex[:6]}"
    broken = _schema(code)
    broken["judge_dimensions"][0]["weight"] = -5
    jd, _version = await _seed_jd(db_session, code, schema=broken)
    candidate = await _seed_candidate(db_session)
    await _seed_golden(db_session, candidate, jd, "advance", user_id)
    await db_session.commit()

    response = await client.post(PREVIEW, json={}, headers=await auth_headers("hr_lead"))

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "invalid_active_rule"


@pytest.mark.parametrize(
    "window",
    [
        {"window_start": "2026-07-01T00:00:00"},
        {"window_end": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()},
        {
            "window_start": datetime.now(timezone.utc).isoformat(),
            "window_end": (datetime.now(timezone.utc) - timedelta(days=1)).isoformat(),
        },
    ],
)
async def test_invalid_windows_return_a_stable_error(
    client, db_session, auth_headers, window: dict
):
    response = await client.post(PREVIEW, json=window, headers=await auth_headers("hr_lead"))

    assert response.status_code == 422
    assert response.json()["detail"] == {
        "code": "invalid_release_window",
        "message": (
            "Release window must be timezone-aware, ordered, and end no later than now"
        ),
    }


async def test_window_over_a_year_is_refused(client, db_session, auth_headers):
    response = await client.post(
        PREVIEW,
        json={"window_start": (NOW - timedelta(days=400)).isoformat()},
        headers=await auth_headers("hr_lead"),
    )

    assert response.status_code == 422
    assert response.json()["detail"] == {
        "code": "release_window_too_large",
        "message": "Release window cannot exceed 365 days",
    }


# --- create -----------------------------------------------------------------


async def test_create_permissions(client, db_session, auth_headers):
    await _seeded_jd(db_session, "PERM")

    assert (await client.post(RELEASES, json={})).status_code == 401
    assert (
        await client.post(RELEASES, json={}, headers=await auth_headers("hr"))
    ).status_code == 403
    assert (
        await client.post(RELEASES, json={}, headers=await auth_headers("admin"))
    ).status_code == 201


async def test_read_permissions_allow_every_role(client, db_session, auth_headers):
    await _seeded_jd(db_session, "READ")
    created = await client.post(RELEASES, json={}, headers=await auth_headers("hr_lead"))
    release_id = created.json()["id"]

    for role in ("hr", "hr_lead", "admin"):
        headers = await auth_headers(role)
        assert (await client.get(RELEASES, headers=headers)).status_code == 200
        assert (
            await client.get(f"{RELEASES}/{release_id}", headers=headers)
        ).status_code == 200


async def test_stale_fingerprint_is_refused(client, db_session, auth_headers):
    headers = await auth_headers("hr_lead")
    user_id = await _importer_id(db_session)
    jd = await _seed_release_fixture(db_session, f"STALE_{uuid4().hex[:6]}", user_id)
    fingerprint = (await client.post(PREVIEW, json={}, headers=headers)).json()[
        "input_fingerprint"
    ]

    # The world moves between preview and create.
    extra = await _seed_candidate(db_session)
    await _seed_golden(db_session, extra, jd, "reject", user_id)
    await db_session.commit()

    response = await client.post(
        RELEASES, json={"expected_input_fingerprint": fingerprint}, headers=headers
    )

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "release_input_changed"


async def test_create_echoes_the_previewed_window_and_fingerprint(
    client, db_session, auth_headers
):
    headers = await auth_headers("hr_lead")
    await _seeded_jd(db_session, "ECHO")
    preview = (await client.post(PREVIEW, json={}, headers=headers)).json()

    response = await client.post(
        RELEASES,
        json={
            "window_start": preview["window_start"],
            "window_end": preview["window_end"],
            "expected_input_fingerprint": preview["input_fingerprint"],
        },
        headers=headers,
    )

    assert response.status_code == 201
    assert response.json()["window_start"] == preview["window_start"]


async def test_release_persists_snapshot_metrics_and_audit(client, db_session, auth_headers):
    headers = await auth_headers("hr_lead")
    await _seeded_jd(db_session, "PERSIST")

    body = (await client.post(RELEASES, json={}, headers=headers)).json()

    assert body["golden_snapshot_item_count"] == 2
    assert len(body["golden_snapshot_sha256"]) == 64
    assert body["classification"]["confusion"] == {"tp": 1, "fp": 0, "tn": 1, "fn": 0}
    assert body["created_by"]["display_name"]

    audits = (
        await db_session.execute(
            select(AuditLog).where(AuditLog.event_type == "quality_release_created")
        )
    ).scalars().all()
    assert len(audits) == 1
    assert audits[0].actor.startswith("user:")
    assert set(audits[0].payload) == {
        "golden_snapshot_sha256",
        "bindings",
        "window_start",
        "window_end",
        "f1_status",
        "evidence_status",
        "status",
    }


async def test_below_target_release_writes_both_audit_events(
    client, db_session, auth_headers
):
    headers = await auth_headers("hr_lead")
    user_id = await _importer_id(db_session)
    jd, version = await _seed_jd(db_session, f"BELOW_{uuid4().hex[:6]}")
    # Every prediction wrong => F1 of 0, which is below the 0.75 target.
    for label, grade in (("advance", "rejected"), ("reject", "L1")):
        candidate = await _seed_candidate(db_session)
        await _seed_golden(db_session, candidate, jd, label, user_id)
        await _seed_score(db_session, candidate, jd, version, grade=grade)
    await db_session.commit()

    body = (await client.post(RELEASES, json={}, headers=headers)).json()

    assert body["status"] == "below_target"
    events = {
        row.event_type
        for row in (
            await db_session.execute(
                select(AuditLog).where(AuditLog.event_type.like("quality_release%"))
            )
        ).scalars()
    }
    assert events == {"quality_release_created", "quality_release_below_target"}


async def test_snapshot_content_is_reused_but_each_release_is_new(
    client, db_session, auth_headers
):
    headers = await auth_headers("hr_lead")
    await _seeded_jd(db_session, "REUSE")

    first = (await client.post(RELEASES, json={}, headers=headers)).json()
    second = (await client.post(RELEASES, json={}, headers=headers)).json()

    assert first["golden_snapshot_sha256"] == second["golden_snapshot_sha256"]
    assert first["id"] != second["id"]
    snapshots = (
        await db_session.execute(select(func.count()).select_from(GoldenSetSnapshot))
    ).scalar_one()
    assert snapshots == 1


async def test_a_created_release_is_immutable_against_later_changes(
    client, db_session, auth_headers
):
    headers = await auth_headers("hr_lead")
    user_id = await _importer_id(db_session)
    jd = await _seed_release_fixture(db_session, f"IMM_{uuid4().hex[:6]}", user_id)
    created = (await client.post(RELEASES, json={}, headers=headers)).json()

    extra = await _seed_candidate(db_session)
    await _seed_golden(db_session, extra, jd, "reject", user_id)
    await db_session.commit()

    reloaded = (await client.get(f"{RELEASES}/{created['id']}", headers=headers)).json()

    assert reloaded == created


async def test_agreement_uses_window_and_version_matched_feedback(
    client, db_session, auth_headers
):
    headers = await auth_headers("hr_lead")
    user_id = await _importer_id(db_session)
    jd, version = await _seed_jd(db_session, f"AGREE_{uuid4().hex[:6]}")
    candidate = await _seed_candidate(db_session)
    await _seed_golden(db_session, candidate, jd, "advance", user_id)
    score = await _seed_score(db_session, candidate, jd, version)
    db_session.add(
        Feedback(
            score_id=score.id,
            reviewer_user_id=user_id,
            decision="advance",
            ai_agreed=True,
            created_at=IN_WINDOW,
            updated_at=IN_WINDOW,
        )
    )
    await db_session.commit()

    body = (await client.post(RELEASES, json={}, headers=headers)).json()

    assert body["agreement"]["agreed"] == 1
    assert body["agreement"]["denominator"] == 1


async def test_operation_metrics_use_release_attribution(
    client, db_session, auth_headers
):
    headers = await auth_headers("hr_lead")
    user_id = await _importer_id(db_session)
    jd, version = await _seed_jd(db_session, f"OPS_{uuid4().hex[:6]}")
    candidate = await _seed_candidate(db_session)
    await _seed_golden(db_session, candidate, jd, "advance", user_id)
    await _seed_score(db_session, candidate, jd, version)

    # A second JD's version, used to prove wrong-version judge work is excluded.
    other_jd, other_version = await _seed_jd(db_session, f"OTHER_{uuid4().hex[:6]}")

    await _seed_attempt(db_session, operation="extract", jd_id=jd.id, latency_ms=100)
    await _seed_attempt(
        db_session, operation="judge", rule_version_id=version.id, latency_ms=300
    )
    # Wrong version: stays in the global dashboard, never in this release.
    await _seed_attempt(
        db_session, operation="judge", rule_version_id=other_version.id, latency_ms=9999
    )
    # Unattributed usage.
    await _seed_attempt(db_session, operation="lightweight", latency_ms=8888)
    await db_session.commit()

    body = (
        await client.post(RELEASES, json={"jd_codes": [jd.code]}, headers=headers)
    ).json()

    current = body["operations"]["current"]
    assert current["attempt_count"] == 2
    assert current["known_cost_cny"] == 2
    # Continuous percentile over the two attributed latencies only.
    assert current["p50_latency_ms"] == 200
    assert current["scored_count"] == 1
    assert body["operations"]["previous"]["attempt_count"] == 0


# --- failure and conflict handling ------------------------------------------


async def test_a_failure_before_commit_leaves_nothing_behind(
    client, db_session, auth_headers, monkeypatch
):
    headers = await auth_headers("hr_lead")
    await _seeded_jd(db_session, "FAIL")

    from backend.app.services.quality import releases as releases_module

    def _boom(*_args, **_kwargs):
        raise RuntimeError("injected failure before commit")

    monkeypatch.setattr(releases_module, "_write_release_audits", _boom)

    with pytest.raises(RuntimeError):
        await client.post(RELEASES, json={}, headers=headers)

    for model in (QualityRelease, GoldenSetSnapshot, GoldenSetSnapshotEntry):
        assert (
            await db_session.execute(select(func.count()).select_from(model))
        ).scalar_one() == 0


async def test_repeated_conflicts_surface_as_service_unavailable(
    client, db_session, auth_headers, monkeypatch
):
    headers = await auth_headers("hr_lead")
    await _seeded_jd(db_session, "CONF")

    from sqlalchemy.exc import IntegrityError

    from backend.app.services.quality import releases as releases_module

    attempts = {"count": 0}

    async def _always_conflict(*_args, **_kwargs):
        attempts["count"] += 1
        raise IntegrityError("insert", {}, Exception("uq_golden_snapshots_content_sha256"))

    monkeypatch.setattr(releases_module, "_upsert_snapshot", _always_conflict)

    response = await client.post(RELEASES, json={}, headers=headers)

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "release_transaction_conflict"
    # Bounded: exactly three attempts, not an unbounded retry loop.
    assert attempts["count"] == 3


# --- reads ------------------------------------------------------------------


async def test_list_is_newest_first_and_filterable(client, db_session, auth_headers):
    headers = await auth_headers("hr_lead")
    user_id = await _importer_id(db_session)
    await _seed_release_fixture(db_session, f"L1_{uuid4().hex[:6]}", user_id)
    first = (await client.post(RELEASES, json={}, headers=headers)).json()
    second = (await client.post(RELEASES, json={}, headers=headers)).json()

    body = (await client.get(RELEASES, headers=headers)).json()

    assert [item["id"] for item in body["items"]] == [second["id"], first["id"]]
    assert body["total"] == 2

    filtered = (
        await client.get(f"{RELEASES}?status=below_target", headers=headers)
    ).json()
    assert filtered["total"] == 0


async def test_reads_expose_no_candidate_identity_or_content(
    client, db_session, auth_headers
):
    headers = await auth_headers("hr_lead")
    await _seeded_jd(db_session, "LEAK")
    created = (await client.post(RELEASES, json={}, headers=headers)).json()

    detail = (await client.get(f"{RELEASES}/{created['id']}", headers=headers)).text
    listing = (await client.get(RELEASES, headers=headers)).text

    for forbidden in (
        "private-name",
        "quotable evidence",
        "private reasoning",
        "candidate_id",
        "entries",
    ):
        assert forbidden not in detail
        assert forbidden not in listing
