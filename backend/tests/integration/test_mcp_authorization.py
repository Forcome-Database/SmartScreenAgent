from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from backend.app.config import get_settings
from backend.app.models import JD, AuditLog, Candidate, RuleVersion, Score, User
from backend.app.security.crypto import encrypt_pii
from backend.app.security.jwt import create_access_token

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

NOW = datetime.now(timezone.utc)

# Planted in `_seed_score`, one per kind of thing design §11.3 forbids a tool
# from returning. Every tool's output is scanned for all of them.
#
# Note what is *not* here: `resume_judge_v1`, the `prompt_version` seeded
# alongside the judge dimensions. WP7 scanned for the substring "resume" and
# false-positived on exactly that legitimate value, so the blacklist names
# whole seeded secrets and the field-set assertions — not a substring sweep —
# are what prove nothing else came back.
SEEDED_SECRETS = (
    "private-name",
    "private-phone",
    "private-email",
    "private/object/key.pdf",
    "quotable evidence",
    "private reasoning",
    "private interview question",
    "private-hard-filter-rule",
    "private-rule-dimension",
)


def _assert_no_seeded_secret(payload: object) -> None:
    rendered = str(payload)
    for secret in SEEDED_SECRETS:
        assert secret not in rendered, f"{secret!r} leaked into {rendered!r}"


async def _service_headers(db_session) -> dict[str, str]:
    user = User(
        dingtalk_userid=f"mcp-{uuid4().hex}",
        display_name="MCP Service",
        role=get_settings().MCP_SERVICE_ROLE,
    )
    db_session.add(user)
    await db_session.commit()
    token = create_access_token({"sub": str(user.id), "role": user.role})
    return {"Authorization": f"Bearer {token}"}


async def _seed_score(db_session) -> tuple[Candidate, Score]:
    code = f"MCP_{uuid4().hex[:6]}"
    jd = JD(code=code, name=code, description="", status="active")
    db_session.add(jd)
    await db_session.flush()
    version = RuleVersion(
        jd_id=jd.id, version="v1", published_at=NOW, schema_json={"jd_code": code}
    )
    db_session.add(version)
    await db_session.flush()
    jd.active_rule_version_id = version.id
    candidate = Candidate(
        source="upload",
        name_cipher=encrypt_pii("private-name"),
        phone_cipher=encrypt_pii("private-phone"),
        email_cipher=encrypt_pii("private-email"),
        raw_file_key="private/object/key.pdf",
        pii_hash=uuid4().hex,
        extracted_json={},
    )
    db_session.add(candidate)
    await db_session.flush()
    score = Score(
        candidate_id=candidate.id,
        jd_id=jd.id,
        rule_version_id=version.id,
        total_score=70,
        grade="L1",
        hard_filter_result={
            "passed": True,
            "unknown_filter_ids": [],
            "audit_entries": [{"rule": "private-hard-filter-rule"}],
        },
        rule_dimensions={"items": [{"id": "years", "note": "private-rule-dimension"}]},
        judge_dimensions={
            "prompt_version": "resume_judge_v1",
            "dimensions": [
                {
                    "id": "independence",
                    "tier": "high",
                    "score": 10,
                    "confidence": 0.9,
                    "evidence_quotes": ["quotable evidence"],
                    "reasoning": "private reasoning",
                    "suggested_interview_questions": ["private interview question"],
                }
            ],
        },
    )
    db_session.add(score)
    await db_session.flush()
    await db_session.commit()
    return candidate, score


async def test_the_service_role_cannot_reach_pii_routes(client, db_session):
    """The three routes design §11.3.2 names, end to end over real HTTP.

    Exhaustive coverage of the route table lives in
    `backend/tests/unit/test_mcp_ceiling.py`, which needs no database; this
    confirms the structural property actually surfaces as a 403 to a caller
    holding a genuine, correctly signed service credential.
    """
    candidate, score = await _seed_score(db_session)
    headers = await _service_headers(db_session)

    # The guarantee is "cannot reach", not "not offered". Even calling the REST
    # routes directly with the service credential must fail.
    assert (
        await client.get(f"/api/v1/candidates/{candidate.id}", headers=headers)
    ).status_code == 403
    assert (
        await client.get(f"/api/v1/candidates/{candidate.id}/raw-file", headers=headers)
    ).status_code == 403
    assert (
        await client.get(
            f"/api/v1/candidates/{candidate.id}/scores/{score.id}", headers=headers
        )
    ).status_code == 403


async def test_the_service_role_cannot_reach_the_aggregate_routes_either(client, db_session):
    """The ceiling is total, not PII-shaped.

    The MCP tools read the service layer directly (design §11.2) and never
    issue an HTTP request, so naming `mcp_service` in a router's role tuple
    would widen the credential's reach with no caller that needs it. `GET
    /api/v1/jds` backs the `list_jds` tool and is still closed to the service
    credential.
    """
    await _seed_score(db_session)
    headers = await _service_headers(db_session)

    assert (await client.get("/api/v1/jds", headers=headers)).status_code == 403


async def test_an_hr_credential_reaches_what_the_service_role_cannot(
    client, db_session, auth_headers
):
    """Positive control: the 403s above come from the role, not a bad token.

    Same URL, same seeded row, a credential differing only in its role — and it
    answers with the decrypted name, confirming the route the service identity
    cannot reach really is a PII-decrypting one.
    """
    candidate, _score = await _seed_score(db_session)
    headers = await auth_headers("hr")

    response = await client.get(f"/api/v1/candidates/{candidate.id}", headers=headers)

    assert response.status_code == 200
    assert response.json()["name"] == "private-name"


