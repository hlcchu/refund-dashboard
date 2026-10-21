import logging
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.auth import DevTokenIssuer, entra_validator
from app.config import Settings, get_settings
from app.db import (
    create_schema,
    make_engine,
    make_session_factory,
    managed_identity_token_provider,
)
from app.routers import admin, audit, identity, refunds
from app.seed import seed_demo_data
from app.services import DomainError
from app.telemetry import instrument_app

STATIC_DIR = Path(__file__).parent / "static"

SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Content-Security-Policy": (
        "default-src 'self'; connect-src 'self' https://login.microsoftonline.com; "
        "frame-src https://login.microsoftonline.com; frame-ancestors 'none'; base-uri 'none'"
    ),
    "Cache-Control": "no-store",
}


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    logging.basicConfig(level=logging.INFO)
    # The Azure SDK logs every HTTP call (telemetry export, token fetches) at INFO.
    logging.getLogger("azure").setLevel(logging.WARNING)

    app = FastAPI(
        title="Refund Dashboard API",
        version="0.1.0",
        docs_url="/docs" if settings.environment != "production" else None,
        redoc_url=None,
    )
    app.state.settings = settings

    token_provider = (
        managed_identity_token_provider(settings.sql_managed_identity_client_id)
        if settings.sql_managed_identity_client_id
        else None
    )
    engine = make_engine(settings.database_url, sql_token_provider=token_provider)
    if settings.auto_create_schema:
        create_schema(engine)
    app.state.engine = engine
    app.state.session_factory = make_session_factory(engine)

    if settings.auth_mode == "entra":
        app.state.token_validator = entra_validator(
            settings.entra_tenant_id, settings.entra_api_client_id
        )
    else:
        app.state.dev_issuer = DevTokenIssuer()
        app.state.token_validator = app.state.dev_issuer.validator()
        app.include_router(identity.dev_router)

    app.include_router(identity.router)
    app.include_router(refunds.router)
    app.include_router(audit.router)
    app.include_router(admin.router)

    if settings.seed_demo_data:
        seed_demo_data(app.state.session_factory, settings)

    @app.exception_handler(DomainError)
    async def _domain_error(_request: Request, exc: DomainError) -> JSONResponse:
        return JSONResponse({"detail": exc.message}, status_code=exc.status_code)

    @app.middleware("http")
    async def _security_headers(request: Request, call_next):
        response = await call_next(request)
        for k, v in SECURITY_HEADERS.items():
            response.headers.setdefault(k, v)
        return response

    @app.get("/healthz", include_in_schema=False)
    def healthz() -> dict:
        return {"status": "ok"}

    @app.get("/ui-config", include_in_schema=False)
    def ui_config() -> dict:
        if settings.auth_mode == "dev":
            return {"auth_mode": "dev"}
        return {
            "auth_mode": "entra",
            "tenant_id": settings.entra_tenant_id,
            "client_id": settings.entra_api_client_id,
            "scope": f"api://{settings.entra_api_client_id}/Refunds.Access",
        }

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    instrument_app(app)
    return app
