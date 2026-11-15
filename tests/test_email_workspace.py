import uuid

import pytest
from fastapi.testclient import TestClient

from app.config import get_base_settings
from app.main import app

ORIGIN = {"Origin": "http://127.0.0.1:3000"}
PASSWORD = "correct horse battery staple!"


@pytest.fixture
def auth(monkeypatch):
    monkeypatch.setenv("AUTH_REQUIRED", "true")
    get_base_settings.cache_clear()
    yield
    get_base_settings.cache_clear()


def register(browser, role):
    email = f"{uuid.uuid4()}@example.test"
    response = browser.post(
        "/auth/register",
        headers=ORIGIN,
        json={"email": email, "password": PASSWORD, "display_name": role.title(), "role": role},
    )
    assert response.status_code == 201, response.text
    return response.json()["user"]


def test_role_invitation_shared_decisions_and_demo_send_never_contacts_gmail(auth, monkeypatch):
    with TestClient(app) as client, TestClient(app) as assistant, TestClient(app) as stranger:
        client_user = register(client, "CLIENT")
        assistant_user = register(assistant, "ASSISTANT")
        register(stranger, "CLIENT")

        assert client_user["role"] == "CLIENT"
        assert assistant_user["role"] == "ASSISTANT"
        assert assistant.get("/email-workspace").json()["linked"] is False
        assert assistant.post("/email-workspace/demo", headers=ORIGIN).status_code == 409
        assert stranger.get("/email-workspace").json()["items"] == []

        invitation = client.post("/email-workspace/invitation", headers=ORIGIN)
        assert invitation.status_code == 200
        code = invitation.json()["code"]
        assert (
            client.post("/email-workspace/join", headers=ORIGIN, json={"code": code}).status_code
            == 403
        )
        assert (
            assistant.post("/email-workspace/join", headers=ORIGIN, json={"code": code}).status_code
            == 200
        )
        assert (
            assistant.post("/email-workspace/join", headers=ORIGIN, json={"code": code}).status_code
            == 400
        )

        watch = assistant.post(
            "/email-workspace/watches",
            headers=ORIGIN,
            json={
                "name": "Quote decisions",
                "condition": "New quotes that require client approval",
                "action": "NOTIFY",
                "instructions": "",
                "approval_policy": "REQUIRED",
            },
        )
        assert watch.status_code == 201, watch.text
        assert client.get("/email-workspace/watches").json()[0]["name"] == "Quote decisions"
        assert stranger.get("/email-workspace/watches").json() == []
        assert (
            assistant.patch(
                f"/email-workspace/watches/{watch.json()['id']}?enabled=false", headers=ORIGIN
            ).json()["enabled"]
            is False
        )

        assert client.post("/email-workspace/demo", headers=ORIGIN).json()["created"] == 5
        assert client.post("/email-workspace/demo", headers=ORIGIN).json()["created"] == 0
        all_items = assistant.get("/email-workspace").json()["items"]
        assert len(all_items) == 5
        assert len(client.get("/email-workspace").json()["items"]) == 2
        assert stranger.get("/email-workspace").json()["items"] == []
        assistant_item = next(
            row for row in all_items if row["responsibility"] == "ASSISTANT_TO_REPLY"
        )
        assert client.get(f"/email-workspace/{assistant_item['id']}").status_code == 404
        changed = assistant.patch(
            f"/email-workspace/{assistant_item['id']}",
            headers=ORIGIN,
            json={"responsibility": "CLIENT_TO_REPLY"},
        )
        assert changed.status_code == 200
        assert changed.json()["manual_override"] is True
        assert len(client.get("/email-workspace").json()["items"]) == 3
        from app.services import email_workspace as triage

        monkeypatch.setattr(
            triage,
            "decide",
            lambda *_: {
                "classification": "ASSISTANT_TO_REPLY",
                "confidence": 0.9,
                "reason": "Routine schedule",
                "summary": "A new assistant suggestion.",
                "review_required": False,
            },
        )
        reassessed = assistant.post(
            f"/email-workspace/{assistant_item['id']}/classify", headers=ORIGIN
        )
        assert reassessed.status_code == 200
        assert reassessed.json()["responsibility"] == "CLIENT_TO_REPLY"
        assert reassessed.json()["suggested_responsibility"] == "ASSISTANT_TO_REPLY"
        assert reassessed.json()["review_required"] is True
        assert (
            client.patch(
                f"/email-workspace/{assistant_item['id']}",
                headers=ORIGIN,
                json={"responsibility": "NO_REPLY_NEEDED"},
            ).status_code
            == 403
        )
        assert (
            client.patch(
                f"/email-workspace/{assistant_item['id']}",
                headers=ORIGIN,
                json={"responsibility": "ASSISTANT_TO_REPLY"},
            ).status_code
            == 200
        )
        assert client.get(f"/email-workspace/{assistant_item['id']}").status_code == 404
        assert (
            client.post(
                f"/email-workspace/{assistant_item['id']}/send", headers=ORIGIN, json={"body": "No"}
            ).status_code
            == 403
        )
        sent = assistant.post(
            f"/email-workspace/{assistant_item['id']}/send",
            headers=ORIGIN,
            json={"body": "Thanks, Friday works."},
        )
        assert sent.status_code == 200, sent.text
        assert sent.json()["simulated"] is True and sent.json()["sent"] is False
        assert (
            assistant.get(f"/email-workspace/{assistant_item['id']}").json()["status"] == "HANDLED"
        )
        assert client.post("/email-workspace/revoke", headers=ORIGIN).json()["revoked"] == 1
        assert assistant.get("/email-workspace").json()["linked"] is False
        assert assistant.get("/email-workspace/watches").status_code == 409


