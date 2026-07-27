"""The MCP service role's ceiling, read out of the live route table.

Design §11.3.2: the service identity names no router tuple. The tools read the
service layer directly, so there is no REST route this credential needs, and
any route that admits it is a widening with no caller.

Two places assert that, and they must not drift apart: `build_mcp_app` refuses
to mount when the configured role appears in any guard, and
`backend/tests/unit/test_mcp_ceiling.py` fails the build when a contributor
adds one. Neither is redundant — the test catches the router edit, the mount
catches the `MCP_SERVICE_ROLE` typo the test cannot see, because the test
asserts about whatever role it is configured with. They share this walk so the
one that drifted could not be the one that stopped refusing.
"""

from __future__ import annotations

from collections.abc import Iterable

from fastapi.routing import APIRoute
from starlette.routing import BaseRoute


def api_routes(routes: Iterable[BaseRoute]) -> list[APIRoute]:
    return [route for route in routes if isinstance(route, APIRoute)]


def route_methods(route: APIRoute) -> set[str]:
    return route.methods - {"HEAD", "OPTIONS"}


def role_guards(route: APIRoute) -> list[frozenset[str]]:
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


def routes_admitting_role(routes: Iterable[BaseRoute], role: str) -> list[str]:
    """Every `"METHOD /path"` whose guard admits `role`, sorted.

    Empty is the only acceptable answer for the MCP service role.
    """
    return sorted(
        f"{method} {route.path}"
        for route in api_routes(routes)
        for method in route_methods(route)
        if any(role in guard for guard in role_guards(route))
    )
