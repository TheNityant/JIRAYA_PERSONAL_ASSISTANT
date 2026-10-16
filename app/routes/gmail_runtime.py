import base64
import json

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.integrations.gmail.connection import get_gmail_client
from app.runtime.gmail_runtime import GmailRuntimeService
from app.services.connection_store import ConnectionStore
from app.services.runtime_state_store import RuntimeStateStore

router = APIRouter(tags=["gmail-runtime"])


@router.get("/runtime/gmail/status")
def gmail_runtime_status(db: Session = Depends(get_db)) -> dict:
    settings = get_settings()
    state_store = RuntimeStateStore(db)
    state = state_store.get(f"gmail-history:{settings.dev_user_id}") or {}
    health = state_store.get(f"gmail-monitor-health:{settings.dev_user_id}") or {}
    monitor_operational = bool(
        settings.gmail_background_monitor_enabled and health.get("last_status") == "OK"
    )
    return {
        # Kept for the existing frontend: this now means the monitor is actually healthy,
        # not merely that the configuration flag is turned on.
        "background_monitor_enabled": monitor_operational,
        "background_monitor_configured": settings.gmail_background_monitor_enabled,
        "poll_seconds": settings.gmail_background_poll_seconds,
        "pubsub_configured": bool(settings.gmail_pubsub_topic_name),
        "pubsub_topic": settings.gmail_pubsub_topic_name or None,
        "web_oauth_configured": bool(
            settings.google_oauth_client_id and settings.google_oauth_client_secret
        ),
        "history_cursor": state.get("history_id"),
        "mailbox": state.get("email_address"),
        "last_poll_at": health.get("last_poll_at"),
        "last_poll_status": health.get("last_status"),
        "last_poll_decision": health.get("last_decision"),
        "last_error": health.get("last_error"),
    }


@router.post("/runtime/gmail/poll")
def poll_gmail_runtime(db: Session = Depends(get_db)) -> dict:
    settings = get_settings()
    try:
        return GmailRuntimeService(db=db, settings=settings).poll()
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Gmail runtime unavailable: {type(exc).__name__}") from exc


@router.post("/runtime/gmail/watch/register")
def register_gmail_watch(db: Session = Depends(get_db)) -> dict:
    settings = get_settings()
    if not settings.gmail_pubsub_topic_name:
        raise HTTPException(
            status_code=400,
            detail="GMAIL_PUBSUB_TOPIC_NAME is not configured; local history polling can still run.",
        )
    try:
        gmail = get_gmail_client(db, settings)
        response = gmail.register_watch(topic_name=settings.gmail_pubsub_topic_name)
        profile = gmail.get_profile()
        history_id = str(response.get("historyId") or profile.get("historyId") or "")
        RuntimeStateStore(db).set(
            f"gmail-history:{settings.dev_user_id}",
            {
                "history_id": history_id,
                "email_address": profile.get("emailAddress") or "",
                "watch_expiration": response.get("expiration"),
            },
        )
        connection = ConnectionStore(db).get_active(
            user_id=settings.dev_user_id,
            provider="google",
        )
        if connection:
            ConnectionStore(db).update_metadata(
                connection,
                {
                    "gmail_history_id": history_id,
                    "gmail_watch_expiration": response.get("expiration"),
                    "gmail_pubsub_topic": settings.gmail_pubsub_topic_name,
                },
            )
        return {
            "registered": True,
            "history_id": history_id,
            "expiration": response.get("expiration"),
            "topic": settings.gmail_pubsub_topic_name,
        }
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Gmail watch registration failed: {type(exc).__name__}") from exc


@router.post("/webhooks/google/gmail")
async def gmail_pubsub_webhook(
    request: Request,
    token: str | None = Query(default=None),
    db: Session = Depends(get_db),
) -> dict:
    settings = get_settings()
    if settings.gmail_pubsub_webhook_token and token != settings.gmail_pubsub_webhook_token:
        raise HTTPException(status_code=401, detail="Invalid Gmail Pub/Sub webhook token")

    payload = await request.json()
    encoded = str(((payload.get("message") or {}).get("data")) or "")
    if not encoded:
        raise HTTPException(status_code=400, detail="Pub/Sub message has no data")
    try:
        padding = "=" * (-len(encoded) % 4)
        decoded = json.loads(base64.urlsafe_b64decode(encoded + padding).decode("utf-8"))
        email_address = str(decoded.get("emailAddress") or "")
        history_id = str(decoded.get("historyId") or "")
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Invalid Gmail Pub/Sub payload") from exc

    if not history_id:
        raise HTTPException(status_code=400, detail="Gmail notification has no historyId")

    service = GmailRuntimeService(db=db, settings=settings)
    profile = service.gmail.get_profile()
    profile_email = str(profile.get("emailAddress") or "")
    if email_address and profile_email and email_address.casefold() != profile_email.casefold():
        return {"accepted": True, "decision": "IGNORED_OTHER_ACCOUNT"}

    result = service.consume_history(
        target_history_id=history_id,
        email_address=email_address or profile_email,
    )
    return {"accepted": True, "result": result}
