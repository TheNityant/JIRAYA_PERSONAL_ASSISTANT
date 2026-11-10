"""Manual Google sign-in over an SSH-tunnelled desktop, for an operator-assisted pilot.

Stop attendance first. Never expose the forwarded desktop on a public interface.
"""

import argparse
import asyncio
import os
from types import SimpleNamespace

from sqlalchemy import select

from app.config import get_base_settings
from app.db import SessionLocal
from app.meet_cloud.identity import set_browser_status
from app.meet_cloud.profiles import account_matches, profile_path
from app.models import User
from app.security.identity import account_scope
from app.services.connection_store import ConnectionStore


def connected_account(user_email):
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
    return user_id, email, expected


def save_verified_account(user_id, email, expected, state):
    with account_scope(user_id, email), SessionLocal() as db:
        connection = ConnectionStore(db).get_active(user_id=user_id, provider="google_calendar")
        if not connection or connection.provider_account_id != expected:
            raise RuntimeError("Calendar changed during setup. Connect the current account again.")
        set_browser_status(db, user_id, expected, state)


async def check_saved_session(user_email):
    """Headless verification of an existing profile; never opens a sign-in desktop."""
    from app.meet_cloud.browser import CloudJoiner

    settings = get_base_settings()
    user_id, email, expected = connected_account(user_email)
    directory = profile_path(settings.meet_cloud_profile_root, user_id, expected)
    if not directory.is_dir():
        save_verified_account(user_id, email, expected, "SETUP_REQUIRED")
        print("No saved browser profile. Complete initial setup.", flush=True)
        return False
    # Independent of the deployed default: this command always verifies Google identity.
    settings = settings.model_copy(update={"meet_cloud_identity_mode": "google_profile"})
    joiner = CloudJoiner(
        audio=SimpleNamespace(name=""),
        settings=settings,
        expected_account=expected,
        profile_dir=str(directory),
        on_turn=None,
        tick=None,
        leave_on_speech=False,
    )
    engine, context = await joiner._launch_context()
    try:
        page = context.pages[0] if context.pages else await context.new_page()
        verified = await joiner.check_session(page)
    finally:
        try:
            await context.close()
        finally:
            await engine.stop()
    save_verified_account(
        user_id, email, expected, "CONNECTED" if verified else "RECONNECT_REQUIRED"
    )
    print(
        "Saved Google session verified; no interactive sign-in needed."
        if verified
        else "Google browser reconnection required.",
        flush=True,
    )
    return verified


async def setup(user_email):
    settings = get_base_settings()
    user_id, email, expected = connected_account(user_email)
    directory = profile_path(settings.meet_cloud_profile_root, user_id, expected)
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(directory, 0o700)
    processes = []
    verified = False
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
                            verified = True
                            break
                    if verified:
                        break
                    await asyncio.sleep(2)
                if not verified:
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
    # Only report ready after Chromium closes and flushes the persistent profile.
    # A Calendar account changed during setup must not authorize the new account.
    save_verified_account(user_id, email, expected, "CONNECTED")
    print(
        "Google account verified. Saved browser profile will be reused across meetings and worker restarts. Reconnect only if Google invalidates the session.",
        flush=True,
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--user-email", required=True)
    parser.add_argument(
        "--check",
        action="store_true",
        help="Verify the saved Google session without interactive sign-in. Stop the worker first.",
    )
    args = parser.parse_args()
    os.umask(0o077)
    if args.check:
        if not asyncio.run(check_saved_session(args.user_email)):
            raise SystemExit(1)
    else:
        asyncio.run(setup(args.user_email))


if __name__ == "__main__":
    main()
