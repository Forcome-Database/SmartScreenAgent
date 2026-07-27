"""The MCP server that publishes the four content-free tools of design §11.2.

Mounted by `create_app` only when `MCP_ENABLED` is set, and refused outright
when the configuration would make that mount either wider or emptier than the
design allows. Nothing here decrypts, scores, or spends money.
"""

from __future__ import annotations

from typing import Any

import structlog
from fastapi import FastAPI
from mcp import types
from mcp.server import Server
from mcp.server.sse import SseServerTransport
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Mount, Route
from starlette.types import ASGIApp, Receive, Scope, Send

from backend.app.config import get_settings
from backend.app.database import AsyncSessionLocal
from backend.app.mcp import tools
from backend.app.mcp.ceiling import routes_admitting_role
from backend.app.mcp.identity import McpUnauthorized, resolve_mcp_user

logger = structlog.get_logger(__name__)


class McpMountRefused(RuntimeError):
    """The configuration would publish a surface the design does not allow."""


class McpToolFailed(RuntimeError):
    """An unexpected tool failure, carrying a message that names nothing."""


# The only unbounded string channel out of this package, and it ends at a
# language model: the SDK catches whatever a handler raises and puts `str(exc)`
# straight into `content[0].text`. A SQLAlchemy `StatementError` carries the
# statement it failed on, and `operations_summary` reaches into the WP7
# reporting service whose exception strings this package does not own. Every
# tool's field set is exact by construction, and this is what stops the error
# text from being the one exception to that.
UNEXPECTED_TOOL_ERROR = "the tool call failed unexpectedly; details are in the server log"


# Argument bounds live in the tool functions, not here. `top_candidates`
# refuses `days < 1` and clamps `n`, and duplicating those numbers in a schema
# would only create a second place to keep them — the copy that drifted being
# the one that stopped refusing. The schemas pin the argument *names* and
# types, which is what a model needs and what nothing else states.
#
# `window` is the exception, and only because its enum is read from the same
# constant the tool validates against: it cannot drift, and a model that has to
# guess window names guesses wrong.
TOOLS: tuple[types.Tool, ...] = (
    types.Tool(
        name="list_jds",
        description="Every job description, with the rule version currently scoring it.",
        inputSchema={"type": "object", "properties": {}, "additionalProperties": False},
    ),
    types.Tool(
        name="top_candidates",
        description=(
            "The best-scoring candidates for one JD over the last `days` days, "
            "identified by candidate id. Names, contact details and resume text "
            "are not available through this server."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "jd_code": {"type": "string"},
                "n": {"type": "integer"},
                "days": {"type": "integer"},
            },
            "required": ["jd_code"],
            "additionalProperties": False,
        },
    ),
    types.Tool(
        name="score_summary",
        description=(
            "One scorecard reduced to what two candidates can be compared on: "
            "total, grade, hard-filter outcome, and each judged dimension's "
            "tier and score. Evidence quotes and reasoning are not available."
        ),
        inputSchema={
            "type": "object",
            "properties": {"score_id": {"type": "integer"}},
            "required": ["score_id"],
            "additionalProperties": False,
        },
    ),
    types.Tool(
        name="operations_summary",
        description="Spend and budget state for one window.",
        inputSchema={
            "type": "object",
            "properties": {"window": {"type": "string", "enum": list(tools.WINDOWS)}},
            "additionalProperties": False,
        },
    ),
)


