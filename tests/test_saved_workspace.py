from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from app.config import get_base_settings
from app.db import SessionLocal
from app.main import app
from app.security.identity import account_scope
from app.security.sessions import digest
from app.security.tokens import TokenCipher
from app.services.connection_store import ConnectionStore
from app.services.runtime_state_store import RuntimeStateStore


def test_sign_out_preserves_connections_rules_and_encryption(monkeypatch):
    from uuid import uuid4

    monkeypatch.setenv("AUTH_REQUIRED", "true")
    key = Fernet.generate_key().decode()
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", key)
    get_base_settings.cache_clear()
    origin = {"Origin": "http://localhost:3000"}
    credentials = {
        "email": str(uuid4()) + "@example.test",
        "password": "fixture password with enough length",
    }
    try:
        with TestClient(app) as client:
            with SessionLocal() as db:
                RuntimeStateStore(db).delete("auth-attempts:" + digest("testclient"))
            user = client.post(
                "/auth/register", json={**credentials, "display_name": "Fixture"}, headers=origin
            ).json()["user"]
            rule = client.post(
                "/automations",
                json={
                    "name": "My saved Calendar automation",
                    "action_config": {"type": "DRAFT_REPLY"},
                },
                headers=origin,
            ).json()
            with account_scope(user["id"], user["email"]), SessionLocal() as db:
                store = ConnectionStore(db)
                for provider in ("google", "google_calendar", "github"):
                    store.upsert(
                        user_id=user["id"],
                        provider=provider,
                        provider_account_id="fixture@example.test",
                        display_name="Fixture",
                        scopes=[],
                        token_expires_at=None,
                        credential_ciphertext=TokenCipher(key).encrypt("fixture credentials"),
                    )
            assert client.post("/auth/logout", headers=origin).status_code == 200
            assert client.get("/automations").status_code == 401
            assert client.post("/auth/login", json=credentials, headers=origin).status_code == 200
            assert any(row["id"] == rule["id"] for row in client.get("/automations").json())
            assert len(client.get("/connections").json()["connections"]) == 3
            with account_scope(user["id"], user["email"]), SessionLocal() as db:
                for row in ConnectionStore(db).list_for_user(user["id"]):
                    assert (
                        TokenCipher(key).decrypt(row.credential_ciphertext) == "fixture credentials"
                    )
    finally:
        get_base_settings.cache_clear()
