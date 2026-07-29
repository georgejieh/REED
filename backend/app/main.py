from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

from app.api import include_routes
from app.config.models import RuntimeMode, Settings
from app.runtime.service import RuntimeService
from app.security import (
    SecurityManager,
    configured_values,
    exact_host_middleware,
    security_headers_middleware,
)
from app.secrets.base import SecretStore


def create_app(
    settings: Settings | None = None,
    secret_store: SecretStore | None = None,
) -> FastAPI:
    active_settings = settings or Settings()
    runtime = RuntimeService(active_settings, secret_store=secret_store)
    security = SecurityManager(active_settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        runtime.start()
        try:
            yield
        finally:
            runtime.stop()

    app = FastAPI(title="REED", version="0.1.0", lifespan=lifespan)
    app.state.runtime = runtime
    app.state.settings = active_settings
    app.state.security = security

    @app.exception_handler(RequestValidationError)
    async def safe_request_validation(
        request: Request,
        error: RequestValidationError,
    ):
        if request.url.path.startswith(
            ("/api/auth", "/api/wizard", "/api/admin")
        ):
            return JSONResponse(
                status_code=422,
                content={"detail": "invalid request"},
            )
        return await request_validation_exception_handler(request, error)

    if active_settings.runtime_mode is RuntimeMode.HOSTED:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=list(
                configured_values(active_settings.hosted_allowed_origins)
            ),
            allow_credentials=True,
            allow_methods=["GET", "HEAD", "OPTIONS"],
            allow_headers=["Accept", "Content-Type"],
        )
    app.middleware("http")(exact_host_middleware)
    app.middleware("http")(security_headers_middleware)

    dashboard_path = active_settings.dashboard_path.resolve()
    assets_path = dashboard_path / "assets"
    if assets_path.is_dir():
        app.mount("/assets", StaticFiles(directory=assets_path), name="assets")

    @app.get("/", include_in_schema=False, response_model=None)
    def dashboard(request: Request) -> FileResponse | PlainTextResponse:
        index_path = dashboard_path / "index.html"
        if not index_path.is_file():
            return PlainTextResponse(
                "REED dashboard is not built",
                status_code=503,
            )
        response = FileResponse(index_path)
        response.headers["Cache-Control"] = "no-store"
        if active_settings.runtime_mode is RuntimeMode.LOCAL:
            try:
                security.validate_session(request, csrf=False)
            except HTTPException:
                if (
                    not security.bootstrap_used
                    and security.clock() < security.bootstrap_expires_at
                ):
                    security.issue_bootstrap(
                        response,
                        secure=request.url.scheme == "https",
                    )
        return response

    include_routes(app)
    return app


app = create_app()
