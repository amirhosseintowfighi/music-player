from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from redis.asyncio import Redis

from app.api.deps import maintenance_gate
from app.api.middleware import RequestContextMiddleware
from app.api.routers import (
    admin,
    auth,
    channels,
    discover,
    health,
    internal,
    library,
    me,
    payments,
    playlists,
    search,
    social,
    stream,
    telegram,
)
from app.api.state import AppState
from app.config import Settings, get_settings
from app.db import make_engine, make_sessionmaker
from app.errors import AppError
from app.services.meili import MeiliClient
from tmusic_common.logging import configure_logging, get_logger

log = get_logger(__name__)


def build_state(settings: Settings) -> AppState:
    engine = make_engine(settings)
    http = httpx.AsyncClient(timeout=10.0)
    return AppState(
        settings=settings,
        engine=engine,
        sessionmaker=make_sessionmaker(engine),
        redis=Redis.from_url(settings.redis_url, decode_responses=True),
        http=http,
        meili=MeiliClient(
            http,
            settings.meili_url,
            settings.meili_api_key.get_secret_value(),
            settings.meili_index,
        ),
    )


def create_app(settings: Settings | None = None, state: AppState | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging("api", settings.log_level, settings.log_json)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        owned = state is None
        app.state.app = state or build_state(settings)
        await telegram.startup(app)
        try:
            yield
        finally:
            await telegram.shutdown(app)
            if owned:
                s: AppState = app.state.app
                await s.http.aclose()
                await s.redis.aclose()
                await s.engine.dispose()

    app = FastAPI(
        title="Telegram Music API",
        version="0.1.0",
        lifespan=lifespan,
        docs_url="/docs" if settings.env != "prod" else None,
        redoc_url=None,
        openapi_url="/openapi.json" if settings.env != "prod" else None,
        # One gate for the whole app: user traffic stops during maintenance, admin
        # and health endpoints keep working so the operator can fix things.
        dependencies=[Depends(maintenance_gate)],
    )
    if state is not None:
        app.state.app = state

    @app.exception_handler(AppError)
    async def app_error(_: Request, exc: AppError) -> JSONResponse:
        headers = {}
        if "retry_after" in exc.details:
            headers["Retry-After"] = str(exc.details["retry_after"])
        return JSONResponse(
            status_code=exc.status,
            content={"error": {"code": exc.code, "message": exc.message, "details": exc.details}},
            headers=headers,
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        errors = [
            {"loc": list(e.get("loc", ())), "msg": str(e.get("msg", "")), "type": e.get("type")}
            for e in exc.errors()
        ]
        return JSONResponse(
            status_code=422,
            content={
                "error": {
                    "code": "invalid_input",
                    "message": "request validation failed",
                    "details": {"errors": errors},
                }
            },
        )

    @app.exception_handler(Exception)
    async def unhandled(_: Request, exc: Exception) -> JSONResponse:
        log.exception("http.unhandled_error", error_type=type(exc).__name__)
        return JSONResponse(
            status_code=500,
            content={"error": {"code": "internal", "message": "internal error", "details": {}}},
        )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE"],
        allow_headers=["Authorization", "Content-Type", "X-Request-ID"],
        expose_headers=["X-Request-ID"],
        max_age=600,
    )
    app.add_middleware(RequestContextMiddleware)

    for module in (
        health,
        auth,
        admin,
        channels,
        discover,
        library,
        me,
        payments,
        playlists,
        search,
        social,
        stream,
        internal,
        telegram,
    ):
        app.include_router(module.router)
    return app
