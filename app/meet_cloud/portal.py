"""Single-worker remote Google browser. No password automation, VNC or public socket."""

import asyncio
import base64
import json
import logging
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlsplit

from sqlalchemy import delete, select, update

from app.db import SessionLocal
from app.meet_cloud.browser import CloudJoiner
from app.meet_cloud.identity import set_browser_status
from app.meet_cloud.portal_store import ACTIVE_SETUP, finish
from app.meet_cloud.profiles import account_matches, profile_path
from app.meet_cloud.store import aware
from app.models import AuthSession, CloudMeetBrowserInput, CloudMeetBrowserSetup, User
from app.security.identity import account_scope
from app.security.tokens import TokenCipher
from app.services.connection_store import ConnectionStore

WIDTH, HEIGHT = 1100, 740
logger = logging.getLogger(__name__)


def allowed_navigation(url):
    try:
        parsed = urlsplit(url)
        if (
            parsed.scheme != "https"
            or parsed.port not in (None, 443)
            or parsed.username
            or parsed.password
        ):
            return False
        if parsed.hostname in {"accounts.google.com", "workspace.google.com"}:
            return True
        return parsed.hostname == "meet.google.com" and parsed.path in {
            "",
            "/",
            "/landing",
            "/landing/",
        }
    except ValueError:
        return False


def validate_setup(db, row, worker_id):
    if not row or row.worker_id != worker_id or row.status not in ACTIVE_SETUP:
        return False
    now = datetime.now(UTC)
    if aware(row.expires_at) <= now:
        finish(db, row, "EXPIRED", "SETUP_TIMEOUT")
        return False
    if aware(row.last_viewed_at) < now - timedelta(seconds=45):
        finish(db, row, "CANCELLED", "VIEW_CLOSED")
        return False
    session = db.get(AuthSession, row.auth_session_hash)
    connection = ConnectionStore(db).get_active(user_id=row.user_id, provider="google_calendar")
    if not session or session.user_id != row.user_id or aware(session.expires_at) <= now:
        finish(db, row, "CANCELLED", "JIRAYA_SIGNED_OUT")
        return False
    if not connection or connection.provider_account_id != row.calendar_account:
        finish(db, row, "CANCELLED", "CALENDAR_CHANGED")
        return False
    return True


async def apply_input(page, events):
    for event in events:
        if event["kind"] == "click":
            await page.mouse.click(event["x"] * WIDTH, event["y"] * HEIGHT)
        elif event["kind"] == "key":
            await page.keyboard.press(event["key"])
        elif event["kind"] == "text":
            await page.keyboard.insert_text(event["text"])
        elif event["kind"] == "scroll":
            await page.mouse.wheel(0, event["delta"])


async def verify_reusable_profile(settings, directory, expected):
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
        return await joiner.check_session(page)
    finally:
        try:
            await context.close()
        finally:
            await engine.stop()


