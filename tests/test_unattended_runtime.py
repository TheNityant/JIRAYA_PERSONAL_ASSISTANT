import asyncio
import threading
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.config import Settings, get_base_settings
from app.db import Base, TenantSession
from app.models import AuthSession, Notification
from app.runtime import background, worker
from app.runtime.status import monitor_status
from app.security.identity import account_scope, identity
from app.services.connection_store import ConnectionStore
from app.services.notification_store import NotificationStore
from app.services.runtime_state_store import RuntimeStateStore


@pytest.fixture
def database(tmp_path, monkeypatch):
    engine = create_engine("sqlite+pysqlite:///" + str(tmp_path / "worker.db"))
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, class_=TenantSession, expire_on_commit=False)
    monkeypatch.setattr(background, "SessionLocal", sessions)
    monkeypatch.setattr(worker, "init_db", lambda: Base.metadata.create_all(engine))
    yield sessions
    engine.dispose()


def connect(sessions, user_id, provider, *, active=True):
    with account_scope(user_id, user_id + "@example.com"), sessions() as db:
        store = ConnectionStore(db)
        store.ensure_user(user_id=user_id, email=user_id + "@example.com")
        store.upsert(
            user_id=user_id,
            provider=provider,
            provider_account_id=user_id + "@example.com",
            display_name=user_id,
            scopes=[],
            credential_ciphertext="fixture-not-a-token",
            token_expires_at=None,
        )
        if not active:
            store.disconnect(user_id=user_id, provider=provider)


def test_worker_runs_without_browser_or_auth_sessions_and_resumes_saved_state(
    database, monkeypatch
):
    for name in ("alice", "bob"):
        connect(database, name, "google")
    connect(database, "disconnected", "google", active=False)
    observations = []

    class Service:
        def __init__(self, *, db, settings):
            self.db, self.user = db, settings.dev_user_id

        def poll(self):
            assert identity.get()[0] == self.user
            assert not self.db.scalars(select(AuthSession)).all()
            store = RuntimeStateStore(self.db)
            key = "fixture-cursor:" + self.user
            previous = (store.get(key) or {}).get("sequence", 0)
            observations.append((self.user, previous))
            store.set(key, {"sequence": previous + 1})
            NotificationStore(self.db).create_once(
                user_id=self.user,
                category="INFORMATION",
                title="Offline event",
                message="Persisted without any HTTP requests",
                dedupe_key="source-event-1",
            )
            return {"decision": "POLL_COMPLETE"}

    monkeypatch.setattr(background, "GmailRuntimeService", Service)
    settings = Settings(
        _env_file=None,
        automation_runtime_mode="external",
        auth_required=True,
        gmail_background_monitor_enabled=True,
        calendar_background_monitor_enabled=False,
        github_background_monitor_enabled=False,
    )

    async def run_once():
        stop = asyncio.Event()
        task = asyncio.create_task(worker.serve(settings, stop))
        # Wait for the actual monitor loop, not an HTTP Poll/Debug request.
        for _ in range(100):
            if len(observations) % 2 == 0 and len(observations) >= target:
                break
            await asyncio.sleep(0.025)
        stop.set()
        await asyncio.wait_for(task, timeout=3)

    target = 2
    asyncio.run(run_once())
    target = 4
    asyncio.run(run_once())
    assert sorted(observations) == [("alice", 0), ("alice", 1), ("bob", 0), ("bob", 1)]
    with database() as db:
        assert len(db.scalars(select(Notification)).all()) == 2
    assert identity.get() is None


def test_one_account_failure_does_not_stop_other_accounts(database):
    connect(database, "alice", "test")
    connect(database, "bob", "test")
    observed = []

    class Service:
        def __init__(self, *, db, settings):
            self.user = settings.dev_user_id

        def poll(self):
            observed.append(self.user)
            if self.user == "alice":
                raise ValueError("provider-secret-must-not-leak")
            return {"decision": "OK"}

    background.poll_accounts(Settings(_env_file=None, auth_required=True), "test", "test", Service)
    assert set(observed) == {"alice", "bob"}
    with database() as db:
        store = RuntimeStateStore(db)
        assert store.get("test-monitor-health:alice")["last_error"] == "ValueError"
        assert store.get("test-monitor-health:bob")["last_status"] == "OK"


