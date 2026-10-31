import asyncio
import logging
from datetime import datetime, timezone

from app.config import Settings
from app.db import SessionLocal
from app.runtime.gmail_runtime import GmailRuntimeService
from app.services.runtime_state_store import RuntimeStateStore

logger = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_error(exc: Exception) -> str:
    """Return a useful diagnostic without ever dumping provider payloads/tokens.

    RuntimeError messages in Jiraya's credential/connection layer are deliberately
    human-readable and contain no secret values. For all other exception classes we
    expose only the class name.
    """
    if isinstance(exc, RuntimeError):
        text = str(exc).strip()
        return text[:500] if text else "RuntimeError"
    return type(exc).__name__


def poll_accounts(settings, provider, label, factory):
    from sqlalchemy import select
    from app.models import Connection, User
    from app.security.identity import account_scope

    with SessionLocal() as registry:
        rows = registry.execute(select(User.id, User.email).join(Connection, Connection.user_id == User.id)
            .where(Connection.provider == provider, Connection.status == "CONNECTED").distinct()).all()
    accounts = list(rows)
    if not settings.auth_required and (settings.dev_user_id, settings.dev_user_email) not in accounts:
        accounts.append((settings.dev_user_id, settings.dev_user_email))
    for user_id, email in accounts:
        with account_scope(user_id, email), SessionLocal() as db:
            scoped = settings.model_copy(update={"dev_user_id": user_id, "dev_user_email": email})
            try:
                result = factory(db=db, settings=scoped).poll()
                health = {"last_poll_at": _now_iso(), "last_status": "ERROR" if result.get("errors") else "OK",
                          "last_decision": result.get("decision"),
                          "last_error": ("; ".join(error["detail"] for error in result["errors"])[:1000] if provider == "github" else "Some checks failed. Check service access.") if result.get("errors") else None}
            except Exception as exc:
                db.rollback()
                health = {"last_poll_at": _now_iso(), "last_status": "ERROR", "last_error": _safe_error(exc)}
            RuntimeStateStore(db).set(f"{label}-monitor-health:{user_id}", health)


async def monitor_loop(settings, provider, label, factory, delay):
    await asyncio.sleep(1)
    while True:
        try:
            await asyncio.to_thread(poll_accounts, settings, provider, label, factory)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("%s background monitor failed: %s", label, _safe_error(exc))
        await asyncio.sleep(delay)


async def gmail_monitor_loop(settings: Settings):
    await monitor_loop(settings, "google", "gmail", GmailRuntimeService, settings.gmail_background_poll_seconds)


async def calendar_monitor_loop(settings: Settings):
    from app.runtime.calendar_runtime import CalendarRuntimeService
    await monitor_loop(settings, "google_calendar", "calendar", CalendarRuntimeService, settings.calendar_background_poll_seconds)


async def github_monitor_loop(settings: Settings):
    from app.runtime.github_runtime import GitHubRuntimeService
    await monitor_loop(settings, "github", "github", GitHubRuntimeService, settings.github_background_poll_seconds)
