"""The mount, and the two configurations it has to refuse.

`build_mcp_app` runs after `create_app` has registered every router, and that
is what makes it the only place these checks can live: `backend/app/config.py`
cannot import the routers to inspect them without an import cycle, and a
`Settings` validator could only compare the configured role against itself.
`backend/tests/unit/test_mcp_ceiling.py` has the same blind spot — it asserts
about whatever `MCP_SERVICE_ROLE` it is handed, so `MCP_SERVICE_ROLE=hr` in
production collapses the whole ceiling with every one of those tests still
green. This file is where that typo becomes a startup failure.

Offline by construction: nothing here reaches the database. A refused token is
refused by `hmac.compare_digest` before any query is issued, so the 401s below
are as deterministic as the mount assertions.
"""

from __future__ import annotations

import httpx
import pytest
from mcp import types
from starlette.applications import Starlette

from backend.app.config import get_settings
from backend.app.main import create_app
from backend.app.mcp.server import McpMountRefused, build_mcp_app, build_mcp_server


@pytest.fixture
def enabled(monkeypatch):
    """The switch on, configured the way a working deployment would be."""
    settings = get_settings()
    monkeypatch.setattr(settings, "MCP_ENABLED", True)
    monkeypatch.setattr(settings, "MCP_SERVICE_TOKEN", "test-mcp-token")
    return settings


def test_the_enabled_switch_mounts_the_server(enabled) -> None:
    mounted = {getattr(route, "path", None) for route in create_app().routes}

    assert "/mcp" in mounted


def test_the_disabled_switch_mounts_nothing(monkeypatch) -> None:
    """Disabled means absent, not guarded — the same claim the HTTP test in
    `backend/tests/integration/test_mcp_authorization.py` makes over the wire,
    stated here where it costs no database."""
    monkeypatch.setattr(get_settings(), "MCP_ENABLED", False)

    mounted = {getattr(route, "path", None) for route in create_app().routes}

    assert "/mcp" not in mounted


async def test_a_request_without_the_service_token_is_refused(enabled) -> None:
    """Both endpoints, on a missing credential and on a wrong one.

    The gate is ASGI middleware rather than a route dependency because the SSE
    endpoint holds its response open for the life of the session: the check has
    to happen before the stream opens, not once per message.
    """
    transport = httpx.ASGITransport(app=create_app())
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        assert (await client.get("/mcp/sse")).status_code == 401
        assert (
            await client.get("/mcp/sse", headers={"Authorization": "Bearer wrong"})
        ).status_code == 401
        assert (
            await client.post("/mcp/messages/", params={"session_id": "0" * 32}, json={})
        ).status_code == 401
        # A token in the wrong scheme is still no token.
        assert (
            await client.get("/mcp/sse", headers={"Authorization": "test-mcp-token"})
        ).status_code == 401


def test_the_mount_is_refused_when_the_service_role_names_a_route_guard(
    enabled, monkeypatch
) -> None:
    """`MCP_SERVICE_ROLE=hr` must crash the process, not quietly widen it.

    The refusal covers every `require_roles` allow-set, not only the
    PII-decrypting ones: `GET /api/v1/jds` backs the `list_jds` tool and reads
    no candidate data at all, and it still has to be named here — the tools
    read the service layer directly, so no route belongs to this credential.
    """
    monkeypatch.setattr(get_settings(), "MCP_SERVICE_ROLE", "hr")

    with pytest.raises(McpMountRefused) as refusal:
        create_app()

    message = str(refusal.value)
    assert "hr" in message
    assert "GET /api/v1/candidates/{candidate_id}" in message
    assert "GET /api/v1/jds" in message


def test_the_mount_is_refused_when_the_switch_is_on_without_a_token(
    enabled, monkeypatch
) -> None:
    """Fail-closed is not the same as usable.

    An empty token authenticates nobody, so the surface would be mounted,
    reachable by a proxy, and permanently useless. That is a configuration
    mistake and has to look like one.
    """
    monkeypatch.setattr(get_settings(), "MCP_SERVICE_TOKEN", "")

    with pytest.raises(McpMountRefused) as refusal:
        create_app()

    assert "MCP_SERVICE_TOKEN" in str(refusal.value)


def test_a_correctly_configured_app_is_accepted(enabled) -> None:
    """The positive control for both refusals above: the shipped route table
    and the shipped role produce an app rather than an exception."""
    assert isinstance(build_mcp_app(create_app()), Starlette)


async def test_the_server_publishes_exactly_the_four_content_free_tools() -> None:
    result = await build_mcp_server().request_handlers[types.ListToolsRequest](None)

    assert [tool.name for tool in result.root.tools] == [
        "list_jds",
        "top_candidates",
        "score_summary",
        "operations_summary",
    ]
