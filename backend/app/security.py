from __future__ import annotations

import hashlib
import hmac
import secrets
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from urllib.parse import urlsplit

from fastapi import HTTPException, Request, Response, status

from app.config.models import RuntimeMode, Settings


BOOTSTRAP_COOKIE = "reed_bootstrap"
SESSION_COOKIE = "reed_session"
CSRF_HEADER = "x-csrf-token"


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _origin(value: str) -> str | None:
    parsed = urlsplit(value)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.netloc
        or parsed.username is not None
        or parsed.password is not None
    ):
        return None
    return f"{parsed.scheme}://{parsed.netloc}"


def configured_values(value: str) -> tuple[str, ...]:
    return tuple(item.strip() for item in value.split(",") if item.strip())


def has_exact_host(request: Request) -> bool:
    allowed = configured_values(request.app.state.settings.allowed_hosts)
    host = request.headers.get("host", "").lower()
    return bool(host) and host in {item.lower() for item in allowed}


def require_exact_origin(request: Request, allowed: tuple[str, ...]) -> None:
    origin = request.headers.get("origin")
    referer = request.headers.get("referer")
    if (
        origin is None
        or referer is None
        or _origin(origin) not in allowed
        or _origin(referer) not in allowed
        or origin != _origin(origin)
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="request origin rejected",
        )


@dataclass(frozen=True)
class SessionRecord:
    expires_at: datetime
    csrf_digest: str


