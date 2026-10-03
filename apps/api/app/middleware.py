"""Request-ID middleware with structured logging."""

from __future__ import annotations

import json
import logging
import time
import uuid

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

log = logging.getLogger("app.requests")


class RequestIDMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        request_id = request.headers.get("x-request-id") or uuid.uuid4().hex[:16]
        start = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            # M5: unhandled errors stay typed JSON, keep request-id correlation,
            # and never leak internals.
            log.exception(
                json.dumps({"request_id": request_id, "event": "unhandled_error"})
            )
            return JSONResponse(
                status_code=500,
                content={"detail": "internal error"},
                headers={"x-request-id": request_id},
            )
        elapsed_ms = (time.perf_counter() - start) * 1000.0
        response.headers["x-request-id"] = request_id
        log.info(
            json.dumps(
                {
                    "request_id": request_id,
                    "method": request.method,
                    "path": request.url.path,
                    "status": response.status_code,
                    "duration_ms": round(elapsed_ms, 2),
                }
            )
        )
        return response
