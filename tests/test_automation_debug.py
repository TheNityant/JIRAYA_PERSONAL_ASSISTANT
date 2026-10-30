from types import SimpleNamespace

from fastapi.testclient import TestClient

from app.main import app
from app.routes import automation_execution, gmail


def test_inbox_picker_is_bounded_sorted_and_does_not_return_email_bodies(monkeypatch):
    calls = []
    messages = {
        "old": SimpleNamespace(
            id="old", subject="Old", sender="old@example.test", internal_date_ms=10
        ),
        "new": SimpleNamespace(
            id="new", subject="New", sender="new@example.test", internal_date_ms=20
        ),
    }
    client = SimpleNamespace(
        list_message_ids=lambda **kwargs: calls.append(kwargs) or list(messages),
        get_message=lambda ident: messages[ident],
    )
    monkeypatch.setattr(gmail, "get_gmail_client", lambda *args: client)
    with TestClient(app) as browser:
        response = browser.get("/gmail/inbox/recent?limit=2")
        assert response.status_code == 200
        assert response.json() == {
            "items": [
                {"id": "new", "subject": "New", "sender": "new@example.test"},
                {"id": "old", "subject": "Old", "sender": "old@example.test"},
            ]
        }
        assert calls == [{"query": "in:inbox", "max_results": 2}]
        assert browser.get("/gmail/inbox/recent?limit=21").status_code == 422


def test_manual_run_uses_exact_selected_email_and_reports_empty_inbox(monkeypatch):
    calls = []
    latest = [SimpleNamespace(id="newest")]
    executor = SimpleNamespace(
        gmail=SimpleNamespace(get_latest_inbox=lambda: latest[0]),
        run=lambda **kwargs: calls.append(kwargs) or {"decision": "FIXTURE"},
    )
    monkeypatch.setattr(automation_execution, "_executor", lambda db: executor)
    with TestClient(app) as browser:
        rule = browser.post("/automations", json={"name": "Debug email selection"}).json()
        endpoint = f"/automations/{rule['id']}/run/gmail"
        assert browser.post(endpoint, json={}).json()["decision"] == "FIXTURE"
        assert calls[-1]["message_id"] == "newest"
        assert (
            browser.post(
                endpoint, json={"message_id": "chosen-old-email", "force": True}
            ).status_code
            == 200
        )
        assert calls[-1]["message_id"] == "chosen-old-email"
        assert calls[-1]["force"] is True
        latest[0] = None
        assert browser.post(endpoint, json={}).json()["decision"] == "NO_MESSAGE"
        assert len(calls) == 2
