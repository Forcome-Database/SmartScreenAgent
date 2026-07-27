from __future__ import annotations

import asyncio
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
                },
                # A second dimension, so "field-set exact at every nesting
                # level" is a claim about a list rather than about its head.
                {
                    "id": "ownership",
                    "tier": "medium",
                    "score": 6,
                    "confidence": 0.5,
                    "evidence_quotes": ["quotable evidence"],
                    "reasoning": "private reasoning",
                    "suggested_interview_questions": ["private interview question"],
                },
            ],
        },
    )
    db_session.add(score)
    await db_session.flush()
    await db_session.commit()
    return candidate, score


async def _jd_code(db_session, jd_id: int) -> str:
    return (await db_session.execute(select(JD.code).where(JD.id == jd_id))).scalar_one()


async def _call_tool(name: str, arguments: dict[str, object]):
    """One tool call as a client receives it — not as the tool returns it.

    Goes through the handler the low-level server registers, so what is
    asserted is the `CallToolResult` that leaves the process: the structured
    content, the text block serialised beside it, and the error flag. The tool
    functions' own return values are asserted elsewhere in this file; a
    serialisation layer that flattened `None` into `[]` would pass every one of
    those and still lie to a model.
    """
    from mcp import types

    from backend.app.mcp.server import build_mcp_server

    result = await build_mcp_server().request_handlers[types.CallToolRequest](
        types.CallToolRequest(
            method="tools/call",
            params=types.CallToolRequestParams(name=name, arguments=arguments),
        )
    )
    return result.root


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
    # Every element, not just the head: the seeded payload carries two.
    assert len(summary["dimensions"]) == 2
    for dimension in summary["dimensions"]:
        assert set(dimension) == {"id", "tier", "score"}
    assert summary["hard_filter_rejected"] is False
    _assert_no_seeded_secret(summary)


