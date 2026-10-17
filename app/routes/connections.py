import os
import re
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.integrations.gmail.client import GmailClient
from app.integrations.gmail.connection import get_gmail_client
from app.integrations.google.oauth import GOOGLE_GMAIL_SCOPES, build_google_oauth_flow
from app.security.tokens import TokenCipher
from app.services.connection_store import ConnectionStore
from app.services.runtime_state_store import RuntimeStateStore

router = APIRouter(prefix="/connections", tags=["connections"])


def _public_connection(connection) -> dict:
    return {
        "id": connection.id,
        "provider": connection.provider,
        "provider_account_id": connection.provider_account_id,
        "display_name": connection.display_name,
        "status": connection.status,
        "scopes": connection.scopes,
        "token_expires_at": connection.token_expires_at,
        "metadata": connection.connection_metadata,
        "updated_at": connection.updated_at,
    }


def _frontend_redirect(*, status: str, provider: str, detail: str | None = None) -> RedirectResponse:
    settings = get_settings()
    query = {"connection": provider, "status": status}
    if detail:
        query["detail"] = detail
    return RedirectResponse(f"{settings.frontend_base_url}/?{urlencode(query)}", status_code=302)


def _oauth_state_key(user_id: str) -> str:
    return f"oauth-google-health:{user_id}"


def _safe_oauth_error(exc: Exception) -> str:
    """Return useful OAuth diagnostics while redacting credential-like values."""
    kind = type(exc).__name__
    text = str(exc).strip()
    if not text:
        return kind

    # Provider/library errors are useful during local development, but never surface
    # authorization codes, access/refresh tokens, or client secrets.
    redacted = re.sub(
        r"(?i)(client_secret|access_token|refresh_token|id_token|authorization_code|code)\s*[=:]\s*[^\s,&]+",
        r"\1=[REDACTED]",
        text,
    )
    redacted = redacted.replace("\n", " ").strip()
    if len(redacted) > 320:
        redacted = redacted[:317] + "..."
    return f"{kind}: {redacted}"


def _record_oauth_health(
    db: Session,
    *,
    user_id: str,
    status: str,
    detail: str | None = None,
    account: str | None = None,
) -> None:
    RuntimeStateStore(db).set(
        _oauth_state_key(user_id),
        {
            "status": status,
            "detail": detail,
            "account": account,
        },
    )


@router.get("")
def list_connections(db: Session = Depends(get_db)) -> dict:
    settings = get_settings()
    store = ConnectionStore(db)
    store.ensure_user(user_id=settings.dev_user_id, email=settings.dev_user_email)
    connections = store.list_for_user(settings.dev_user_id)
    active_google = next(
        (item for item in connections if item.provider == "google" and item.status == "CONNECTED"),
        None,
    )
    gmail_connected = bool(
        active_google
        and all(scope in (active_google.scopes or []) for scope in GOOGLE_GMAIL_SCOPES)
    )
    oauth_health = RuntimeStateStore(db).get(_oauth_state_key(settings.dev_user_id)) or {}
    oauth_error = (
        str(oauth_health.get("detail") or "").strip()
        if oauth_health.get("status") == "ERROR" and not gmail_connected
        else ""
    )
    gmail_description = "Read incoming mail and send replies for allowed automations."
    if oauth_error:
        gmail_description = f"Connection failed: {oauth_error} Click Connect to retry."

    return {
        "mode": "single-user-development",
        "user": {"id": settings.dev_user_id, "email": settings.dev_user_email},
        "connections": [_public_connection(item) for item in connections],
        "oauth_status": oauth_health,
        "catalog": [
            {
                "id": "gmail",
                "provider": "google",
                "label": "Gmail",
                "description": gmail_description,
                "status": "CONNECTED" if gmail_connected else "AVAILABLE",
                "account": active_google.provider_account_id if gmail_connected else None,
                "connect_path": "/connections/google/start",
            },
            {
                "id": "google_calendar",
                "provider": "google",
                "label": "Google Calendar",
                "description": "Check availability and create/update calendar events.",
                "status": "COMING_NEXT",
                "account": None,
                "connect_path": None,
            },
            {
                "id": "github",
                "provider": "github",
                "label": "GitHub",
                "description": "Observe repositories, workflow runs, pull requests, and issues.",
                "status": "COMING_NEXT",
                "account": None,
                "connect_path": None,
            },
        ],
    }


