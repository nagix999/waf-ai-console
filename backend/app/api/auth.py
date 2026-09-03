import hmac

from fastapi import APIRouter, HTTPException, Request, status

from ..schemas import LoginRequest, PrincipalResponse
from ..security import CurrentPrincipal

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login", response_model=PrincipalResponse)
def login(payload: LoginRequest, request: Request) -> PrincipalResponse:
    settings = request.app.state.settings
    username_ok = hmac.compare_digest(payload.username, settings.admin_username)
    password_ok = hmac.compare_digest(payload.password, settings.admin_password)
    if not (username_ok and password_ok):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid_credentials")
    request.session.clear()
    request.session["admin_authenticated"] = True
    return PrincipalResponse(kind="admin_session", username=settings.admin_username, scopes=["admin", "ingest", "review"])


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(request: Request) -> None:
    request.session.clear()


@router.get("/me", response_model=PrincipalResponse)
def me(principal: CurrentPrincipal) -> PrincipalResponse:
    return PrincipalResponse(
        kind=principal.kind,
        username=principal.username,
        source_system=principal.source_system,
        scopes=sorted(principal.scopes),
    )
