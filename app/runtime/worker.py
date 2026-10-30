"""Run cloud automation without an HTTP server or browser session.

Run one instance only. Configure the API and worker with
AUTOMATION_RUNTIME_MODE=external and the same database/encryption/provider settings.
"""

import asyncio
import logging
import signal

from app.config import Settings, get_base_settings
from app.db import init_db
from app.runtime.background import running_monitors


async def serve(settings: Settings, shutdown: asyncio.Event | None = None):
    if settings.automation_runtime_mode != "external":
        raise RuntimeError("Set AUTOMATION_RUNTIME_MODE=external on both the API and worker first.")
    if not any(
        (
            settings.gmail_background_monitor_enabled,
            settings.calendar_background_monitor_enabled,
            settings.github_background_monitor_enabled,
        )
    ):
        raise RuntimeError("Enable at least one background monitor before starting the worker.")
    init_db()
    shutdown = shutdown or asyncio.Event()
    loop = asyncio.get_running_loop()
    registered = []
    for signum in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(signum, shutdown.set)
            registered.append(signum)
        except NotImplementedError:  # Windows local development: Ctrl+C still works.
            pass
    try:
        async with running_monitors(settings):
            logging.getLogger(__name__).info(
                "Automation worker started; browser sessions are not required."
            )
            await shutdown.wait()
    finally:
        for signum in registered:
            loop.remove_signal_handler(signum)


def main():
    logging.basicConfig(level=logging.INFO)
    try:
        asyncio.run(serve(get_base_settings()))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