class SecurityManager:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.clock = lambda: datetime.now(UTC)
        self.bootstrap_token = secrets.token_urlsafe(32)
        self.bootstrap_expires_at = self.clock() + timedelta(
            seconds=settings.bootstrap_ttl_seconds
        )
        self.bootstrap_used = False
        self.sessions: dict[str, SessionRecord] = {}
        self.login_attempts: dict[str, deque[datetime]] = defaultdict(deque)
        hosted_secret = settings.hosted_operator_secret.get_secret_value()
        self.signing_key = (
            hashlib.sha256(
                f"reed-hosted-session:{hosted_secret}".encode("utf-8")
            ).digest()
            if hosted_secret
            else secrets.token_bytes(32)
        )

    @property
    def allowed_origins(self) -> tuple[str, ...]:
        if self.settings.runtime_mode is RuntimeMode.HOSTED:
            backend = self.settings.hosted_backend_origin
            return (backend,) if backend else ()
        return configured_values(self.settings.local_allowed_origins)

    def issue_bootstrap(self, response: Response, *, secure: bool) -> None:
        if self.settings.runtime_mode is not RuntimeMode.LOCAL:
            raise HTTPException(status_code=404, detail="not found")
        if self.bootstrap_used or self.clock() >= self.bootstrap_expires_at:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="local bootstrap unavailable",
            )
        response.set_cookie(
            BOOTSTRAP_COOKIE,
            self.bootstrap_token,
            max_age=self.settings.bootstrap_ttl_seconds,
            httponly=True,
            secure=secure,
            samesite="strict",
            path="/",
        )

    def exchange_bootstrap(
        self,
        request: Request,
        response: Response,
    ) -> str:
        supplied = request.cookies.get(BOOTSTRAP_COOKIE, "")
        valid = (
            not self.bootstrap_used
            and self.clock() < self.bootstrap_expires_at
            and hmac.compare_digest(supplied, self.bootstrap_token)
        )
        if not valid:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="local bootstrap rejected",
            )
        origin = request.headers.get("origin")
        referer = request.headers.get("referer")
        if origin is None and referer is None:
            if not has_exact_host(request):
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="request origin rejected",
                )
        else:
            require_exact_origin(request, self.allowed_origins)
        self.bootstrap_used = True
        response.delete_cookie(
            BOOTSTRAP_COOKIE,
            path="/",
            httponly=True,
            secure=request.url.scheme == "https",
            samesite="strict",
        )
        return self._create_session(response, secure=request.url.scheme == "https")

    def login(
        self,
        request: Request,
        response: Response,
        supplied_secret: str,
    ) -> str:
        if self.settings.runtime_mode is not RuntimeMode.HOSTED:
            raise HTTPException(status_code=404, detail="not found")
        require_exact_origin(request, self.allowed_origins)
        self._check_login_rate(request)
        configured = self.settings.hosted_operator_secret.get_secret_value()
        if not configured or not hmac.compare_digest(supplied_secret, configured):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="authentication failed",
            )
        self.login_attempts.pop(self._client_key(request), None)
        return self._create_session(response, secure=True)

    def _create_session(self, response: Response, *, secure: bool) -> str:
        token = secrets.token_urlsafe(32)
        signature = hmac.new(
            self.signing_key,
            token.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        cookie_value = f"{token}.{signature}"
        csrf = secrets.token_urlsafe(32)
        self.sessions[_digest(cookie_value)] = SessionRecord(
            expires_at=self.clock()
            + timedelta(seconds=self.settings.session_ttl_seconds),
            csrf_digest=_digest(csrf),
        )
        response.set_cookie(
            SESSION_COOKIE,
            cookie_value,
            max_age=self.settings.session_ttl_seconds,
            httponly=True,
            secure=secure,
            samesite="strict",
            path="/",
        )
        return csrf

    def validate_session(self, request: Request, *, csrf: bool) -> None:
        token = request.cookies.get(SESSION_COOKIE, "")
        if not self._valid_signature(token):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="authentication required",
            )
        record = self.sessions.get(_digest(token))
        if record is None or self.clock() >= record.expires_at:
            if token:
                self.sessions.pop(_digest(token), None)
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="authentication required",
            )
        if csrf:
            supplied = request.headers.get(CSRF_HEADER, "")
            if not hmac.compare_digest(_digest(supplied), record.csrf_digest):
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="request verification failed",
                )

    def logout(self, request: Request, response: Response) -> None:
        token = request.cookies.get(SESSION_COOKIE, "")
        if token:
            self.sessions.pop(_digest(token), None)
        self.invalidate_bootstrap()
        response.delete_cookie(
            SESSION_COOKIE,
            path="/",
            httponly=True,
            secure=self.settings.runtime_mode is RuntimeMode.HOSTED,
            samesite="strict",
        )

    def _valid_signature(self, cookie_value: str) -> bool:
        try:
            token, signature = cookie_value.rsplit(".", 1)
        except ValueError:
            return False
        expected = hmac.new(
            self.signing_key,
            token.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        return hmac.compare_digest(signature, expected)

    def invalidate_bootstrap(self) -> None:
        self.bootstrap_used = True
        self.bootstrap_token = ""

    def expire_all_sessions(self) -> None:
        self.sessions.clear()

    def _check_login_rate(self, request: Request) -> None:
        now = self.clock()
        cutoff = now - timedelta(
            seconds=self.settings.auth_rate_limit_window_seconds
        )
        attempts = self.login_attempts[self._client_key(request)]
        while attempts and attempts[0] <= cutoff:
            attempts.popleft()
        if len(attempts) >= self.settings.auth_rate_limit_attempts:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="authentication rate limit exceeded",
            )
        attempts.append(now)

    @staticmethod
    def _client_key(request: Request) -> str:
        return request.client.host if request.client is not None else "unknown"


async def security_headers_middleware(
    request: Request,
    call_next,
) -> Response:
    response = await call_next(request)
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; base-uri 'self'; object-src 'none'; "
        "frame-ancestors 'none'; form-action 'self'"
    )
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "same-origin"
    response.headers["X-Content-Type-Options"] = "nosniff"
    if request.app.state.settings.runtime_mode is RuntimeMode.HOSTED:
        response.headers[
            "Strict-Transport-Security"
        ] = "max-age=31536000; includeSubDomains"
    if (
        request.method not in {"GET", "HEAD", "OPTIONS"}
        or request.url.path.startswith(("/api/auth", "/api/wizard"))
    ):
        response.headers["Cache-Control"] = "no-store"
    return response


async def exact_host_middleware(request: Request, call_next) -> Response:
    if not has_exact_host(request):
        return Response("invalid host header", status_code=400)
    return await call_next(request)