def test_same_credentials_switch_views_without_cross_account_access(auth):
    assistant_view = {**ORIGIN, "X-Jiraya-Workspace-Mode": "ASSISTANT"}
    with TestClient(app) as browser, TestClient(app) as stranger:
        user = register(browser, "CLIENT")
        register(stranger, "CLIENT")
        stranger.post("/email-workspace/demo", headers=ORIGIN)
        foreign_id = stranger.get("/email-workspace").json()["items"][0]["id"]
        browser.post("/email-workspace/demo", headers=ORIGIN)
        assert len(browser.get("/email-workspace").json()["items"]) == 2
        browser.post("/auth/logout", headers=ORIGIN)
        accepted = browser.post(
            "/auth/login",
            headers=ORIGIN,
            json={"email": user["email"], "password": PASSWORD, "role": "ASSISTANT"},
        )
        assert accepted.status_code == 200
        assert accepted.json()["user"]["role"] == "CLIENT"
        full = browser.get("/email-workspace", headers=assistant_view).json()
        assert full["role"] == "ASSISTANT" and full["linked"] is True
        assert len(full["items"]) == 5
        assert len(browser.get("/email-workspace").json()["items"]) == 2
        assert (
            browser.get(f"/email-workspace/{foreign_id}", headers=assistant_view).status_code == 404
        )
        routine = next(
            item for item in full["items"] if item["responsibility"] == "ASSISTANT_TO_REPLY"
        )
        changed = browser.patch(
            f"/email-workspace/{routine['id']}",
            headers=assistant_view,
            json={"responsibility": "CLIENT_TO_REPLY"},
        )
        assert changed.status_code == 200
        assert len(browser.get("/email-workspace").json()["items"]) == 3


def test_delegated_assistant_cannot_claim_client_role(auth):
    with TestClient(app) as browser:
        user = register(browser, "ASSISTANT")
        browser.post("/auth/logout", headers=ORIGIN)
        denied = browser.post(
            "/auth/login",
            headers=ORIGIN,
            json={"email": user["email"], "password": PASSWORD, "role": "CLIENT"},
        )
        assert denied.status_code == 403
        assert browser.get("/email-workspace").status_code == 401


def test_uncertain_send_cannot_be_reset_by_status_patch(auth):
    from app.db import SessionLocal
    from app.models import EmailConversation

    with TestClient(app) as client:
        register(client, "CLIENT")
        client.post("/email-workspace/demo", headers=ORIGIN)
        row_id = client.get("/email-workspace").json()["items"][0]["id"]
        with SessionLocal() as db:
            row = db.get(EmailConversation, row_id)
            row.status = "SEND_UNCERTAIN"
            db.commit()
        response = client.patch(
            f"/email-workspace/{row_id}", headers=ORIGIN, json={"status": "OPEN"}
        )
        assert response.status_code == 409
        with SessionLocal() as db:
            assert db.get(EmailConversation, row_id).status == "SEND_UNCERTAIN"
