from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from financial_ai.api.dependencies import initialize_dependencies, shutdown_dependencies
from financial_ai.api.middleware import (
    APIKeyMiddleware,
    RequestLoggingMiddleware,
    configure_limiter,
    register_exception_handlers,
)
from financial_ai.api.routes import router
from financial_ai.config import get_settings

logger = logging.getLogger(__name__)


# LIFESPAN
@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    settings = get_settings()
    logger.info("Starting %s v%s [%s]", settings.APP_NAME, settings.APP_VERSION, settings.APP_ENV)

    await initialize_dependencies()

    logger.info("Application ready - listening on %s:%d", settings.API_HOST, settings.API_PORT)

    yield

    logger.info("Shutting down %s...", settings.APP_NAME)
    await shutdown_dependencies()
    logger.info("Shutdown complete")


# APPLICATION FACTORY
def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="Financial AI Analyst API",
        description={
            "Production-grade financial analysis using rag OVER sec filings. "
            "Ingest 10-K, 10-Q, and 8-K documents and answers natural language "
            "financial questions grounded in primary source documents"
        },
        version=settings.APP_VERSION,
        docs_url="/docs" if settings.DEBUG else None,
        redoc_url="/redoc" if settings.DEBUG else None,
        openapi_url="/openapi.json" if settings.DEBUG else None,
        lifespan=lifespan,
    )
    # CORS
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.CORS_ORIGINS_LIST,
        allow_credentials=True,
        allow_methods=["GET", "POST"],
        allow_headers=["*"],
    )
    # Request logging
    app.add_middleware(RequestLoggingMiddleware)

    # Exception handling
    register_exception_handlers(app)

    # Rate limiting
    configure_limiter(app)

    # API Key Auth
    app.add_middleware(APIKeyMiddleware)

    # Routes
    app.include_router(router)

    # Root
    @app.get("/", include_in_schema=False)
    async def root() -> dict[str, str]:
        return {
            "service": settings.APP_NAME,
            "version": settings.APP_VERSION,
            "docs": "/docs" if settings.DEBUG else "disabled",
        }

    return app


app = create_app()


# Dev entrypoint
def main() -> None:
    settings = get_settings()
    uvicorn.run(
        "financial_ai.api.server:app",
        host=settings.API_HOST,
        port=settings.API_PORT,
        reload=settings.DEBUG,
        log_level="debug" if settings.DEBUG else "info",
        access_log=True,
    )


if __name__ == "__main__":
    main()