async def run_setup(key, worker_id, settings, shutdown):
    with SessionLocal() as db:
        row = db.get(CloudMeetBrowserSetup, key)
        if not validate_setup(db, row, worker_id):
            return
        user = db.get(User, row.user_id)
        user_id, email, expected = row.user_id, user.email, row.calendar_account
    directory = profile_path(settings.meet_cloud_profile_root, user_id, expected)
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(directory, 0o700)
    cipher = TokenCipher(settings.token_encryption_key)
    display = None
    verified = False
    failure = "BROWSER_RUNTIME_UNAVAILABLE"
    try:
        display = await asyncio.create_subprocess_exec(
            "Xvfb",
            ":99",
            "-screen",
            "0",
            f"{WIDTH}x{HEIGHT}x24",
            "-nolisten",
            "tcp",
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        for _ in range(50):
            if Path("/tmp/.X11-unix/X99").exists():
                break
            if display.returncode is not None:
                raise RuntimeError("Display unavailable")
            await asyncio.sleep(0.1)
        else:
            raise RuntimeError("Display unavailable")
        from playwright.async_api import async_playwright

        async with async_playwright() as engine:
            env = {
                k: v
                for k, v in os.environ.items()
                if k in {"PATH", "HOME", "LANG", "LD_LIBRARY_PATH"}
            }
            env["DISPLAY"] = ":99"
            context = await engine.chromium.launch_persistent_context(
                str(directory),
                headless=False,
                locale="en-US",
                viewport={"width": WIDTH, "height": HEIGHT},
                env=env,
                accept_downloads=False,
                args=["--lang=en-US"],
            )
            try:

                async def guard(route):
                    request = route.request
                    if (
                        request.is_navigation_request()
                        and request.frame == request.frame.page.main_frame
                        and not allowed_navigation(request.url)
                    ):
                        await route.abort()
                    else:
                        await route.fallback()

                await context.route("**/*", guard)
                page = context.pages[0] if context.pages else await context.new_page()
                await page.goto(
                    "https://meet.google.com/", wait_until="domcontentloaded", timeout=30_000
                )
                while not shutdown.is_set():
                    with account_scope(user_id, email), SessionLocal() as db:
                        row = db.get(CloudMeetBrowserSetup, key)
                        if not validate_setup(db, row, worker_id):
                            return
                        row.heartbeat_at = datetime.now(UTC)
                        commands = list(
                            db.scalars(
                                select(CloudMeetBrowserInput)
                                .where(CloudMeetBrowserInput.setup_id == key)
                                .order_by(CloudMeetBrowserInput.sequence)
                            ).all()
                        )
                        # Erase ciphertext before dispatch; commands are never replayed after a crash.
                        payloads = [command.payload_ciphertext for command in commands]
                        db.execute(
                            delete(CloudMeetBrowserInput).where(
                                CloudMeetBrowserInput.setup_id == key
                            )
                        )
                        db.commit()
                    if display.returncode is not None:
                        raise RuntimeError("Display stopped")
                    pages = [tab for tab in context.pages if not tab.is_closed()]
                    if not pages:
                        raise RuntimeError("Browser closed")
                    page = pages[-1]
                    if not allowed_navigation(page.url):
                        failure = "UNSUPPORTED_BROWSER_PAGE"
                        break
                    for payload in payloads:
                        await apply_input(page, json.loads(cipher.decrypt(payload)))
                    if await account_matches(page, expected):
                        verified = True
                        with account_scope(user_id, email), SessionLocal() as db:
                            row = db.get(CloudMeetBrowserSetup, key)
                            if not validate_setup(db, row, worker_id):
                                return
                            changed = db.execute(
                                update(CloudMeetBrowserSetup)
                                .where(
                                    CloudMeetBrowserSetup.id == key,
                                    CloudMeetBrowserSetup.worker_id == worker_id,
                                    CloudMeetBrowserSetup.status.in_(ACTIVE_SETUP),
                                )
                                .values(
                                    status="VERIFYING",
                                    frame_ciphertext="",
                                    heartbeat_at=datetime.now(UTC),
                                )
                            ).rowcount
                            db.commit()
                            if not changed:
                                return
                        break
                    image = await page.screenshot(type="jpeg", quality=55, timeout=10_000)
                    frame = {
                        "image": base64.b64encode(image).decode(),
                        "width": WIDTH,
                        "height": HEIGHT,
                        "origin": "https://" + (urlsplit(page.url).hostname or ""),
                        "captured_at": datetime.now(UTC).isoformat(),
                    }
                    with account_scope(user_id, email), SessionLocal() as db:
                        row = db.get(CloudMeetBrowserSetup, key)
                        if not validate_setup(db, row, worker_id):
                            return
                        changed = db.execute(
                            update(CloudMeetBrowserSetup)
                            .where(
                                CloudMeetBrowserSetup.id == key,
                                CloudMeetBrowserSetup.worker_id == worker_id,
                                CloudMeetBrowserSetup.status.in_(ACTIVE_SETUP),
                            )
                            .values(
                                status="OPEN",
                                heartbeat_at=datetime.now(UTC),
                                frame_ciphertext=cipher.encrypt(json.dumps(frame)),
                            )
                        ).rowcount
                        db.commit()
                        if not changed:
                            return
                    await asyncio.sleep(0.7)
            finally:
                await context.close()
        if verified:
            # Verify that the headed sign-in really survives a new headless worker browser.
            verified = await verify_reusable_profile(settings, directory, expected)
            failure = "BROWSER_SESSION_NOT_REUSABLE"
    except Exception as exc:  # noqa: BLE001 - log type only, never browser or input details
        logger.warning("Meeting browser setup stopped (%s)", type(exc).__name__)
        verified = False
    finally:
        if display and display.returncode is None:
            display.terminate()
            try:
                await asyncio.wait_for(display.wait(), 5)
            except TimeoutError:
                display.kill()
                await display.wait()
        with account_scope(user_id, email), SessionLocal() as db:
            row = db.get(CloudMeetBrowserSetup, key)
            if row and validate_setup(db, row, worker_id):
                if verified and not shutdown.is_set():
                    if finish(db, row, "CONNECTED", commit=False):
                        # The terminal setup state and ready marker commit together.
                        set_browser_status(db, user_id, expected, "CONNECTED")
                else:
                    finish(db, row, "FAILED", "WORKER_SHUTDOWN" if shutdown.is_set() else failure)
