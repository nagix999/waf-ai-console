from contextlib import asynccontextmanager
from copy import deepcopy

from fastapi import FastAPI, HTTPException, Request
from fastapi.openapi.utils import get_openapi
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from starlette.middleware.sessions import SessionMiddleware

from .api.analyses import router as analyses_router
from .api.analysis_exports import router as analysis_exports_router
from .api.auth import router as auth_router
from .api.dashboard import router as dashboard_router
from .api.model_profiles import router as model_profiles_router
from .api.evaluation_labels import router as evaluation_labels_router
from .api.prompt_policies import router as prompt_policies_router
from .api.internal_egress import router as internal_egress_router
from .api.service_api_keys import router as service_api_keys_router
from .api.test_runs import router as test_runs_router
from .api.test_comparisons import router as test_comparisons_router
from .api.input_schemas import router as input_schemas_router
from .api.production_api import router as production_api_router
from .config import Settings, get_settings
from .browser_security import BrowserSecurityMiddleware
from .database import Base, build_engine, build_session_factory
from .services.crypto import CryptoService
from .services.input_schemas import InputSchemaError
from .services.production_api import active_contract


class ValidationIssue(BaseModel):
    field: str
    type: str
    message: str


class APIErrorResponse(BaseModel):
    detail: str | list[ValidationIssue]


ANALYSIS_ERROR_RESPONSES = {
    401: {"model": APIErrorResponse, "description": "Missing or invalid service key / session"},
    403: {"model": APIErrorResponse, "description": "Required scope unavailable"},
    404: {"model": APIErrorResponse, "description": "Analysis missing or outside the caller's source system"},
    409: {"model": APIErrorResponse, "description": "Event or review identity reused with conflicting content"},
    413: {"model": APIErrorResponse, "description": "Payload or upload size limit exceeded"},
    422: {"model": APIErrorResponse, "description": "Invalid fields, query range, or upload format; raw input omitted"},
}


def create_app(settings: Settings | None = None, create_schema: bool = False) -> FastAPI:
    settings = settings or get_settings()
    engine = build_engine(settings.database_url)
    session_factory = build_session_factory(engine)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        if create_schema:
            Base.metadata.create_all(engine)
        yield
        engine.dispose()

    app = FastAPI(
        title=settings.app_name,
        version="0.2.0",
        description=(
            "WAF analysis ingestion and polling API. Service clients use X-API-Key. "
            "POST /api/v1/analyses creates production analyses; administrator-only "
            "test endpoints create test analyses. Use the returned id for polling and "
            "check status (pending/processing/completed/failed), not only HTTP status. "
            "Detailed integration contract: docs/Production_API_v0.1.md in the project."
        ),
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.engine = engine
    app.state.session_factory = session_factory
    app.state.crypto = CryptoService(settings.data_encryption_key, settings.encryption_key_version)
    app.add_middleware(BrowserSecurityMiddleware, public_origin=settings.public_origin)
    # Added last so the signed session is available to browser-write protection.
    app.add_middleware(
        SessionMiddleware,
        secret_key=settings.session_secret,
        max_age=settings.session_max_age_seconds,
        same_site="strict",
        https_only=settings.session_https_only,
        session_cookie="__Host-waf_session" if settings.session_https_only else "session",
    )
    app.include_router(auth_router, prefix="/api/v1")
    app.include_router(analyses_router, prefix="/api/v1", responses=ANALYSIS_ERROR_RESPONSES)
    app.include_router(analysis_exports_router, prefix="/api/v1")
    app.include_router(dashboard_router, prefix="/api/v1", responses=ANALYSIS_ERROR_RESPONSES)
    app.include_router(model_profiles_router, prefix="/api/v1")
    app.include_router(evaluation_labels_router, prefix="/api/v1", responses=ANALYSIS_ERROR_RESPONSES)
    app.include_router(prompt_policies_router, prefix="/api/v1")
    app.include_router(internal_egress_router, prefix="/api/v1")
    app.include_router(service_api_keys_router, prefix="/api/v1")
    app.include_router(test_runs_router, prefix="/api/v1", responses=ANALYSIS_ERROR_RESPONSES)
    app.include_router(test_comparisons_router, prefix="/api/v1", responses=ANALYSIS_ERROR_RESPONSES)
    app.include_router(input_schemas_router, prefix="/api/v1")
    app.include_router(production_api_router, prefix="/api/v1")

    def live_openapi():
        # Cache only the static route contracts, never the active definition.
        # Separate API processes and rollbacks see the same database state.
        if app.openapi_schema is None:
            app.openapi_schema = get_openapi(title=app.title, version=app.version,
                openapi_version=app.openapi_version, description=app.description, routes=app.routes)
        with session_factory() as db:
            try:
                metadata, _fields, definition = active_contract(db, app.state.crypto)
                db.commit()
            except InputSchemaError as exc:
                db.rollback()
                raise HTTPException(exc.status_code, exc.code) from None
        document = deepcopy(app.openapi_schema)
        document["components"]["schemas"]["AnalysisInput"] = {"title": "AnalysisInput", **definition,
            "x-input-schema": metadata, "x-payload-max-bytes": settings.payload_max_bytes}
        document["info"]["x-input-schema"] = metadata
        return document

    app.openapi = live_openapi

    @app.middleware("http")
    async def no_stale_openapi(request: Request, call_next):
        response = await call_next(request)
        if request.url.path == app.openapi_url:
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(RequestValidationError)
    async def sanitized_validation_error(_request: Request, exc: RequestValidationError) -> JSONResponse:
        details = [
            {
                "field": ".".join(str(part) for part in error["loc"]),
                "type": error["type"],
                "message": error["msg"],
            }
            for error in exc.errors()
        ]
        return JSONResponse(status_code=422, content={"detail": details})

    @app.get("/health/live", tags=["health"])
    def live() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/health/ready", tags=["health"])
    def ready() -> dict[str, str]:
        with session_factory() as db:
            db.connection()
        return {"status": "ok"}

    return app


app = create_app()