async def dispatch(db: AsyncSession, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """Translate one tool call into the structured content a client receives.

    Every tool answers with an object, and that is not a stylistic choice: the
    low-level server routes a returned `dict` to `structuredContent` and
    serialises it into the text block beside it, while any other iterable it
    takes for a list of content blocks — which then fails validation and
    reaches the caller as a tool error.

    The two "there is no such thing" answers leave as raised errors rather than
    as data, and the distinction is the whole point of `top_candidates`
    returning `None` separately from `[]`. A model handed `null` or `[]` for a
    code that names no JD reports "there are no top candidates for JD-X" as a
    fact about a JD that does not exist. `[]` — the JD exists and ranks nobody,
    including when it has no active rule version — is a claim about candidates
    and stays data.

    `ValueError` covers the third outcome, a bad argument, in one place:
    `operations_summary` raises `InvalidOperationsWindow`, which subclasses it.
    """
    if name == "list_jds":
        return {"jds": await tools.list_jds(db)}
    if name == "top_candidates":
        jd_code = str(arguments["jd_code"])
        ranked = await tools.top_candidates(
            db,
            jd_code=jd_code,
            n=int(arguments.get("n", 10)),
            days=int(arguments.get("days", 7)),
        )
        if ranked is None:
            raise ValueError(f"no JD with code {jd_code!r}")
        return {"jd_code": jd_code, "candidates": ranked}
    if name == "score_summary":
        score_id = int(arguments["score_id"])
        summary = await tools.score_summary(db, score_id=score_id)
        if summary is None:
            raise ValueError(f"no score with id {score_id}")
        return summary
    if name == "operations_summary":
        return await tools.operations_summary(db, window=str(arguments.get("window", "7d")))
    raise ValueError(f"unknown tool: {name!r}")


def build_mcp_server() -> Server:
    """Register the four content-free tools.

    Each call opens its own short session rather than borrowing a request's:
    the SSE session outlives any one call, and holding a connection open for it
    would pin one per idle client.
    """
    server: Server = Server("smartscreen")

    @server.list_tools()
    async def list_tools() -> list[types.Tool]:
        return list(TOOLS)

    @server.call_tool()
    async def call(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        try:
            async with AsyncSessionLocal() as db:
                return await dispatch(db, name, arguments)
        except ValueError:
            # Deliberate, and the only text allowed out. The three
            # `top_candidates` outcomes are told apart by it — `None` has to
            # arrive as "no JD with code '...'" rather than as a ranking — and
            # `operations_summary`'s `InvalidOperationsWindow` subclasses it.
            # Every one of these is raised in this file or in `tools.py` and
            # quotes back an argument the caller itself supplied.
            raise
        except Exception as exc:
            # Anything else was written by SQLAlchemy, by the WP7 reporting
            # service, or by a library none of this package's field-set
            # guarantees cover. Logged the way `services.sync.runner` logs an
            # abort — an error code and a type name, never the message.
            logger.error(
                "mcp_tool_failed",
                tool=name,
                error_code="tool_failed",
                error_type=type(exc).__name__,
            )
            raise McpToolFailed(UNEXPECTED_TOOL_ERROR) from exc

    return server


class SseEndpoint:
    """The SSE stream, as a raw ASGI app rather than a `request -> response`.

    Starlette treats a non-function endpoint as ASGI and sends nothing once it
    returns. A `request -> response` endpoint — the shape the SDK's own example
    uses — sends its return value, and `connect_sse` has already completed the
    response by then, so the empty `Response()` that example ends with is a
    second `http.response.start`. That is an unhandled ASGI error on every
    client disconnect, surfacing under this app's `AccessLogMiddleware` as a
    bare `AssertionError` from inside Starlette.
    """

    def __init__(self, server: Server, transport: SseServerTransport) -> None:
        self.server = server
        self.transport = transport

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        async with self.transport.connect_sse(scope, receive, send) as (read, write):
            await self.server.run(read, write, self.server.create_initialization_options())


class ServiceTokenGate:
    """Refuse every MCP request that does not present the service token.

    ASGI middleware rather than a route dependency: the SSE endpoint holds its
    response open for the life of the session, so the credential has to be
    checked before the stream opens rather than once per message.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        scheme, _, token = Request(scope).headers.get("authorization", "").partition(" ")
        try:
            if scheme.lower() != "bearer":
                raise McpUnauthorized("missing bearer credential")
            async with AsyncSessionLocal() as db:
                await resolve_mcp_user(db, token.strip())
        except McpUnauthorized:
            await JSONResponse({"detail": "Unauthorized"}, status_code=401)(scope, receive, send)
            return
        await self.app(scope, receive, send)


def build_mcp_app(app: FastAPI) -> Starlette:
    """The SSE ASGI app for `app`, or a refusal to start at all.

    Takes the application because this runs after every router is registered,
    and that is the only moment the first check below is possible:
    `backend/app/config.py` cannot import the routers without a cycle, and a
    `Settings` validator could only compare the configured role against itself.
    Both refusals are `raise`, not `log`, because a configuration typo that
    leaves the process running is a typo nobody finds.
    """
    settings = get_settings()

    admitted = routes_admitting_role(app.routes, settings.MCP_SERVICE_ROLE)
    if admitted:
        raise McpMountRefused(
            f"MCP_SERVICE_ROLE={settings.MCP_SERVICE_ROLE!r} is admitted by "
            f"{len(admitted)} route guard(s) — the service identity would carry "
            f"a human's reach: {', '.join(admitted)}"
        )
    if not settings.MCP_SERVICE_TOKEN:
        raise McpMountRefused(
            "MCP_ENABLED is set with an empty MCP_SERVICE_TOKEN: the server "
            "would be mounted with an identity nobody can ever present."
        )

    transport = SseServerTransport("/messages/")

    return Starlette(
        routes=[
            Route(
                "/sse",
                endpoint=SseEndpoint(build_mcp_server(), transport),
                methods=["GET"],
            ),
            Mount("/messages/", app=transport.handle_post_message),
        ],
        middleware=[Middleware(ServiceTokenGate)],
    )
