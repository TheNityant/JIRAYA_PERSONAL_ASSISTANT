"""Self-service browser relay contracts. No real Google accounts or passwords."""

import asyncio
import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from app.config import get_base_settings
from app.db import SessionLocal, init_db
from app.main import app
from app.meet_cloud.identity import browser_status
from app.meet_cloud.portal import allowed_navigation, validate_setup
from app.meet_cloud.portal_store import claim_setup, expire, finish
from app.meet_cloud.worker import heartbeat
from app.models import (
    AuthSession,
    CloudMeetBrowserInput,
    CloudMeetBrowserSetup,
    CloudMeetSettings,
    User,
)
from app.security.identity import account_scope
from app.security.sessions import COOKIE_NAME, digest
from app.security.tokens import TokenCipher
from app.services.connection_store import ConnectionStore

ORIGIN = {"Origin": "http://localhost:3000"}
BASE = "/meet/cloud/browser/sessions"


@pytest.fixture
def portal(monkeypatch):
    for name, value in {
        "AUTH_REQUIRED": "true",
        "AUTOMATION_RUNTIME_MODE": "external",
        "ENABLE_EXTERNAL_ACTIONS": "true",
        "MEET_CLOUD_ENABLED": "true",
        "MEET_CLOUD_IDENTITY_MODE": "google_profile",
        "MEET_CLOUD_BROWSER_PORTAL_ENABLED": "true",
        "ASSEMBLYAI_API_KEY": "fixture",
        "NEBIUS_API_KEY": "fixture",
        "TOKEN_ENCRYPTION_KEY": Fernet.generate_key().decode(),
    }.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("MEET_CLOUD_GUEST_ONLY", "false")
    get_base_settings.cache_clear()
    init_db()
    identities = []
    with SessionLocal() as db:
        db.execute(delete(CloudMeetBrowserInput))
        db.execute(delete(CloudMeetBrowserSetup))
        db.commit()
    for _ in range(2):
        user, token = uuid4().hex, uuid4().hex
        with account_scope(user, user + "@example.test"), SessionLocal() as db:
            db.add(User(id=user, email=user + "@example.test"))
            db.add(
                AuthSession(
                    token_hash=digest(token),
                    user_id=user,
                    expires_at=datetime.now(UTC) + timedelta(hours=1),
                )
            )
            db.add(CloudMeetSettings(id=user, user_id=user, enabled=False))
            db.commit()
            ConnectionStore(db).upsert(
                user_id=user,
                provider="google_calendar",
                provider_account_id=user + "@example.test",
                display_name="Fixture",
                scopes=[],
                credential_ciphertext="fixture",
                token_expires_at=None,
            )
        identities.append((user, token))
    heartbeat("fixture-worker", "google_profile", True)
    yield identities
    with SessionLocal() as db:
        db.execute(delete(CloudMeetBrowserInput))
        db.execute(delete(CloudMeetBrowserSetup))
        db.commit()
    get_base_settings.cache_clear()


def client_for(identity):
    client = TestClient(app)
    client.cookies.set(COOKIE_NAME, identity[1])
    return client


def start(client):
    response = client.post(BASE, headers=ORIGIN, json={"consent": True})
    assert response.status_code == 200, response.text
    return response.json()["id"]


def open_row(key):
    with SessionLocal() as db:
        assert claim_setup(db, "fixture-worker") == key
        row = db.get(CloudMeetBrowserSetup, key)
        row.status = "OPEN"
        row.frame_ciphertext = TokenCipher(get_base_settings().token_encryption_key).encrypt(
            json.dumps({"image": "PRIVATE-FIXTURE-SCREEN", "origin": "https://accounts.google.com"})
        )
        db.commit()


