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


async def gmail_monitor_loop(settings: Settings) -> None:
    """Continuously consume Gmail history during local/server runtime.

    Production Pub/Sub can wake the same runtime immediately, while this loop remains a
    useful fallback and makes local end-to-end testing possible without a public webhook.
    Runtime health is persisted so the frontend can distinguish "configured" from
    "actually polling Gmail successfully".
    """
    if not settings.gmail_background_monitor_enabled:
        return

    health_key = f"gmail-monitor-health:{settings.dev_user_id}"
    await asyncio.sleep(1)
    while True:
        try:
            def _poll() -> None:
                with SessionLocal() as db:
                    health = RuntimeStateStore(db)
                    try:
                        result = GmailRuntimeService(db=db, settings=settings).poll()
                        health.set(
                            health_key,
                            {
                                "last_poll_at": _now_iso(),
                                "last_status": "OK",
                                "last_decision": result.get("decision"),
                                "last_error": None,
                            },
                        )
                    except Exception as exc:
                        health.set(
                            health_key,
                            {
                                "last_poll_at": _now_iso(),
                                "last_status": "ERROR",
                                "last_decision": None,
                                "last_error": _safe_error(exc),
                            },
                        )
                        raise

            await asyncio.to_thread(_poll)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            # Do not crash FastAPI, but make Jiraya-owned RuntimeError reasons visible.
            # Other provider/library failures expose only their exception type.
            logger.warning("Gmail background monitor cycle failed: %s", _safe_error(exc))

        await asyncio.sleep(settings.gmail_background_poll_seconds)


async def calendar_monitor_loop(settings: Settings) -> None:
    from app.runtime.calendar_runtime import CalendarRuntimeService

    await asyncio.sleep(1)
    while True:
        def _poll_calendar():
            with SessionLocal() as db:
                state = RuntimeStateStore(db)
                try:
                    result = CalendarRuntimeService(db=db, settings=settings).poll()
                    state.set(f"calendar-monitor-health:{settings.dev_user_id}", {
                        "last_poll_at": _now_iso(), "last_status": "OK",
                        "last_decision": result["decision"], "last_error": None})
                except Exception as exc:
                    state.set(f"calendar-monitor-health:{settings.dev_user_id}", {
                        "last_poll_at": _now_iso(), "last_status": "ERROR",
                        "last_error": _safe_error(exc)})
        try:
            await asyncio.to_thread(_poll_calendar)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("Calendar background monitor cycle failed: %s", _safe_error(exc))
        await asyncio.sleep(settings.calendar_background_poll_seconds)
