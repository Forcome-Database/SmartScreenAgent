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

from fastapi.routing import APIRoute

from backend.app.config import get_settings
from backend.app.deps import get_current_user
from backend.app.main import app
from backend.app.mcp.ceiling import api_routes, role_guards, route_methods, routes_admitting_role

# The only routes that carry no `require_roles` guard, each a deliberate
# choice: the service banner, the unauthenticated login exchange that mints the
# very first token, and the infrastructure probe. None of them reads candidate
# data. Pinning the set is what keeps the ceiling exhaustive — a new unguarded
# route cannot appear unnoticed.
#
# Named for what the test actually computes. Called PUBLIC_BY_DESIGN it named a
# set nobody was checking: the walk below collects routes with no ROLE guard,
# which is not the same as public. The day a route ships guarded only by
# `Depends(get_current_user)`, the exhaustiveness assertion fails and the path
# of least resistance is to append it here — parking an authenticated route in
# a list whose name promises it is public, and opening it to the MCP service
# credential, which authenticates like any other user and simply has no role
# any guard names. `test_the_unguarded_routes_are_also_unauthenticated` closes
# that door, so this set can only ever hold genuinely open routes.
UNGUARDED_BY_DESIGN = {
    ("GET", "/"),
    ("POST", "/auth/dingtalk/login"),
    ("GET", "/healthz"),
}


def _dependency_calls(route: APIRoute) -> set[object]:
    """Every callable in one route's dependency closure.

    The same walk `role_guards` does, kept to the same standard: read out of
    what FastAPI will actually resolve at request time, including a dependency
    inherited from a nested one, rather than re-derived from the router source.
    """
    calls: set[object] = set()
    pending = list(route.dependant.dependencies)
    while pending:
        dependant = pending.pop()
        call = getattr(dependant, "call", None)
        if call is not None:
            calls.add(call)
        pending.extend(dependant.dependencies)
    return calls


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

    assert unguarded == UNGUARDED_BY_DESIGN


def test_the_unguarded_routes_are_also_unauthenticated() -> None:
    """Nothing merely authenticated may be parked in the unguarded set.

    The test above computes "carries no `require_roles`", which is weaker than
    "public". A route guarded only by `Depends(get_current_user)` fails it, and
    the cheapest way to make it pass is to add the route to
    `UNGUARDED_BY_DESIGN` — which would hand it to the MCP service credential,
    because that identity is a real authenticated user whose role no guard
    names. Asserting the absence of `get_current_user` as well means the only
    routes that can live in that set are ones nobody needs a token to reach.
    """
    authenticated_but_unguarded = {
        (method, route.path)
        for route in api_routes(app.routes)
        for method in route_methods(route)
        if not role_guards(route) and get_current_user in _dependency_calls(route)
    }

    assert authenticated_but_unguarded == set()