def test_portal_requires_real_login_origin_consent_and_paused_attendance(portal):
    assert TestClient(app).post(BASE, headers=ORIGIN, json={"consent": True}).status_code == 401
    client = client_for(portal[0])
    assert client.post(BASE, json={"consent": True}).status_code == 403
    assert (
        client.post(
            BASE, headers={"Origin": "https://evil.example"}, json={"consent": True}
        ).status_code
        == 403
    )
    assert client.post(BASE, headers=ORIGIN, json={"consent": False}).status_code == 422
    with SessionLocal() as db:
        db.get(CloudMeetSettings, portal[0][0]).enabled = True
        db.commit()
    assert client.post(BASE, headers=ORIGIN, json={"consent": True}).status_code == 409


def test_portal_is_tenant_and_login_session_bound_and_never_caches_screens(portal):
    client = client_for(portal[0])
    key = start(client)
    open_row(key)
    response = client.get(BASE + "/" + key)
    assert response.json()["frame"]["image"] == "PRIVATE-FIXTURE-SCREEN"
    assert "no-store" in response.headers["cache-control"]
    other = client_for(portal[1])
    assert other.get(BASE + "/" + key).status_code == 404
    assert (
        other.post(
            BASE + "/" + key + "/input",
            headers=ORIGIN,
            json={"sequence": 1, "events": [{"kind": "text", "text": "blocked"}]},
        ).status_code
        == 404
    )
    # A second login for the same Jiraya user cannot hijack the first desktop.
    new_token = uuid4().hex
    with SessionLocal() as db:
        db.add(
            AuthSession(
                token_hash=digest(new_token),
                user_id=portal[0][0],
                expires_at=datetime.now(UTC) + timedelta(hours=1),
            )
        )
        db.commit()
    another = client_for((portal[0][0], new_token))
    assert another.get(BASE + "/" + key).status_code == 404


def test_inputs_encrypted_ordered_idempotent_and_cancel_erases_payloads(portal):
    client = client_for(portal[0])
    key = start(client)
    open_row(key)
    body = {"sequence": 1, "events": [{"kind": "text", "text": "fixture-typed-secret"}]}
    assert client.post(BASE + "/" + key + "/input", headers=ORIGIN, json=body).status_code == 200
    assert client.post(BASE + "/" + key + "/input", headers=ORIGIN, json=body).status_code == 200
    assert (
        client.post(
            BASE + "/" + key + "/input", headers=ORIGIN, json={**body, "sequence": 3}
        ).status_code
        == 409
    )
    with SessionLocal() as db:
        items = db.scalars(
            select(CloudMeetBrowserInput).where(CloudMeetBrowserInput.setup_id == key)
        ).all()
        assert len(items) == 1 and "fixture-typed-secret" not in items[0].payload_ciphertext
        assert "fixture-typed-secret" in TokenCipher(
            get_base_settings().token_encryption_key
        ).decrypt(items[0].payload_ciphertext)
    assert client.post(BASE + "/" + key + "/cancel", headers=ORIGIN).status_code == 200
    with SessionLocal() as db:
        row = db.get(CloudMeetBrowserSetup, key)
        assert row.status == "CANCELLED" and not row.frame_ciphertext and row.active_slot is None
        assert not db.scalar(
            select(CloudMeetBrowserInput).where(CloudMeetBrowserInput.setup_id == key)
        )
        assert not finish(db, row, "CONNECTED")  # A late worker cannot resurrect cancellation.
    assert client.post(BASE + "/" + key + "/input", headers=ORIGIN, json=body).status_code == 409


def test_single_desktop_capacity_and_expiry_cleanup(portal):
    client = client_for(portal[0])
    key = start(client)
    open_row(key)
    assert (
        client_for(portal[1]).post(BASE, headers=ORIGIN, json={"consent": True}).status_code == 409
    )
    with SessionLocal() as db:
        row = db.get(CloudMeetBrowserSetup, key)
        row.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        db.commit()
        expire(db)
        assert row.status == "EXPIRED" and row.frame_ciphertext == ""
    assert start(client_for(portal[1]))


