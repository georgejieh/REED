from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field

from app.config.models import RuntimeMode
from app.security import SecurityManager, require_exact_origin


router = APIRouter(prefix="/api/auth", tags=["authentication"])


class SessionResponse(BaseModel):
    csrf_token: str


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    secret: str = Field(min_length=1, max_length=16384)


def _security(request: Request) -> SecurityManager:
    return request.app.state.security


@router.get("/bootstrap", status_code=status.HTTP_204_NO_CONTENT)
def bootstrap(request: Request, response: Response) -> Response:
    _security(request).issue_bootstrap(
        response,
        secure=request.url.scheme == "https",
    )
    response.status_code = status.HTTP_204_NO_CONTENT
    return response


@router.post("/session", response_model=SessionResponse)
def exchange_session(request: Request, response: Response) -> SessionResponse:
    security = _security(request)
    if security.settings.runtime_mode is not RuntimeMode.LOCAL:
        raise HTTPException(status_code=404, detail="not found")
    csrf = security.exchange_bootstrap(request, response)
    return SessionResponse(csrf_token=csrf)


@router.post("/login", response_model=SessionResponse)
def login(
    submission: LoginRequest,
    request: Request,
    response: Response,
) -> SessionResponse:
    csrf = _security(request).login(request, response, submission.secret)
    return SessionResponse(csrf_token=csrf)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(request: Request, response: Response) -> Response:
    security = _security(request)
    require_exact_origin(request, security.allowed_origins)
    security.validate_session(request, csrf=True)
    security.logout(request, response)
    response.status_code = status.HTTP_204_NO_CONTENT
    return response
