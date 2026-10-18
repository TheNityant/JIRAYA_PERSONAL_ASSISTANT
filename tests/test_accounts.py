import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect, select, text

from app.config import get_base_settings
from app.db import SessionLocal
from app.main import app
from app.models import AuthSession, OAuthState, RuntimeState, User
from app.security.identity import account_scope
from app.services.automation_execution_store import AutomationExecutionStore
from app.services.notification_store import NotificationStore

ORIGIN = {"Origin": "http://127.0.0.1:3000"}
PASSWORD = "correct horse battery staple!"


@pytest.fixture
def auth(monkeypatch):
    monkeypatch.setenv("AUTH_REQUIRED", "true")
    get_base_settings.cache_clear()
    with TestClient(app) as setup:
        pass
    with SessionLocal() as db:
        for row in db.scalars(
            select(RuntimeState).where(RuntimeState.key.startswith("auth-attempts:"))
        ).all():
            db.delete(row)
        db.commit()
    yield
    get_base_settings.cache_clear()


def signup(client):
    email = str(uuid.uuid4()) + "@example.com"
    response = client.post(
        "/auth/register",
        json={"email": email, "password": PASSWORD, "display_name": "Test User"},
        headers=ORIGIN,
    )
    assert response.status_code == 201, response.text
    return response.json()["user"]


