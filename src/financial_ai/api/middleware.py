from __future__ import annotations

import logging
import time
import uuid

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import Response

from financial_ai.config import get_settings

logger = logging.getLogger(__name__)

limiter = Limiter(key_func=get_remote_address, default_limits=["100/minute"])


def configure_limiter(app: FastAPI) -> None:
    settings = get_settings()
    storage_url: str | None = None

    if settings.APP_ENV == "production" and settings.REDIS_URL.get_secret_value():
        storage_url = settings.REDIS_URL.get_secret_value()
        logger.info("Rate limiter: Redis backend at %s", settings.REDIS_URL)
    else:
        logger.info("Rate limiter: in-memory backend (dev/test)")

    global limiter
    limiter = Limiter(
        key_func=get_remote_address, default_limits=["100/minute"], storage_uri=storage_url
    )
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)


# Request logging middleware


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        request_id = str(uuid.uuid4())
        t0 = time.monotonic()
        request.state.request_id = request_id

        logger.info(
            "request_start",
            extra={
                "request_id": request_id,
                "method": request.method,
                "path": request.url.path,
                "client_ip": request.client.host if request.client else "unknown",
            },
        )

        try:
            response = await call_next(request)
        except Exception as exc:
            logger.error("request_error", extra={"request_id": request_id, "error": str(exc)})
            raise

        process_ms = int((time.monotonic() - t0) * 1000)
        response.headers["X-Request-ID"] = request_id
        response.headers["X-Process-Time"] = f"{process_ms}ms"

        logger.info(
            "request_complete",
            extra={
                "request_id": request_id,
                "status_code": response.status_code,
                "process_ms": process_ms,
            },
        )
        return response


# API Key Authentication Middleware


class APIKeyMiddleware(BaseHTTPMiddleware):
    EXEMPT_PATHS = frozenset({"/health", "/docs", "/redoc", "/openapi.json", "/"})

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        settings = get_settings()
        if not settings.API_KEY_ENABLED or request.url.path in self.EXEMPT_PATHS:
            return await call_next(request)

        api_key = request.headers.get("X-API-Key")
        expected = settings.API_KEY.get_secret_value() if settings.API_KEY else None

        if not expected:
            return await call_next(request)

        if api_key != expected:
            return JSONResponse(
                status_code=status.HTTP_401_UNAUTHORIZED,
                content={
                    "type": "about:blank",
                    "title": "Unauthorized",
                    "status": 401,
                    "detail": "Invalid or missing API key. Pass X-API-Key header.",
                    "Instance": request.url.path,
                },
            )
        return await call_next(request)


# Exception handlers
def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        request_id = getattr(request.state, "request_id", "unknown")
        logger.error(
            "unhandled_exception",
            extra={"request_id": request_id, "error": str(exc)},
            exc_info=True,
        )
        return JSONResponse(
            status_code=500,
            content={
                "type": "about:blank",
                "title": "Internal Server Error",
                "status": 500,
                "detail": "An unexpected error occurred.",
                "Instance": request.url.path,
                "request_id": request_id,
            },
        )

    @app.exception_handler(ValueError)
    async def value_error_handler(request: Request, exc: ValueError) -> JSONResponse:
        request_id = getattr(request.state, "request_id", "unknown")
        return JSONResponse(
            status_code=422,
            content={
                "type": "about:blank",
                "title": "Validation Error",
                "status": 422,
                "detail": str(exc),
                "Instance": request.url.path,
                "request_id": request_id,
            },
        )
