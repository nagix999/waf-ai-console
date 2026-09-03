from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.middleware.sessions import SessionMiddleware

from .api.analyses import router as analyses_router
from .api.auth import router as auth_router
from .api.model_profiles import router as model_profiles_router
from .config import Settings, get_settings
from .database import Base, build_engine, build_session_factory
from .services.crypto import CryptoService


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

    app = FastAPI(title=settings.app_name, version="0.1.0", lifespan=lifespan)
    app.state.settings = settings
    app.state.engine = engine
    app.state.session_factory = session_factory
    app.state.crypto = CryptoService(settings.data_encryption_key, settings.encryption_key_version)
    app.add_middleware(
        SessionMiddleware,
        secret_key=settings.session_secret,
        max_age=settings.session_max_age_seconds,
        same_site="strict",
        https_only=settings.session_https_only,
    )
    app.include_router(auth_router, prefix="/api/v1")
    app.include_router(analyses_router, prefix="/api/v1")
    app.include_router(model_profiles_router, prefix="/api/v1")

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