@pytest.mark.parametrize(
    "change,reason",
    [
        ("logout", "JIRAYA_SIGNED_OUT"),
        ("calendar", "CALENDAR_CHANGED"),
        ("inactive", "VIEW_CLOSED"),
    ],
)
def test_worker_stops_on_revocation_account_change_and_inactivity(portal, change, reason):
    client = client_for(portal[0])
    key = start(client)
    open_row(key)
    with account_scope(portal[0][0], portal[0][0] + "@example.test"), SessionLocal() as db:
        row = db.get(CloudMeetBrowserSetup, key)
        if change == "logout":
            db.delete(db.get(AuthSession, digest(portal[0][1])))
        if change == "calendar":
            ConnectionStore(db).get_active(
                user_id=portal[0][0], provider="google_calendar"
            ).status = "DISCONNECTED"
        if change == "inactive":
            row.last_viewed_at = datetime.now(UTC) - timedelta(seconds=50)
        db.commit()
        assert not validate_setup(db, row, "fixture-worker")
        assert row.status == "CANCELLED" and row.reason == reason and row.frame_ciphertext == ""


@pytest.mark.parametrize(
    "event",
    [
        {"kind": "key", "key": "Control+L"},
        {"kind": "click", "x": 1.5},
        {"kind": "eval", "text": "evil"},
        {"kind": "text", "text": "x" * 513},
    ],
)
def test_remote_control_rejects_browser_hotkeys_scripts_and_unbounded_input(portal, event):
    client = client_for(portal[0])
    key = start(client)
    open_row(key)
    assert (
        client.post(
            BASE + "/" + key + "/input", headers=ORIGIN, json={"sequence": 1, "events": [event]}
        ).status_code
        == 422
    )


def test_navigation_excludes_meet_rooms_non_google_hosts_and_local_files():
    assert allowed_navigation("https://accounts.google.com/v3/signin")
    assert allowed_navigation("https://meet.google.com/")
    for url in [
        "file:///etc/passwd",
        "https://meet.google.com/abc-defg-hij",
        "https://accounts.google.com.evil.test/",
        "http://accounts.google.com/",
        "https://accounts.google.com:444/",
        "https://user:password@accounts.google.com/",
    ]:
        assert not allowed_navigation(url)


def test_portal_requires_worker_capability_even_if_calendar_is_authorized(portal):
    heartbeat("old-worker", "google_profile", False)
    assert (
        client_for(portal[0]).post(BASE, headers=ORIGIN, json={"consent": True}).status_code == 503
    )


