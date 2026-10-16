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
                                "last_error": type(exc).__name__,
                            },
                        )
                        raise

            await asyncio.to_thread(_poll)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            # Do not crash FastAPI, but do make the failed monitor visible in normal logs
            # and through /runtime/gmail/status. Never log credential values.
            logger.warning("Gmail background monitor cycle failed: %s", type(exc).__name__)

        await asyncio.sleep(settings.gmail_background_poll_seconds)
