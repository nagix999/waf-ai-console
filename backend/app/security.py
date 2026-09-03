import hmac
from dataclasses import dataclass
from typing import Annotated, Callable

from fastapi import Depends, HTTPException, Request, status


@dataclass(frozen=True)
class Principal:
    kind: str
    scopes: frozenset[str]
    username: str | None = None
    source_system: str | None = None


def get_principal(request: Request) -> Principal:
    settings = request.app.state.settings
    if request.session.get("admin_authenticated") is True:
        return Principal(
            kind="admin_session",
            username=settings.admin_username,
            source_system="admin-ui",
            scopes=frozenset({"admin", "ingest", "review"}),
        )

    supplied = request.headers.get("x-api-key", "")
    if supplied and hmac.compare_digest(supplied, settings.bootstrap_api_key):
        return Principal(
            kind="service_api_key",
            source_system=settings.bootstrap_source_system,
            scopes=frozenset({"ingest", "review"}),
        )
    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="authentication_required")


CurrentPrincipal = Annotated[Principal, Depends(get_principal)]


def require_scope(scope: str) -> Callable:
    def dependency(principal: CurrentPrincipal) -> Principal:
        if scope not in principal.scopes:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=f"scope_required:{scope}")
        return principal

    return dependency
