from __future__ import annotations

from fastapi import HTTPException, Request, status

from app.security import require_exact_origin


def require_session(request: Request) -> None:
    request.app.state.security.validate_session(request, csrf=False)


def require_mutation_auth(request: Request) -> None:
    security = request.app.state.security
    require_exact_origin(request, security.allowed_origins)
    security.validate_session(request, csrf=True)


def require_same_origin(request: Request) -> None:
    raise HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail="obsolete authentication dependency",
    )
