from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.runtime.status import monitor_status
from app.services.connection_store import ConnectionStore
from app.services.runtime_state_store import RuntimeStateStore

router = APIRouter(tags=["automation-runtime"])


@router.get("/runtime/status")
def runtime_status(db: Annotated[Session, Depends(get_db)]) -> dict:
    settings = get_settings()
    states = RuntimeStateStore(db)
    connections = ConnectionStore(db)
    monitors = {}
    for label, provider, enabled, delay in (
        (
            "gmail",
            "google",
            settings.gmail_background_monitor_enabled,
            settings.gmail_background_poll_seconds,
        ),
        (
            "calendar",
            "google_calendar",
            settings.calendar_background_monitor_enabled,
            settings.calendar_background_poll_seconds,
        ),
        (
            "github",
            "github",
            settings.github_background_monitor_enabled,
            settings.github_background_poll_seconds,
        ),
    ):
        connected = (
            connections.get_active(user_id=settings.dev_user_id, provider=provider) is not None
        )
        health = monitor_status(
            states.get(f"{label}-monitor-health:{settings.dev_user_id}") or {},
            enabled=enabled,
            poll_seconds=delay,
        )
        status = (
            "DISCONNECTED"
            if not connected
            else "DISABLED"
            if not enabled
            else "STARTING"
            if not health.get("last_poll_at")
            else "STALE"
            if not health["fresh"]
            else "ERROR"
            if health.get("last_status") != "OK"
            else "MANUAL_ONLY"
            if health.get("poll_source") == "manual"
            else "IDLE"
            if health.get("last_decision") == "NO_AUTOMATIONS"
            else "MONITORING"
        )
        monitors[label] = {
            **health,
            "status": status,
            "connected": connected,
            "operational": connected and health["operational"],
            "configured": enabled,
            "poll_seconds": delay,
        }
    return {
        "mode": settings.automation_runtime_mode,
        "browser_required": False,
        "external_actions_enabled": settings.enable_external_actions,
        "monitors": monitors,
        "local_checkpoint_requires_device": True,
    }
