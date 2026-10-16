import asyncio
import logging

from app.config import Settings
from app.db import SessionLocal
from app.runtime.gmail_runtime import GmailRuntimeService

logger = logging.getLogger(__name__)


async def gmail_monitor_loop(settings: Settings) -> None:
    """Continuously consume Gmail history during local/server runtime.

    Production Pub/Sub can wake the same runtime immediately, while this loop remains a
    useful fallback and makes local end-to-end testing possible without a public webhook.
    """
    if not settings.gmail_background_monitor_enabled:
        return

    await asyncio.sleep(1)
    while True:
        try:
            def _poll() -> None:
                with SessionLocal() as db:
                    GmailRuntimeService(db=db, settings=settings).poll()

            await asyncio.to_thread(_poll)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            # Background monitoring must not crash the FastAPI process. Connection/model
            # errors remain visible through normal runtime/API diagnostics.
            logger.debug("Gmail background monitor skipped cycle: %s", type(exc).__name__)

        await asyncio.sleep(settings.gmail_background_poll_seconds)
