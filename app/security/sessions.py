import hashlib
import secrets
from datetime import datetime, timedelta, timezone

from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse

from app.config import get_base_settings
from app.db import SessionLocal
from app.models import AuthSession, User
from app.security.identity import account_scope

COOKIE_NAME = "jiraya_session"


def digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def create_session(db, user, response, settings):
    token = secrets.token_urlsafe(32)
    db.add(
        AuthSession(
            token_hash=digest(token),
            user_id=user.id,
            expires_at=datetime.now(timezone.utc) + timedelta(days=settings.session_days),
        )
    )
    db.commit()
    response.set_cookie(
        COOKIE_NAME,
        token,
        httponly=True,
        secure=settings.session_cookie_secure
        or settings.app_env.lower() == "production"
        or settings.session_cookie_samesite == "none",
        samesite=settings.session_cookie_samesite,
        max_age=settings.session_days * 86400,
        path="/",
    )


class AccountMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        request = Request(scope)
        settings = get_base_settings()
        path = request.url.path
        if request.method == "OPTIONS":
            return await self.app(scope, receive, send)
        public = path in {
            "/",
            "/health",
            "/openapi.json",
            "/docs",
            "/docs/oauth2-redirect",
            "/auth/me",
            "/auth/login",
            "/auth/register",
        } or path.startswith("/webhooks/")
        current = None
        token = request.cookies.get(COOKIE_NAME)
        if token:
            with SessionLocal() as db:
                session = db.get(AuthSession, digest(token))
                if session:
                    expires = session.expires_at
                    if expires.tzinfo is None:
                        expires = expires.replace(tzinfo=timezone.utc)
                    if expires > datetime.now(timezone.utc):
                        user = db.get(User, session.user_id)
                        if user:
                            current = (user.id, user.email)
        if settings.auth_required and not public and not current:
            if (
                path.startswith("/connections/")
                and request.query_params.get("response_mode") != "json"
            ):
                return await RedirectResponse(
                    settings.frontend_base_url
                    + "/?status=error&connection=account&detail=Please_sign_in_again",
                    status_code=302,
                )(scope, receive, send)
            response = JSONResponse({"detail": "Sign in to Jiraya to continue."}, status_code=401)
            return await response(scope, receive, send)
        # Origin checks cover cookie-authenticated writes and account creation/login.
        if request.method not in {"GET", "HEAD"} and not path.startswith("/webhooks/"):
            origin = request.headers.get("origin")
            allowed = {item.strip().rstrip("/") for item in settings.frontend_origins.split(",")}
            if origin and origin.rstrip("/") not in allowed:
                return await JSONResponse(
                    {"detail": "Request origin is not allowed."}, status_code=403
                )(scope, receive, send)
            if settings.auth_required and not origin:
                return await JSONResponse(
                    {"detail": "A trusted Origin header is required."}, status_code=403
                )(scope, receive, send)
        if current:
            with account_scope(*current):
                return await self.app(scope, receive, send)
        return await self.app(scope, receive, send)