def test_stop_drains_current_account_and_skips_remaining_accounts(database):
    connect(database, "alice", "test")
    connect(database, "bob", "test")
    stop = threading.Event()
    observed = []

    class Service:
        def __init__(self, *, db, settings):
            self.user = settings.dev_user_id

        def poll(self):
            observed.append(self.user)
            stop.set()
            return {"decision": "COMPLETED_BEFORE_SHUTDOWN"}

    background.poll_accounts(
        Settings(_env_file=None, auth_required=True), "test", "test", Service, stop
    )
    assert len(observed) == 1
    with database() as db:
        assert RuntimeStateStore(db).get("test-monitor-health:" + observed[0])["last_status"] == "OK"
        other = ({"alice", "bob"} - set(observed)).pop()
        assert RuntimeStateStore(db).get("test-monitor-health:" + other) is None


@pytest.mark.parametrize("mode,enabled", [("embedded", True), ("external", False)])
def test_worker_rejects_configuration_that_would_duplicate_or_disable_monitors(mode, enabled):
    settings = Settings(
        _env_file=None,
        automation_runtime_mode=mode,
        gmail_background_monitor_enabled=enabled,
        calendar_background_monitor_enabled=False,
        github_background_monitor_enabled=False,
    )
    with pytest.raises(RuntimeError):
        asyncio.run(worker.serve(settings))


def test_external_api_never_starts_embedded_monitors(monkeypatch):
    from app import main

    monkeypatch.setenv("AUTOMATION_RUNTIME_MODE", "external")
    monkeypatch.setenv("GMAIL_BACKGROUND_MONITOR_ENABLED", "true")
    get_base_settings.cache_clear()

    def forbidden(*args):
        raise AssertionError("API started a duplicate monitor")

    monkeypatch.setattr(main, "running_monitors", forbidden)
    try:
        with TestClient(main.app) as client:
            assert client.get("/health").status_code == 200
    finally:
        get_base_settings.cache_clear()


@pytest.mark.parametrize("age,healthy", [(0, True), (179, True), (181, False), (-60, False)])
def test_old_success_does_not_claim_monitor_is_running(age, healthy):
    health = {
        "last_status": "OK",
        "last_poll_at": (datetime.now(UTC) - timedelta(seconds=age)).isoformat(),
    }
    assert monitor_status(health, enabled=True, poll_seconds=15)["operational"] is healthy
    assert not monitor_status(health, enabled=False, poll_seconds=15)["operational"]
    assert not monitor_status({**health, "poll_source": "manual"}, enabled=True, poll_seconds=15)[
        "operational"
    ]


def test_runtime_status_is_authenticated_and_only_reads_current_user(monkeypatch):
    from app.db import SessionLocal
    from app.main import app

    monkeypatch.setenv("AUTH_REQUIRED", "true")
    monkeypatch.setenv("GMAIL_BACKGROUND_MONITOR_ENABLED", "true")
    monkeypatch.setenv("AUTOMATION_RUNTIME_MODE", "external")
    get_base_settings.cache_clear()
    try:
        with TestClient(app) as client:
            assert client.get("/runtime/status").status_code == 401
            from app.security.sessions import digest

            with SessionLocal() as db:
                RuntimeStateStore(db).delete("auth-attempts:" + digest("testclient"))
            result = client.post(
                "/auth/register",
                headers={"Origin": "http://localhost:3000"},
                json={
                    "email": str(uuid4()) + "@example.com",
                    "password": "correct horse battery staple!",
                    "display_name": "Test user",
                },
            )
            assert result.status_code == 201
            user = result.json()["user"]
            connect(SessionLocal, user["id"], "google")
            with SessionLocal() as db:
                for user_id, age in ((user["id"], 600), ("another-user", 0)):
                    RuntimeStateStore(db).set(
                        "gmail-monitor-health:" + user_id,
                        {
                            "last_status": "OK",
                            "last_poll_at": (
                                datetime.now(UTC) - timedelta(seconds=age)
                            ).isoformat(),
                        },
                    )
            response = client.get("/runtime/status")
            assert response.status_code == 200
            data = response.json()
            assert data["mode"] == "external" and not data["browser_required"]
            assert data["monitors"]["gmail"]["status"] == "STALE"
            assert not data["monitors"]["gmail"]["operational"]
            assert "credential" not in response.text and "another-user" not in response.text
            assert not client.get("/runtime/gmail/status").json()["background_monitor_enabled"]
    finally:
        get_base_settings.cache_clear()
