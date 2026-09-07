from dataclasses import dataclass
from typing import Annotated, Callable

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import APIKeyHeader
from sqlalchemy.exc import SQLAlchemyError

from .services.service_api_keys import authenticate_key


@dataclass(frozen=True)
class Principal:
    kind: str
    scopes: frozenset[str]
    username: str | None = None
    source_system: str | None = None


service_api_key = APIKeyHeader(name="X-API-Key", auto_error=False, scheme_name="ServiceAPIKey")


def get_principal(request: Request, supplied: Annotated[str | None, Depends(service_api_key)]) -> Principal:
    settings = request.app.state.settings
    if request.session.get("admin_authenticated") is True:
        return Principal(
            kind="admin_session",
            username=settings.admin_username,
            source_system="admin-ui",
            scopes=frozenset({"admin", "ingest", "review"}),
        )

    if not supplied or len(supplied) > 512:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="authentication_required")
    try:
        # A fresh, short session keeps revocation checks and last-authenticated
        # timestamps separate from an endpoint's read snapshot/ingest commit.
        with request.app.state.session_factory() as db:
            key = authenticate_key(db, supplied)
            if key is not None:
                principal = Principal(kind="service_api_key", source_system=key.source_system, scopes=frozenset(key.scopes_json))
                db.commit()
                return principal
    except SQLAlchemyError:
        raise HTTPException(status_code=503, detail="service_api_key_authentication_unavailable") from None
    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="authentication_required")


CurrentPrincipal = Annotated[Principal, Depends(get_principal)]


def require_scope(scope: str) -> Callable:
    def dependency(principal: CurrentPrincipal) -> Principal:
        if scope not in principal.scopes:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=f"scope_required:{scope}")
        return principal

    return dependency