@pytest.mark.parametrize("outcome", ["connected", "not_reusable", "cancelled"])
def test_worker_relay_flushes_profile_and_verifies_reuse_before_ready(
    portal, monkeypatch, tmp_path, outcome
):
    from app.meet_cloud import portal as runtime

    identity = portal[0]
    client = client_for(identity)
    key = start(client)
    with SessionLocal() as db:
        assert claim_setup(db, "fixture-worker") == key
    settings = get_base_settings()
    settings.meet_cloud_profile_root = str(tmp_path)
    text, options, processes = [], {}, []

    class Process:
        returncode = None

        def terminate(self):
            self.returncode = 0

        def kill(self):
            self.returncode = -1

        async def wait(self):
            return self.returncode

    async def start_process(*args, **kwargs):
        proc = Process()
        processes.append(proc)
        return proc

    async def nop(*args, **kwargs):
        pass

    async def insert(value):
        text.append(value)

    async def screen(**kwargs):
        return b"fixture-jpeg"

    page = SimpleNamespace(
        url="https://accounts.google.com/signin",
        is_closed=lambda: False,
        goto=nop,
        screenshot=screen,
        keyboard=SimpleNamespace(insert_text=insert, press=nop),
        mouse=SimpleNamespace(click=nop, wheel=nop),
    )

    async def account(*args):
        return bool(text)

    context = SimpleNamespace(pages=[page], route=nop, close=nop)

    async def launch(*args, **kwargs):
        options.update(kwargs)
        return context

    class Playwright:
        async def __aenter__(self):
            return SimpleNamespace(chromium=SimpleNamespace(launch_persistent_context=launch))

        async def __aexit__(self, *args):
            pass

    async def reusable(*args):
        return outcome != "not_reusable"

    async def after_frame(seconds):
        if seconds != 0.7:
            return
        view = client.get(BASE + "/" + key).json()
        assert view["frame"]["origin"] == "https://accounts.google.com"
        if outcome == "cancelled":
            client.post(BASE + "/" + key + "/cancel", headers=ORIGIN)
        else:
            assert (
                client.post(
                    BASE + "/" + key + "/input",
                    headers=ORIGIN,
                    json={
                        "sequence": 1,
                        "events": [{"kind": "text", "text": "fixture-only-input"}],
                    },
                ).status_code
                == 200
            )

    import playwright.async_api

    original_exists = runtime.Path.exists
    monkeypatch.setattr(
        runtime.Path,
        "exists",
        lambda path: True if str(path) == "/tmp/.X11-unix/X99" else original_exists(path),
    )
    monkeypatch.setattr(runtime.asyncio, "create_subprocess_exec", start_process)
    monkeypatch.setattr(runtime.asyncio, "sleep", after_frame)
    monkeypatch.setattr(runtime, "account_matches", account)
    monkeypatch.setattr(runtime, "verify_reusable_profile", reusable)
    monkeypatch.setattr(playwright.async_api, "async_playwright", Playwright)
    asyncio.run(runtime.run_setup(key, "fixture-worker", settings, asyncio.Event()))
    with SessionLocal() as db:
        row = db.get(CloudMeetBrowserSetup, key)
        assert (
            row.status
            == {"connected": "CONNECTED", "not_reusable": "FAILED", "cancelled": "CANCELLED"}[
                outcome
            ]
        )
        assert not row.frame_ciphertext
        assert not db.scalar(
            select(CloudMeetBrowserInput).where(CloudMeetBrowserInput.setup_id == key)
        )
        assert browser_status(db, identity[0], identity[0] + "@example.test")["state"] == (
            "CONNECTED" if outcome == "connected" else "SETUP_REQUIRED"
        )
    assert options["headless"] is False
    assert "DATABASE_URL" not in options["env"] and "TOKEN_ENCRYPTION_KEY" not in options["env"]
    assert all(p.returncode is not None for p in processes)