def make_automation(client):
    response = client.post(
        "/automations",
        json={"name": "Private workflow", "action_config": {"type": "NOTIFY"}},
        headers=ORIGIN,
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_sign_in_create_account_sign_out_entirely_via_api(auth):
    with TestClient(app) as client:
        assert client.get("/automations").status_code == 401
        result = client.get("/auth/me").json()
        assert result["auth_required"] and result["user"] is None
        user = signup(client)
        assert client.get("/auth/me").json()["user"]["id"] == user["id"]
        cookie = client.cookies.get("jiraya_session")
        assert cookie
        with SessionLocal() as db:
            stored = db.scalars(select(User).where(User.id == user["id"])).one()
            assert stored.password_hash and PASSWORD not in stored.password_hash
            assert db.get(AuthSession, cookie) is None
        assert client.get("/automations").status_code == 200
        assert client.post("/auth/logout", headers=ORIGIN).status_code == 200
        assert client.get("/automations").status_code == 401
        response = client.post(
            "/auth/login", json={"email": user["email"], "password": PASSWORD}, headers=ORIGIN
        )
        assert response.status_code == 200
        assert "HttpOnly" in response.headers["set-cookie"]
        assert client.get("/auth/me").json()["user"]["id"] == user["id"]


def test_two_users_cannot_read_or_modify_each_others_work(auth):
    with TestClient(app) as alice, TestClient(app) as bob:
        a, b = signup(alice), signup(bob)
        workflow = make_automation(alice)
        assert bob.get("/automations").json() == []
        assert alice.get("/automations").json()[0]["id"] == workflow["id"]
        assert (
            bob.put(
                f"/automations/{workflow['id']}", json={"name": "Stolen"}, headers=ORIGIN
            ).status_code
            == 404
        )
        assert bob.delete(f"/automations/{workflow['id']}", headers=ORIGIN).status_code == 404
        assert (
            bob.post(
                f"/automations/{workflow['id']}/run/gmail", json={}, headers=ORIGIN
            ).status_code
            == 404
        )
        with account_scope(a["id"], a["email"]), SessionLocal() as db:
            execution = AutomationExecutionStore(db).create(
                automation_id=workflow["id"],
                correlation_id="test-private",
                source_event_id="shared-message-id",
                source_thread_id="thread",
                action_type="DRAFT_REPLY",
                approval_policy="REQUIRED",
                status="AWAITING_APPROVAL",
                match_result={},
                draft_body="Private",
            )
            execution_id = execution.id
        assert bob.get(f"/automation-executions/{execution_id}").status_code == 404
        assert (
            bob.post(
                f"/automation-executions/{execution_id}/approve", json={}, headers=ORIGIN
            ).status_code
            == 404
        )
        assert bob.get("/automation-executions").json() == []
        assert alice.get(f"/automation-executions/{execution_id}").json()["draft_body"] == "Private"


def test_notification_ids_and_dedupe_are_account_scoped(auth):
    with TestClient(app) as alice, TestClient(app) as bob:
        a, b = signup(alice), signup(bob)
        ids = []
        for user in (a, b):
            with account_scope(user["id"], user["email"]), SessionLocal() as db:
                row = NotificationStore(db).create_once(
                    user_id=user["id"],
                    category="INFORMATION",
                    title="Private",
                    message=user["email"],
                    dedupe_key="same-source-event",
                )
                ids.append(row.id)
        assert ids[0] != ids[1]
        assert alice.get("/notifications").json()["items"][0]["message"] == a["email"]
        assert bob.get("/notifications").json()["items"][0]["message"] == b["email"]
        assert bob.post(f"/notifications/{ids[0]}/read", headers=ORIGIN).status_code == 404


def test_cross_origin_writes_and_missing_origin_are_rejected(auth):
    with TestClient(app) as client:
        signup(client)
        assert (
            client.post(
                "/automations", json={"name": "bad"}, headers={"Origin": "https://attacker.example"}
            ).status_code
            == 403
        )
        assert client.post("/auth/logout").status_code == 403
        assert client.post("/auth/logout", headers=ORIGIN).status_code == 200


def test_expired_and_forged_sessions_fail(auth):
    from app.security.sessions import digest

    with TestClient(app) as client:
        signup(client)
        token = client.cookies.get("jiraya_session")
        with SessionLocal() as db:
            session = db.get(AuthSession, digest(token))
            session.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
            db.commit()
        assert client.get("/automations").status_code == 401
        client.cookies.clear()
        client.cookies.set("jiraya_session", "forged")
        assert client.get("/automations").status_code == 401
        assert client.get("/auth/me").json()["user"] is None


def test_oauth_json_start_and_state_are_bound_to_the_signed_in_account(auth, monkeypatch):
    from cryptography.fernet import Fernet
    from urllib.parse import parse_qs, urlparse

    monkeypatch.setenv("GITHUB_OAUTH_CLIENT_ID", "test-client")
    monkeypatch.setenv("GITHUB_OAUTH_CLIENT_SECRET", "test-secret")
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", Fernet.generate_key().decode())
    get_base_settings.cache_clear()
    with TestClient(app) as alice, TestClient(app) as bob:
        a, b = signup(alice), signup(bob)
        response = alice.get("/connections/github/start?response_mode=json")
        assert response.status_code == 200
        url = response.json()["authorization_url"]
        assert url.startswith("https://github.com/login/oauth/authorize?")
        state = parse_qs(urlparse(url).query)["state"][0]
        with SessionLocal() as db:
            assert db.get(OAuthState, state).user_id == a["id"]
        callback = bob.get(
            "/connections/github/callback",
            params={"state": state, "code": "fake"},
            follow_redirects=False,
        )
        assert "invalid_or_expired_state" in callback.headers["location"]
        with SessionLocal() as db:
            assert db.get(OAuthState, state) is not None
        assert bob.get("/connections").json()["connections"] == []


def test_missing_provider_setup_is_visible_in_app_not_raw_navigation(auth, monkeypatch):
    monkeypatch.setenv("GITHUB_OAUTH_CLIENT_ID", "")
    monkeypatch.setenv("GITHUB_OAUTH_CLIENT_SECRET", "")
    get_base_settings.cache_clear()
    with TestClient(app) as client:
        signup(client)
        catalog = {item["id"]: item for item in client.get("/connections").json()["catalog"]}
        assert catalog["github"]["status"] == "SETUP_REQUIRED"
        result = client.get("/connections/github/start?response_mode=json")
        assert result.status_code == 503
        assert ".env" not in result.text
        assert "app owner" in result.json()["detail"]


def test_additive_migration_preserves_legacy_records(tmp_path, monkeypatch):
    import app.db as database

    engine = create_engine("sqlite+pysqlite:///" + str(tmp_path / "legacy.db"))
    with engine.begin() as connection:
        connection.execute(
            text("CREATE TABLE automations (id VARCHAR(36) PRIMARY KEY, name VARCHAR(255))")
        )
        connection.execute(text("INSERT INTO automations (id, name) VALUES ('old', 'Preserved')"))
        connection.execute(
            text(
                "CREATE TABLE users (id VARCHAR(64) PRIMARY KEY, email VARCHAR(320), display_name VARCHAR(255), created_at DATETIME)"
            )
        )
    monkeypatch.setattr(database, "engine", engine)
    database.init_db()
    database.init_db()
    with engine.connect() as connection:
        row = connection.execute(text("SELECT name, user_id FROM automations WHERE id='old'")).one()
        assert row.name == "Preserved"
        assert row.user_id == database.settings.dev_user_id
        assert "password_hash" in {col["name"] for col in inspect(connection).get_columns("users")}
    engine.dispose()


def test_background_poll_runs_in_each_connected_account_scope(auth):
    from app.config import get_settings
    from app.models import Automation
    from app.runtime.background import poll_accounts
    from app.services.connection_store import ConnectionStore

    with TestClient(app) as alice, TestClient(app) as bob:
        users = [signup(alice), signup(bob)]
        workflows = [make_automation(alice), make_automation(bob)]
        for user in users:
            with account_scope(user["id"], user["email"]), SessionLocal() as db:
                ConnectionStore(db).upsert(
                    user_id=user["id"],
                    provider="test-provider",
                    provider_account_id=user["email"],
                    display_name="Test",
                    scopes=[],
                    credential_ciphertext="not-a-live-token",
                    token_expires_at=None,
                )
        observed = {}

        class Service:
            def __init__(self, *, db, settings):
                self.db, self.settings = db, settings

            def poll(self):
                observed[self.settings.dev_user_id] = [
                    row.id for row in self.db.scalars(select(Automation)).all()
                ]
                if self.settings.dev_user_id == users[0]["id"]:
                    raise RuntimeError("One account unavailable")
                return {"decision": "OK"}

        poll_accounts(get_settings(), "test-provider", "test-provider", Service)
        assert observed[users[0]["id"]] == [workflows[0]["id"]]
        assert observed[users[1]["id"]] == [workflows[1]["id"]]
        with SessionLocal() as db:
            assert (
                db.get(RuntimeState, f"test-provider-monitor-health:{users[0]['id']}").value[
                    "last_status"
                ]
                == "ERROR"
            )
            assert (
                db.get(RuntimeState, f"test-provider-monitor-health:{users[1]['id']}").value[
                    "last_status"
                ]
                == "OK"
            )


def test_sign_in_attempts_are_rate_limited(auth):
    from app.security.sessions import digest
    from app.services.runtime_state_store import RuntimeStateStore
    import time

    with TestClient(app) as client:
        with SessionLocal() as db:
            RuntimeStateStore(db).set(
                "auth-attempts:" + digest("testclient"), {"start": time.time(), "count": 20}
            )
        response = client.post(
            "/auth/login",
            json={"email": "missing@example.com", "password": PASSWORD},
            headers=ORIGIN,
        )
        assert response.status_code == 429


def test_production_cannot_disable_authentication():
    from app.config import Settings

    with pytest.raises(ValueError):
        Settings(app_env="production", auth_required=False)
