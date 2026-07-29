from __future__ import annotations

from fastapi import Request

from app.security import require_exact_origin


def require_session(request: Request) -> None:
    request.app.state.security.validate_session(request, csrf=False)


def require_mutation_auth(request: Request) -> None:
    security = request.app.state.security
    require_exact_origin(request, security.allowed_origins)
    security.validate_session(request, csrf=True)