@router.get("/google/start")
def start_google_connection(
    return_to: str | None = Query(default=None),
    db: Session = Depends(get_db),
):
    del return_to
    settings = get_settings()
    store = ConnectionStore(db)
    store.ensure_user(user_id=settings.dev_user_id, email=settings.dev_user_email)

    try:
        # Fail locally before sending the user through Google when Jiraya cannot securely
        # persist the resulting refresh token.
        TokenCipher(settings.token_encryption_key)
        state_record = store.create_oauth_state(
            user_id=settings.dev_user_id,
            provider="google",
        )
        _record_oauth_health(
            db,
            user_id=settings.dev_user_id,
            status="AUTHORIZING",
            detail=None,
        )
        flow = build_google_oauth_flow(settings, state=state_record.state)
        authorization_url, _ = flow.authorization_url(
            access_type="offline",
            prompt="consent",
        )
    except RuntimeError as exc:
        detail = _safe_oauth_error(exc)
        _record_oauth_health(
            db,
            user_id=settings.dev_user_id,
            status="ERROR",
            detail=detail,
        )
        raise HTTPException(status_code=503, detail=detail) from exc

    return RedirectResponse(authorization_url, status_code=302)


@router.get("/google/callback")
def google_connection_callback(
    state: str,
    code: str | None = None,
    error: str | None = None,
    db: Session = Depends(get_db),
):
    settings = get_settings()
    store = ConnectionStore(db)

    state_record = store.consume_oauth_state(state=state, provider="google")
    if state_record is None:
        detail = "invalid_or_expired_state"
        _record_oauth_health(
            db,
            user_id=settings.dev_user_id,
            status="ERROR",
            detail=detail,
        )
        return _frontend_redirect(status="error", provider="google", detail=detail)
    if error:
        detail = f"GoogleAuthorizationError: {str(error)[:240]}"
        _record_oauth_health(
            db,
            user_id=state_record.user_id,
            status="ERROR",
            detail=detail,
        )
        return _frontend_redirect(status="error", provider="google", detail=detail)
    if not code:
        detail = "missing_code"
        _record_oauth_health(
            db,
            user_id=state_record.user_id,
            status="ERROR",
            detail=detail,
        )
        return _frontend_redirect(status="error", provider="google", detail=detail)

    try:
        if settings.app_env.lower() == "development":
            os.environ.setdefault("OAUTHLIB_INSECURE_TRANSPORT", "1")

        flow = build_google_oauth_flow(settings, state=state)
        flow.fetch_token(code=code)
        credentials = flow.credentials

        gmail = GmailClient(settings, credentials=credentials)
        profile = gmail.get_profile()
        email_address = profile.get("emailAddress")
        if not email_address:
            raise RuntimeError("Google authorized Gmail, but no mailbox identity was returned.")

        history_id = str(profile.get("historyId") or "")
        metadata = {"gmail_history_id": history_id}
        if settings.gmail_watch_auto_register and settings.gmail_pubsub_topic_name:
            try:
                watch = gmail.register_watch(topic_name=settings.gmail_pubsub_topic_name)
                history_id = str(watch.get("historyId") or history_id)
                metadata.update(
                    {
                        "gmail_history_id": history_id,
                        "gmail_watch_expiration": watch.get("expiration"),
                        "gmail_pubsub_topic": settings.gmail_pubsub_topic_name,
                    }
                )
            except Exception as watch_exc:
                # OAuth connection remains useful because the local history monitor can
                # still run even if Pub/Sub has not been configured correctly yet.
                metadata["gmail_watch_error"] = type(watch_exc).__name__

        cipher = TokenCipher(settings.token_encryption_key)
        connection = store.upsert(
            user_id=state_record.user_id,
            provider="google",
            provider_account_id=email_address,
            display_name=email_address,
            scopes=list(credentials.scopes or GOOGLE_GMAIL_SCOPES),
            credential_ciphertext=cipher.encrypt(credentials.to_json()),
            token_expires_at=credentials.expiry,
            metadata=metadata,
        )
        RuntimeStateStore(db).set(
            f"gmail-history:{state_record.user_id}",
            {
                "history_id": history_id,
                "email_address": email_address,
                "watch_expiration": metadata.get("gmail_watch_expiration"),
                "connection_id": connection.id,
            },
        )
        _record_oauth_health(
            db,
            user_id=state_record.user_id,
            status="CONNECTED",
            detail=None,
            account=email_address,
        )
    except Exception as exc:
        detail = _safe_oauth_error(exc)
        _record_oauth_health(
            db,
            user_id=state_record.user_id,
            status="ERROR",
            detail=detail,
        )
        return _frontend_redirect(
            status="error",
            provider="google",
            detail=detail,
        )

    return _frontend_redirect(status="connected", provider="google")


@router.post("/google/disconnect")
def disconnect_google(db: Session = Depends(get_db)) -> dict:
    settings = get_settings()
    if settings.gmail_pubsub_topic_name:
        try:
            get_gmail_client(db, settings).stop_watch()
        except Exception:
            pass
    connection = ConnectionStore(db).disconnect(
        user_id=settings.dev_user_id,
        provider="google",
    )
    _record_oauth_health(
        db,
        user_id=settings.dev_user_id,
        status="DISCONNECTED",
        detail=None,
    )
    return {
        "disconnected": connection is not None,
        "provider": "google",
    }
