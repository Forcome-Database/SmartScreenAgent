import time
import uuid

import structlog
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

logger = structlog.get_logger("access")


class AccessLogMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        trace_id = request.headers.get("x-trace-id", uuid.uuid4().hex[:16])
        start = time.perf_counter()
        structlog.contextvars.bind_contextvars(trace_id=trace_id)
        try:
            response: Response = await call_next(request)
        except Exception:
            elapsed_ms = (time.perf_counter() - start) * 1000
            logger.error(
                "request_error",
                method=request.method,
                path=request.url.path,
                elapsed_ms=round(elapsed_ms, 2),
            )
            raise
        elapsed_ms = (time.perf_counter() - start) * 1000
        logger.info(
            "request",
            method=request.method,
            path=request.url.path,
            status=response.status_code,
            elapsed_ms=round(elapsed_ms, 2),
        )
        response.headers["x-trace-id"] = trace_id
        structlog.contextvars.clear_contextvars()
        return response