async def test_score_summary_reads_nothing_it_would_have_to_audit(db_session):
    """The call writes no audit row, and reports a missing score as `None`.

    `get_score_detail` returns the quotes and records a `score_detail_read`
    event because for an operator that read *is* a PII event. This projection
    cannot carry them, so recording one would corrupt the trail with entries
    claiming a scorecard was opened when nothing readable was. That absence is
    what this asserts.

    It is *not* proof the tool avoided `get_score_detail`: that service writes
    its row only `if actor is not None`, so a regression calling it with no
    actor would leave the count unchanged and still pass here. The proof of
    what is read is
    `backend/tests/unit/test_mcp_tools.py::test_the_score_projection_reads_exactly_three_judge_fields`,
    which pins the only statement the tool issues.
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


async def test_a_hard_filter_rejection_summarises_without_its_reasons(db_session):
    """The other branch of `hard_filter_rejected`, and a null judge payload.

    A rejected score is stored with `judge_dimensions` NULL and with the
    filters it failed spelled out beside the flag. The tool reports the flag
    and none of the reasons: which rule rejected a person is an operator's
    business, decided against the resume the tool cannot see.
    """
    from backend.app.mcp.tools import score_summary

    candidate, seeded = await _seed_score(db_session)
    # The seeded score already occupies (candidate, jd, rule version), so the
    # rejection needs a JD of its own.
    other = JD(code=f"MCP_{uuid4().hex[:6]}", name="other", description="", status="active")
    db_session.add(other)
    await db_session.flush()
    rejected = Score(
        candidate_id=candidate.id,
        jd_id=other.id,
        rule_version_id=seeded.rule_version_id,
        total_score=0,
        grade="rejected",
        hard_filter_result={
            "rejected": True,
            "failed_filter_ids": ["age_limit"],
            "audit_entries": [{"rule": "private-hard-filter-rule"}],
        },
        rule_dimensions={},
        judge_dimensions=None,
    )
    db_session.add(rejected)
    await db_session.commit()

    summary = await score_summary(db_session, score_id=rejected.id)

    assert summary is not None
    assert set(summary) == {
        "score_id",
        "jd_code",
        "total_score",
        "grade",
        "hard_filter_rejected",
        "dimensions",
    }
    assert summary["hard_filter_rejected"] is True
    assert summary["dimensions"] == []
    _assert_no_seeded_secret(summary)


async def test_top_candidates_exposes_ids_not_identities(db_session):
    from backend.app.mcp.tools import score_summary, top_candidates

    _candidate, score = await _seed_score(db_session)
    summary = await score_summary(db_session, score_id=score.id)
    assert summary is not None

    rows = await top_candidates(db_session, jd_code=str(summary["jd_code"]), n=10, days=7)

    assert rows
    for row in rows:
        assert set(row) == {"candidate_id", "total_score", "grade", "scored_at"}
    assert rows[0]["candidate_id"] == score.candidate_id
    _assert_no_seeded_secret(rows)


async def test_top_candidates_ranks_only_the_active_rule_version(db_session):
    """A republication must not put one person in two of the `n` slots.

    `uq_scores_candidate_jd_rule` is keyed on the rule version, so publishing a
    rule inside the window leaves the superseded score in place beside the new
    one. Their totals are graded against two different schemas, so ranking them
    against each other means nothing — and here the superseded one is the
    higher, so without the filter it would be presented as the current answer.
    The WP4 ranked list this tool is a view of filters to the active version.
    """
    from backend.app.mcp.tools import top_candidates

    candidate, seeded = await _seed_score(db_session)
    jd = await db_session.get(JD, seeded.jd_id)
    republished = RuleVersion(
        jd_id=jd.id, version="v2", published_at=NOW, schema_json={"jd_code": jd.code}
    )
    db_session.add(republished)
    await db_session.flush()
    jd.active_rule_version_id = republished.id
    db_session.add(
        Score(
            candidate_id=candidate.id,
            jd_id=jd.id,
            rule_version_id=republished.id,
            # Lower than the seeded 70 scored under the retired v1, so an
            # unfiltered query would rank the stale score first.
            total_score=55,
            grade="L2",
            hard_filter_result={},
            rule_dimensions={},
            judge_dimensions=None,
        )
    )
    await db_session.commit()

    rows = await top_candidates(db_session, jd_code=jd.code, n=10, days=7)

    assert rows is not None
    assert len(rows) == 1
    assert rows[0]["candidate_id"] == candidate.id
    assert rows[0]["total_score"] == "55.00"
    assert rows[0]["grade"] == "L2"


async def test_top_candidates_separates_an_unknown_jd_from_an_unranked_one(db_session):
    """`None` and `[]` are different answers and must stay different.

    A model handed `[]` for a misspelled code states "there are no top
    candidates for JD-X" as a fact about a JD that does not exist. `None` is
    the not-found signal `score_summary` and `list_ranked_for_jd` already use.
    A JD with no active rule version ranks nobody *yet*, which is `[]` — the
    same answer WP4 gives it.
    """
    from backend.app.mcp.tools import top_candidates

    await _seed_score(db_session)
    scored_but_empty = JD(
        code=f"MCP_{uuid4().hex[:6]}", name="empty", description="", status="active"
    )
    db_session.add(scored_but_empty)
    await db_session.flush()
    version = RuleVersion(
        jd_id=scored_but_empty.id, version="v1", published_at=NOW, schema_json={}
    )
    db_session.add(version)
    await db_session.flush()
    scored_but_empty.active_rule_version_id = version.id
    unpublished = JD(
        code=f"MCP_{uuid4().hex[:6]}", name="unpublished", description="", status="active"
    )
    db_session.add(unpublished)
    await db_session.commit()

    assert await top_candidates(db_session, jd_code="MCP_NO_SUCH_JD") is None
    assert await top_candidates(db_session, jd_code=scored_but_empty.code) == []
    assert await top_candidates(db_session, jd_code=unpublished.code) == []


async def test_top_candidates_refuses_a_window_that_can_contain_nothing(db_session):
    """`days=0` silently returning `[]` is the unknown-JD lie from the other side."""
    from backend.app.mcp.tools import top_candidates

    _candidate, seeded = await _seed_score(db_session)
    jd_code = await _jd_code(db_session, seeded.jd_id)

    for days in (0, -1):
        with pytest.raises(ValueError):
            await top_candidates(db_session, jd_code=jd_code, days=days)


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


async def test_mcp_is_not_mounted_while_disabled(client):
    from backend.app.config import get_settings

    assert get_settings().MCP_ENABLED is False

    # Disabled must mean absent, not merely guarded: an unmounted route cannot
    # be reached by a misconfigured proxy either.
    response = await client.post("/mcp", json={})

    assert response.status_code == 404


async def test_an_unknown_jd_leaves_as_a_refusal_not_as_an_empty_ranking(db_session):
    """The whole point of `top_candidates` returning `None`, kept intact on the wire.

    A model handed `null` or `[]` for a code that names no JD reports "there
    are no top candidates for JD-X" as a fact about a JD that does not exist.
    So this outcome leaves as an error carrying the code it could not find,
    which is a claim about the JD and not about any candidate.
    """
    await _seed_score(db_session)

    result = await _call_tool("top_candidates", {"jd_code": "MCP_NO_SUCH_JD"})

    assert result.isError is True
    assert result.structuredContent is None
    assert result.content[0].text == "no JD with code 'MCP_NO_SUCH_JD'"


async def test_a_jd_that_ranks_nobody_leaves_as_an_empty_ranking(db_session):
    """The other of the three outcomes: the JD exists and ranks nobody.

    This one *is* a claim about candidates, and is answered as data.
    """
    await _seed_score(db_session)
    unpublished = JD(
        code=f"MCP_{uuid4().hex[:6]}", name="unpublished", description="", status="active"
    )
    db_session.add(unpublished)
    await db_session.commit()

    result = await _call_tool("top_candidates", {"jd_code": unpublished.code})

    assert result.isError is False
    assert result.structuredContent == {"jd_code": unpublished.code, "candidates": []}


async def test_a_window_that_can_contain_nothing_leaves_as_a_refusal(db_session):
    """The third outcome: a bad argument, told apart from both of the above."""
    _candidate, seeded = await _seed_score(db_session)
    jd_code = await _jd_code(db_session, seeded.jd_id)

    result = await _call_tool("top_candidates", {"jd_code": jd_code, "days": 0})

    assert result.isError is True
    assert result.content[0].text == "days must be at least 1, got 0"


async def test_the_top_candidates_wire_payload_carries_ids_and_nothing_else(db_session):
    """The exact field set of what goes on the wire, not of what the tool returned.

    `result.model_dump()` covers the text block too — the serialisation a model
    actually reads — so a widening that only reached the unstructured content
    would fail here.
    """
    _candidate, score = await _seed_score(db_session)
    jd_code = await _jd_code(db_session, score.jd_id)

    result = await _call_tool("top_candidates", {"jd_code": jd_code, "n": 10, "days": 7})

    assert result.isError is False
    assert set(result.structuredContent) == {"jd_code", "candidates"}
    assert result.structuredContent["candidates"]
    for row in result.structuredContent["candidates"]:
        assert set(row) == {"candidate_id", "total_score", "grade", "scored_at"}
    _assert_no_seeded_secret(result.model_dump())


async def test_the_score_summary_wire_payload_carries_no_evidence(db_session):
    _candidate, score = await _seed_score(db_session)

    result = await _call_tool("score_summary", {"score_id": score.id})

    assert result.isError is False
    assert set(result.structuredContent) == {
        "score_id",
        "jd_code",
        "total_score",
        "grade",
        "hard_filter_rejected",
        "dimensions",
    }
    assert len(result.structuredContent["dimensions"]) == 2
    for dimension in result.structuredContent["dimensions"]:
        assert set(dimension) == {"id", "tier", "score"}
    _assert_no_seeded_secret(result.model_dump())


async def test_a_missing_score_leaves_as_a_refusal_not_as_an_empty_scorecard(db_session):
    """`score_summary`'s `None` has the same failure mode as `top_candidates`'s."""
    await _seed_score(db_session)

    result = await _call_tool("score_summary", {"score_id": -1})

    assert result.isError is True
    assert result.content[0].text == "no score with id -1"


async def test_the_list_jds_wire_payload_is_an_object_rather_than_a_bare_list(db_session):
    """Every tool answers with an object, and it is not a stylistic choice.

    The low-level server routes a returned `dict` to `structuredContent` and
    serialises it into the text block beside it. Any other iterable it takes
    for a list of content blocks, which then fails model validation and is
    reported to the caller as a tool error — so a bare list would turn a
    working tool into a permanent failure.
    """
    await _seed_score(db_session)

    result = await _call_tool("list_jds", {})

    assert result.isError is False
    assert set(result.structuredContent) == {"jds"}
    assert result.structuredContent["jds"]
    for row in result.structuredContent["jds"]:
        assert set(row) == {"jd_code", "name", "active_rule_version"}
    _assert_no_seeded_secret(result.model_dump())


async def test_the_operations_summary_wire_payload_keeps_money_a_string(db_session):
    await _seed_score(db_session)

    result = await _call_tool("operations_summary", {"window": "7d"})

    assert result.isError is False
    assert set(result.structuredContent) == {
        "window",
        "known_cost_cny",
        "attempt_count",
        "budgets",
    }
    assert isinstance(result.structuredContent["known_cost_cny"], str)
    # Non-empty first, as its three siblings do: a seed that yielded no budgets
    # would leave the per-budget field-set assertion below running zero times
    # and this test green while asserting nothing about a budget at all.
    assert result.structuredContent["budgets"]
    for budget in result.structuredContent["budgets"]:
        assert set(budget) == {"scope", "state", "spend_cny"}
    _assert_no_seeded_secret(result.model_dump())


async def test_an_unknown_tool_name_is_refused(db_session):
    result = await _call_tool("decrypt_candidate", {})

    assert result.isError is True
    assert result.content[0].text == "unknown tool: 'decrypt_candidate'"


async def test_a_finished_sse_session_completes_its_response_exactly_once(
    db_session, monkeypatch
):
    """The SSE endpoint is ASGI-shaped, and that is not a matter of style.

    `connect_sse` has already completed the HTTP response by the time it
    returns, so a `request -> response` endpoint — the shape the SDK's own
    example uses, ending in an empty `Response()` — emits a second
    `http.response.start` after it. That is an unhandled ASGI error on every
    client disconnect, and under this app's `AccessLogMiddleware` it surfaces
    as a bare `AssertionError` from inside Starlette rather than as anything
    naming MCP. Driven here as raw ASGI because the duplicate is a protocol
    event, invisible to an HTTP client that has already gone away.
    """
    from backend.app.main import create_app

    await _service_headers(db_session)
    monkeypatch.setattr(get_settings(), "MCP_ENABLED", True)
    app = create_app()
    sent: list[dict] = []

    async def receive() -> dict[str, str]:
        return {"type": "http.disconnect"}

    async def send(message) -> None:
        sent.append(message)

    await asyncio.wait_for(
        app(
            {
                "type": "http",
                "asgi": {"version": "3.0", "spec_version": "2.3"},
                "http_version": "1.1",
                "method": "GET",
                "path": "/mcp/sse",
                "raw_path": b"/mcp/sse",
                "root_path": "",
                "scheme": "http",
                "query_string": b"",
                "headers": [
                    (b"host", b"testserver"),
                    (b"authorization", f"Bearer {get_settings().MCP_SERVICE_TOKEN}".encode()),
                ],
                "client": ("127.0.0.1", 1234),
                "server": ("testserver", 80),
            },
            receive,
            send,
        ),
        timeout=30,
    )

    starts = [message for message in sent if message["type"] == "http.response.start"]
    assert len(starts) == 1
    # The status, not just the count. `ServiceTokenGate` answers 401 with
    # exactly one `http.response.start` too, so counting alone would go green
    # having never opened a stream — and the service user is found by role with
    # `.first()`, so a seeding change is enough to make the bearer stop
    # resolving. The defect this test exists to catch would return unnoticed.
    assert starts[0]["status"] == 200
