import asyncio
import logging
import threading
from contextlib import asynccontextmanager
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


def poll_accounts(settings, provider, label, factory, stop=None):
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
        if stop is not None and stop.is_set():
            break
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
            try:
                RuntimeStateStore(db).set(f"{label}-monitor-health:{user_id}", health)
            except Exception as exc:
                db.rollback()
                logger.warning("%s monitor could not save health: %s", label, type(exc).__name__)


async def monitor_loop(settings, provider, label, factory, delay, stop=None):
    stop = stop or threading.Event()
    # Small interruptible waits also make SIGTERM stop an idle worker promptly.
    async def pause(seconds):
        remaining = seconds
        while remaining > 0 and not stop.is_set():
            interval = min(remaining, 0.25)
            await asyncio.sleep(interval)
            remaining -= interval

    await pause(1)
    while not stop.is_set():
        try:
            await asyncio.to_thread(poll_accounts, settings, provider, label, factory, stop)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("%s background monitor failed: %s", label, _safe_error(exc))
        await pause(delay)


async def gmail_monitor_loop(settings: Settings, stop=None):
    await monitor_loop(settings, "google", "gmail", GmailRuntimeService, settings.gmail_background_poll_seconds, stop)


async def calendar_monitor_loop(settings: Settings, stop=None):
    from app.runtime.calendar_runtime import CalendarRuntimeService
    await monitor_loop(settings, "google_calendar", "calendar", CalendarRuntimeService, settings.calendar_background_poll_seconds, stop)


async def github_monitor_loop(settings: Settings, stop=None):
    from app.runtime.github_runtime import GitHubRuntimeService
    await monitor_loop(settings, "github", "github", GitHubRuntimeService, settings.github_background_poll_seconds, stop)


@asynccontextmanager
async def running_monitors(settings: Settings):
    """Shared API/worker lifecycle. Drain the current poll before closing the process."""
    stop = threading.Event()
    tasks = [
        asyncio.create_task(factory(settings, stop), name=f"{label}-monitor")
        for label, enabled, factory in (
            ("gmail", settings.gmail_background_monitor_enabled, gmail_monitor_loop),
            ("calendar", settings.calendar_background_monitor_enabled, calendar_monitor_loop),
            ("github", settings.github_background_monitor_enabled, github_monitor_loop),
        )
        if enabled
    ]
    try:
        yield
    finally:
        stop.set()
        await asyncio.gather(*tasks)
