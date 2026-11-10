"""Manual Google sign-in over an SSH-tunnelled desktop, for an operator-assisted pilot.

Stop attendance first. Never expose the forwarded desktop on a public interface.
"""

import argparse
import asyncio
import os

from sqlalchemy import select

from app.config import get_base_settings
from app.db import SessionLocal
from app.meet_cloud.profiles import account_matches, profile_path
from app.models import User
from app.security.identity import account_scope
from app.services.connection_store import ConnectionStore


async def setup(user_email):
    settings = get_base_settings()
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.email == user_email.strip().lower()))
        if not user:
            raise RuntimeError("Jiraya account not found. Use your Jiraya login email.")
        user_id, email = user.id, user.email
    with account_scope(user_id, email), SessionLocal() as db:
        connection = ConnectionStore(db).get_active(user_id=user_id, provider="google_calendar")
        if not connection:
            raise RuntimeError("Connect Google Calendar in Jiraya first.")
        expected = connection.provider_account_id
    directory = profile_path(settings.meet_cloud_profile_root, user_id, expected)
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(directory, 0o700)
    processes = []
    try:
        processes.append(
            await asyncio.create_subprocess_exec(
                *["Xvfb", ":99", "-screen", "0", "1280x720x24", "-nolisten", "tcp"],
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
        )
        for _ in range(50):
            if os.path.exists("/tmp/.X11-unix/X99"):
                break
            await asyncio.sleep(0.1)
        else:
            raise RuntimeError("The sign-in display did not start.")
        processes.append(
            await asyncio.create_subprocess_exec(
                *[
                    "x11vnc",
                    "-display",
                    ":99",
                    "-localhost",
                    "-nopw",
                    "-forever",
                    "-rfbport",
                    "5900",
                ],
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
        )
        processes.append(
            await asyncio.create_subprocess_exec(
                *["websockify", "--web", "/usr/share/novnc", "0.0.0.0:6080", "127.0.0.1:5900"],
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
        )
        from playwright.async_api import async_playwright

        async with async_playwright() as pw:
            # Pass no API/database secrets to the interactive browser process.
            env = {
                key: value
                for key, value in os.environ.items()
                if key in {"PATH", "HOME", "LANG", "LD_LIBRARY_PATH"}
            }
            env["DISPLAY"] = ":99"
            context = await pw.chromium.launch_persistent_context(
                str(directory), headless=False, locale="en-US", env=env, args=["--lang=en-US"]
            )
            try:
                page = context.pages[0] if context.pages else await context.new_page()
                await page.goto("https://meet.google.com/", wait_until="domcontentloaded")
                print(
                    "Open the SSH-tunnelled desktop. Sign in to the same Google account connected to Calendar. No meeting will be joined. Timeout: 10 minutes.",
                    flush=True,
                )
                for _ in range(300):
                    if any(process.returncode is not None for process in processes):
                        raise RuntimeError("The sign-in desktop stopped.")
                    for tab in context.pages:
                        if await account_matches(tab, expected):
                            print(
                                "Google account verified. Closing the setup desktop and saving its isolated browser profile.",
                                flush=True,
                            )
                            return
                    await asyncio.sleep(2)
                raise RuntimeError(
                    "Google sign-in was not verified within 10 minutes. No guest fallback is enabled by this setup."
                )
            finally:
                await context.close()
    finally:
        for process in reversed(processes):
            if process.returncode is not None:
                continue
            process.terminate()
            try:
                await asyncio.wait_for(process.wait(), 5)
            except TimeoutError:
                process.kill()
                await process.wait()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--user-email", required=True)
    args = parser.parse_args()
    os.umask(0o077)
    asyncio.run(setup(args.user_email))


if __name__ == "__main__":
    main()
