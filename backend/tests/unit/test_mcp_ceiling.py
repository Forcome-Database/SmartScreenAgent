"""The MCP service role's ceiling, asserted over the whole route table.

These live in the offline gate rather than beside the HTTP tests in
`backend/tests/integration/test_mcp_authorization.py` on purpose: they need no
database, and the accident they exist to catch — someone naming `mcp_service`
in a router's `require_roles` tuple — is one a contributor would otherwise
commit against a green local run.
"""

from __future__ import annotations

from fastapi.routing import APIRoute

from backend.app.config import get_settings
from backend.app.main import app

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


def _api_routes() -> list[APIRoute]:
    return [route for route in app.routes if isinstance(route, APIRoute)]


def _methods(route: APIRoute) -> set[str]:
    return route.methods - {"HEAD", "OPTIONS"}


def _role_guards(route: APIRoute) -> list[frozenset[str]]:
    """Every `require_roles(...)` allow-set reachable from one route.

    Read out of the dependency closure rather than re-derived from the router
    source, so what is asserted is what FastAPI will actually enforce at
    request time — including a guard inherited from a nested dependency.
    """
    guards: list[frozenset[str]] = []
    pending = list(route.dependant.dependencies)
    while pending:
        dependant = pending.pop()
        call = getattr(dependant, "call", None)
        if call is not None and getattr(call, "__qualname__", "").startswith("require_roles."):
            free_vars = call.__code__.co_freevars
            cells = call.__closure__ or ()
            guards.append(frozenset(cells[free_vars.index("allowed")].cell_contents))
        pending.extend(dependant.dependencies)
    return guards


def test_the_service_role_is_named_by_no_route_guard() -> None:
    """The ceiling, over the whole route table rather than a sample of it.

    Three hand-picked 403s would only show that three routes are closed. This
    walks every registered route and fails the moment `mcp_service` is added to
    any `require_roles` tuple anywhere — the accident worth catching, because
    it would widen the MCP surface without touching a single MCP file.
    """
    service_role = get_settings().MCP_SERVICE_ROLE

    reachable = sorted(
        f"{method} {route.path}"
        for route in _api_routes()
        for method in _methods(route)
        if any(service_role in guard for guard in _role_guards(route))
    )

    assert reachable == []


def test_every_route_is_guarded_or_public_by_design() -> None:
    """No route may ship without a decision about who reaches it.

    This is what makes the assertion above exhaustive rather than a sample: an
    unguarded new route fails here, and so does a refactor that stops
    `_role_guards` recognizing the guard — every route would then read as
    unguarded — so the ceiling assertion can never pass vacuously.
    """
    unguarded = {
        (method, route.path)
        for route in _api_routes()
        for method in _methods(route)
        if not _role_guards(route)
    }

    assert unguarded == PUBLIC_BY_DESIGN