@pytest.mark.skipif(
    not __import__("os").environ.get("JIRAYA_CHROMIUM_CONFIG"),
    reason="Real browser fixture not configured",
)
def test_complete_frontend_relay_reuse_attendance_and_notes(portal, monkeypatch, tmp_path):
    """Real React/proxy/API/DB/Chromium flow; Google, audio and AI are explicit fixtures."""
    import os
    import socket
    import subprocess
    import threading
    import time
    from pathlib import Path
    from urllib.parse import urlsplit

    import playwright.async_api
    import uvicorn

    from app.meet_cloud import browser, worker
    from app.meet_cloud import portal as runtime
    from app.meet_cloud.store import enqueue
    from app.models import CloudMeetSession

    user, token = portal[0]
    settings = get_base_settings()
    settings.meet_cloud_profile_root = str(tmp_path)
    settings.frontend_origins = "http://127.0.0.1:3131,http://localhost:3000"
    binary = json.loads(Path(os.environ["JIRAYA_CHROMIUM_CONFIG"]).read_text())
    original_launch = playwright.async_api.BrowserType.launch_persistent_context
    original_process = asyncio.create_subprocess_exec
    launched = []
    google_signins = []
    left = []
    fixture_requests = []

    async def launch(browser_type, user_data_dir, *args, **kwargs):
        launched.append(str(user_data_dir))
        kwargs.update(headless=True, executable_path=binary["executablePath"], args=binary["args"])
        context = await original_launch(browser_type, user_data_dir, *args, **kwargs)

        async def fixture(route):
            host = urlsplit(route.request.url).hostname
            path = urlsplit(route.request.url).path
            fixture_requests.append((host, path))
            if host == "accounts.google.com":
                google_signins.append("login-screen")
                await route.fulfill(
                    content_type="text/html",
                    body="""<html><body><h1>Controlled Google sign-in fixture</h1><form onsubmit="event.preventDefault();if(document.querySelector('input').value==='fixture-signin-only'){document.cookie='fixture_google_session=1; Domain=.google.com; Path=/; Max-Age=86400; Secure; SameSite=Lax';location.href='https://meet.google.com/'}"><input type="password" aria-label="Fixture sign-in" style="position:absolute;left:80px;top:100px;width:300px;height:50px"><button style="position:absolute;top:180px">Sign in</button></form></body></html>""",
                )
            elif host == "meet.google.com":
                cookies = await context.cookies("https://meet.google.com")
                if not any(c["name"] == "fixture_google_session" for c in cookies):
                    await route.fulfill(
                        content_type="text/html",
                        body="<script>location.href='https://accounts.google.com/signin'</script>",
                    )
                    return
                chip = f'<button aria-label="Google Account: Fixture ({user}@example.test)">Account</button>'
                if path == "/":
                    await route.fulfill(content_type="text/html", body=chip)
                else:
                    await route.fulfill(
                        content_type="text/html",
                        body=chip
                        + """<button aria-label="Turn microphone on">Muted</button><button aria-label="Turn camera on">Camera off</button><button onclick="this.remove();document.querySelector('#call').hidden=false">Join now</button><div id="call" hidden><button aria-label="Reactions">Reactions</button><button aria-label="Leave call" onclick="fetch('https://meet.google.com/fixture-left');document.body.innerHTML='You left the meeting'">Leave call</button></div>""",
                    )
                if path == "/fixture-left":
                    left.append("left")
            else:
                await route.abort()

        await context.route("**/*", fixture)
        return context

    class Display:
        returncode = None

        def terminate(self):
            self.returncode = 0

        def kill(self):
            self.returncode = -1

        async def wait(self):
            return self.returncode

    async def process(*args, **kwargs):
        if args[0] == "Xvfb":
            return Display()
        return await original_process(*args, **kwargs)

    original_exists = runtime.Path.exists
    monkeypatch.setattr(
        runtime.Path,
        "exists",
        lambda p: True if str(p) == "/tmp/.X11-unix/X99" else original_exists(p),
    )
    monkeypatch.setattr(asyncio, "create_subprocess_exec", process)
    monkeypatch.setattr(playwright.async_api.BrowserType, "launch_persistent_context", launch)
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
    server_thread = threading.Thread(target=server.run, daemon=True)
    server_thread.start()
    stop = threading.Event()
    worker_errors = []
    original_warning = runtime.logger.warning

    def fixture_warning(*args, **kwargs):
        import sys

        error = sys.exc_info()[1]
        if error is not None:
            worker_errors.append(str(error))  # Controlled fixture errors only, never live sign-in.
        original_warning(*args, **kwargs)

    monkeypatch.setattr(runtime.logger, "warning", fixture_warning)

    def run_worker():
        async def loop():
            while not stop.is_set():
                worker.heartbeat("fixture-worker", "google_profile", True)
                with SessionLocal() as db:
                    key = claim_setup(db, "fixture-worker")
                if key:
                    await runtime.run_setup(key, "fixture-worker", settings, asyncio.Event())
                await asyncio.sleep(0.1)

        try:
            asyncio.run(loop())
        except Exception as exc:  # noqa: BLE001 - collect fixture worker failures without credentials
            worker_errors.append(type(exc).__name__)

    thread = threading.Thread(target=run_worker, daemon=True)
    thread.start()
    try:
        for _ in range(100):
            if server.started:
                break
            time.sleep(0.05)
        env = {
            **os.environ,
            "PORTAL_FIXTURE_API": f"http://127.0.0.1:{port}",
            "PORTAL_FIXTURE_COOKIE": token,
        }
        result = subprocess.run(
            ["node", "tests/meet-browser-live-relay.cjs"],
            check=False,
            cwd=Path(__file__).resolve().parents[1] / "web",
            env=env,
            text=True,
            capture_output=True,
            timeout=150,
        )
        with SessionLocal() as db:
            states = [(r.status, r.reason) for r in db.scalars(select(CloudMeetBrowserSetup)).all()]
        assert result.returncode == 0, (
            result.stdout + result.stderr + str(worker_errors) + str(states) + str(fixture_requests)
        )
        assert "PASS:" in result.stdout
    finally:
        stop.set()
        thread.join(timeout=15)
        server.should_exit = True
        server_thread.join(timeout=10)
    assert not worker_errors
    with SessionLocal() as db:
        assert browser_status(db, user, user + "@example.test")["state"] == "CONNECTED"
        assert not db.scalar(select(CloudMeetBrowserInput))
        assert not db.scalar(
            select(CloudMeetBrowserSetup.frame_ciphertext).where(
                CloudMeetBrowserSetup.user_id == user
            )
        )

    # Next, use the same profile in two attendance browsers without sign-in screens.
    async def nop(*args, **kwargs):
        pass

    monkeypatch.setattr(
        worker, "AudioSink", lambda name: SimpleNamespace(name=name, setup=nop, close=nop)
    )

    async def speech(settings, audio, on_turn):
        await on_turn(
            {
                "order": 0,
                "text": "We agreed to ship the fix.",
                "speaker": "fixture",
                "at": datetime.now(UTC).isoformat(),
            }
        )
        await on_turn(
            {
                "order": 1,
                "text": "Jiraya, please leave the meeting now.",
                "speaker": "fixture",
                "at": datetime.now(UTC).isoformat(),
            }
        )
        await asyncio.sleep(60)

    monkeypatch.setattr(browser, "listen", speech)
    monkeypatch.setattr(worker, "NebiusProvider", lambda settings: object())
    monkeypatch.setattr(
        worker,
        "summarize",
        lambda provider, turns: json.dumps({"overview": "We agreed to ship the fix."}),
    )

    def no_google(*args):
        raise RuntimeError("Fixture: no Google transcript")

    monkeypatch.setattr(worker, "google_transcript", no_google)
    signins_after_setup = len(google_signins)
    for index in range(2):
        now = datetime.now(UTC)
        event = {
            "id": f"portal-fixture-{index}",
            "summary": "Fixture meeting",
            "url": "https://meet.google.com/abc-defg-hij",
            "start": now.isoformat(),
            "end": (now + timedelta(minutes=10)).isoformat(),
        }
        monkeypatch.setattr(
            worker,
            "upcoming_meetings",
            lambda db, event=event: {
                "status": "READY",
                "calendar_account": user + "@example.test",
                "items": [event],
            },
        )
        with account_scope(user, user + "@example.test"), SessionLocal() as db:
            enqueue(db, user, user + "@example.test", event)
            row = db.scalar(
                select(CloudMeetSession).where(CloudMeetSession.event_id == event["id"])
            )
            row.status = "STARTING"
            row.worker_id = "fixture-worker"
            db.commit()
            key = row.id
        asyncio.run(worker.attend(key, "fixture-worker", asyncio.Event()))
        worker.make_summary(key)
        with account_scope(user, user + "@example.test"), SessionLocal() as db:
            row = db.get(CloudMeetSession, key)
            assert row.status == "COMPLETED", row.details
            assert row.details["reason"] == "EXPLICIT_SPOKEN_LEAVE"
            assert row.details["summary_status"] == "READY"
            assert row.details["summary_source"] == "assemblyai"
            assert "ship the fix" in TokenCipher(settings.token_encryption_key).decrypt(
                row.summary_ciphertext
            )
    assert len(google_signins) == signins_after_setup == 1
    assert len(set(launched)) == 1 and len(launched) >= 4
    assert len(left) == 2