async def test_the_configured_token_resolves_the_service_user(db_session):
    from backend.app.mcp.identity import resolve_mcp_user

    settings = get_settings()
    await _service_headers(db_session)

    user = await resolve_mcp_user(db_session, settings.MCP_SERVICE_TOKEN)

    assert user.role == settings.MCP_SERVICE_ROLE


async def test_a_wrong_or_absent_token_is_refused(db_session):
    from backend.app.mcp.identity import McpUnauthorized, resolve_mcp_user

    await _service_headers(db_session)

    for presented in ("", "not-the-token", get_settings().MCP_SERVICE_TOKEN + "x"):
        with pytest.raises(McpUnauthorized):
            await resolve_mcp_user(db_session, presented)


async def test_a_non_ascii_token_is_refused_rather_than_crashing(db_session):
    """A wrong token must always come back as `McpUnauthorized`.

    The token arrives in an HTTP header, so the caller chooses its bytes, and
    `hmac.compare_digest` raises `TypeError` on a `str` holding non-ASCII
    characters. Comparing the encoded forms keeps the refusal on the one path
    the caller is allowed to observe.
    """
    from backend.app.mcp.identity import McpUnauthorized, resolve_mcp_user

    await _service_headers(db_session)

    with pytest.raises(McpUnauthorized):
        await resolve_mcp_user(db_session, "tökèn")


async def test_an_unconfigured_token_authenticates_nobody(db_session, monkeypatch):
    """The shipped default is an empty token; empty must never match empty."""
    from backend.app.mcp.identity import McpUnauthorized, resolve_mcp_user

    await _service_headers(db_session)
    monkeypatch.setattr(get_settings(), "MCP_SERVICE_TOKEN", "")

    with pytest.raises(McpUnauthorized):
        await resolve_mcp_user(db_session, "")


async def test_an_unprovisioned_service_user_is_refused(db_session):
    from backend.app.mcp.identity import McpUnauthorized, resolve_mcp_user

    with pytest.raises(McpUnauthorized):
        await resolve_mcp_user(db_session, get_settings().MCP_SERVICE_TOKEN)


async def test_score_summary_returns_an_exact_field_set(db_session):
    from backend.app.mcp.tools import score_summary

    _candidate, score = await _seed_score(db_session)

    summary = await score_summary(db_session, score_id=score.id)

    assert summary is not None
    # An exact set, not a substring sweep: WP7 learned that scanning for
    # "resume" false-positives on the legitimate value "resume_judge_v1",
    # which `_seed_score` plants in this very payload.
    assert set(summary) == {
        "score_id",
        "jd_code",
        "total_score",
        "grade",
        "hard_filter_rejected",
        "dimensions",
    }
    assert set(summary["dimensions"][0]) == {"id", "tier", "score"}
    assert summary["hard_filter_rejected"] is False
    _assert_no_seeded_secret(summary)


async def test_score_summary_reads_nothing_it_would_have_to_audit(db_session):
    """The tool is not the operator route with fields removed.

    `get_score_detail` returns the quotes and records a `score_detail_read`
    audit row because doing so is a PII event. This reads a projection that
    cannot carry them, so there is nothing to audit — and an absent audit row
    is the observable proof it did not go through that service.
    """
    from backend.app.mcp.tools import score_summary

    _candidate, score = await _seed_score(db_session)
    before = (await db_session.execute(select(func.count()).select_from(AuditLog))).scalar_one()

    await score_summary(db_session, score_id=score.id)

    # Autoflush means an audit row merely *added* to the session would be
    # counted here too, so this catches an uncommitted one as well.
    after = (await db_session.execute(select(func.count()).select_from(AuditLog))).scalar_one()
    assert after == before
    assert await score_summary(db_session, score_id=-1) is None


async def test_top_candidates_exposes_ids_not_identities(db_session):
    from backend.app.mcp.tools import score_summary, top_candidates

    _candidate, score = await _seed_score(db_session)
    summary = await score_summary(db_session, score_id=score.id)
    assert summary is not None

    rows = await top_candidates(db_session, jd_code=str(summary["jd_code"]), n=10, days=7)

    assert rows
    assert set(rows[0]) == {"candidate_id", "total_score", "grade", "scored_at"}
    assert rows[0]["candidate_id"] == score.candidate_id
    _assert_no_seeded_secret(rows)


async def test_list_jds_returns_an_exact_field_set(db_session):
    from backend.app.mcp.tools import list_jds

    await _seed_score(db_session)

    rows = await list_jds(db_session)

    assert rows
    assert set(rows[0]) == {"jd_code", "name", "active_rule_version"}
    assert rows[0]["active_rule_version"] == "v1"
    _assert_no_seeded_secret(rows)


async def test_operations_summary_returns_an_exact_field_set(db_session):
    from backend.app.mcp.tools import operations_summary
    from backend.app.services.operations.reporting import InvalidOperationsWindow

    await _seed_score(db_session)

    summary = await operations_summary(db_session, window="7d")

    assert set(summary) == {"window", "known_cost_cny", "attempt_count", "budgets"}
    assert summary["window"] == "7d"
    # Money is a string on the wire, as it is throughout the WP7 API.
    assert isinstance(summary["known_cost_cny"], str)
    assert summary["budgets"]
    for budget in summary["budgets"]:
        assert set(budget) == {"scope", "state", "spend_cny"}
        assert isinstance(budget["spend_cny"], str)
    _assert_no_seeded_secret(summary)

    with pytest.raises(InvalidOperationsWindow):
        await operations_summary(db_session, window="all-time")
