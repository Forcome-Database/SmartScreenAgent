"""The MCP service role's ceiling, asserted over the whole route table.

These live in the offline gate rather than beside the HTTP tests in
`backend/tests/integration/test_mcp_authorization.py` on purpose: they need no
database, and the accident they exist to catch — someone naming `mcp_service`
in a router's `require_roles` tuple — is one a contributor would otherwise
commit against a green local run.

The walk itself lives in `backend/app/mcp/ceiling.py`, because `build_mcp_app`
enforces the same property at startup and catches the accident these tests
cannot: they assert about whatever `MCP_SERVICE_ROLE` they are handed, so
`MCP_SERVICE_ROLE=hr` in production collapses the ceiling with this file still
green.
"""

from __future__ import annotations

from backend.app.config import get_settings
from backend.app.main import app
from backend.app.mcp.ceiling import api_routes, role_guards, route_methods, routes_admitting_role

# The only routes that carry no `require_roles` guard, each a deliberate
# choice: the service banner, the unauthenticated login exchange that mints the
# very first token, and the infrastructure probe. None of them reads candidate
# data. Pinning the set is what keeps the ceiling exhaustive — a new unguarded
# route cannot appear unnoticed.
PUBLIC_BY_DESIGN = {
    ("GET", "/"),
    ("POST", "/auth/dingtalk/login"),
    ("GET", "/healthz"),
}


def test_the_service_role_is_named_by_no_route_guard() -> None:
    """The ceiling, over the whole route table rather than a sample of it.

    Three hand-picked 403s would only show that three routes are closed. This
    walks every registered route and fails the moment `mcp_service` is added to
    any `require_roles` tuple anywhere — the accident worth catching, because
    it would widen the MCP surface without touching a single MCP file.
    """
    service_role = get_settings().MCP_SERVICE_ROLE

    assert routes_admitting_role(app.routes, service_role) == []


def test_every_route_is_guarded_or_public_by_design() -> None:
    """No route may ship without a decision about who reaches it.

    This is what makes the assertion above exhaustive rather than a sample: an
    unguarded new route fails here, and so does a refactor that stops
    `role_guards` recognizing the guard — every route would then read as
    unguarded — so the ceiling assertion can never pass vacuously.
    """
    unguarded = {
        (method, route.path)
        for route in api_routes(app.routes)
        for method in route_methods(route)
        if not role_guards(route)
    }

    assert unguarded == PUBLIC_BY_DESIGN
