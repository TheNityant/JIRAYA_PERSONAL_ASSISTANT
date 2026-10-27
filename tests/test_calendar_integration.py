import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from googleapiclient.errors import HttpError
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.automation.executor import AutomationExecutor
from app.config import Settings
from app.db import Base
from app.main import app
from app.models import AutomationExecution, Notification
from app.runtime.calendar_runtime import CalendarRuntimeService
from app.services.automation_store import AutomationStore
from app.services.runtime_state_store import RuntimeStateStore

PROPOSAL = {
    "summary": "Project review",
    "description": "Review current progress",
    "start": "2026-10-19T15:00:00+05:30",
    "end": "2026-10-19T15:30:00+05:30",
}


@pytest.fixture
def db():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session
    engine.dispose()


def automation(db, policy="REQUIRED", source="gmail"):
    return AutomationStore(db).create(
        name="Review meetings",
        description="",
        enabled=True,
        source=source,
        trigger_type="NEW_EMAIL" if source == "gmail" else "CALENDAR_CHANGED",
        match_config={},
        context_config={"instructions": "Use Asia/Kolkata and 30 minutes."},
        action_config={"type": "CREATE_CALENDAR_EVENT" if source == "gmail" else "NOTIFY"},
        approval_policy=policy,
    )


class Provider:
    def __init__(self, response=None, route="AUTO_ACT"):
        self.response = PROPOSAL if response is None else response
        self.route = route

    def complete(self, message, **kwargs):
        if "action-safety router" in message:
            return json.dumps({"decision": self.route, "confidence": 0.99, "reason": "Covered"})
        return json.dumps(self.response)


class Calendar:
    def __init__(self):
        self.events = {}
        self.inserts = 0

    def create_event(self, *, event, **kwargs):
        self.inserts += 1
        if event["id"] in self.events:
            raise HttpError(SimpleNamespace(status=409, reason="Conflict"), b"{}")
        self.events[event["id"]] = event
        return event

    def get_event(self, *, event_id, **kwargs):
        return self.events[event_id]


def executor(db, monkeypatch, *, enabled=True, provider=None, calendar=None):
    calendar = calendar or Calendar()
    monkeypatch.setattr("app.automation.calendar_action.get_calendar_client", lambda *a: calendar)
    message = SimpleNamespace(
        id="email1",
        thread_id="thread1",
        sender="friend@example.com",
        subject="Review meeting",
        body="Monday 19 October, 3pm IST. Thirty minute review.",
        internal_date_ms=1792230000000,
    )
    gmail = SimpleNamespace(get_message=lambda _: message, get_latest_unread_inbox=lambda: message)
    return AutomationExecutor(
        settings=Settings(enable_external_actions=enabled),
        db=db,
        provider=provider or Provider(),
        gmail=gmail,
    ), calendar


@pytest.mark.parametrize(
    "policy,status", [("REQUIRED", "AWAITING_APPROVAL"), ("DRAFT_ONLY", "DRAFT_READY")]
)
def test_proposal_does_not_write_before_policy_allows(db, monkeypatch, policy, status):
    run, calendar = executor(db, monkeypatch)
    result = run.run(automation=automation(db, policy))
    assert result["decision"] == status
    assert result["execution"]["output"]["proposal"]["summary"] == "Project review"
    assert calendar.inserts == 0
    execution = db.get(AutomationExecution, result["execution"]["id"])
    approved = run.approve(execution=execution)
    assert approved["decision"] == ("DRAFT_ONLY" if policy == "DRAFT_ONLY" else "VERIFIED_SUCCESS")
    assert calendar.inserts == (0 if policy == "DRAFT_ONLY" else 1)


def test_auto_creates_verifies_and_deduplicates(db, monkeypatch):
    run, calendar = executor(db, monkeypatch)
    rule = automation(db, "AUTO")
    first = run.run(automation=rule)
    assert first["verification_status"] == "VERIFIED_SUCCESS"
    assert run.run(automation=rule)["decision"] == "ALREADY_EXECUTED"
    assert calendar.inserts == 1
    event = next(iter(calendar.events.values()))
    assert event["start"]["dateTime"] == PROPOSAL["start"]
    assert not event.get("attendees")


def test_blocked_auto_does_not_write(db, monkeypatch):
    run, calendar = executor(db, monkeypatch, enabled=False)
    result = run.run(automation=automation(db, "AUTO"))
    assert result["decision"] == "BLOCKED_BY_POLICY"
    assert calendar.inserts == 0


@pytest.mark.parametrize(
    "response",
    [
        {"error": "No date"},
        {**PROPOSAL, "end": PROPOSAL["start"]},
        {**PROPOSAL, "start": "2026-10-19T15:00:00"},
    ],
)
def test_unclear_or_invalid_times_notify_without_writes(db, monkeypatch, response):
    run, calendar = executor(db, monkeypatch, provider=Provider(response))
    result = run.run(automation=automation(db, "AUTO"))
    assert result["decision"] == "FALLBACK_NOTIFICATION"
    assert calendar.inserts == 0
    assert db.scalars(select(Notification)).first().category == "ACTION_REQUIRED"


def test_auto_safety_fallback_never_requests_approval(db, monkeypatch):
    run, calendar = executor(db, monkeypatch, provider=Provider(route="NOTIFY_ONLY"))
    assert run.run(automation=automation(db, "AUTO"))["decision"] == "FALLBACK_NOTIFICATION"
    assert calendar.inserts == 0


def test_insert_requires_readback_verification(db, monkeypatch):
    calendar = Calendar()
    calendar.get_event = lambda **kw: (_ for _ in ()).throw(RuntimeError("Read unavailable"))
    run, _ = executor(db, monkeypatch, calendar=calendar)
    result = run.run(automation=automation(db, "AUTO"))
    assert result["verification_status"] == "UNOBSERVED"


def test_calendar_sync_baseline_changes_replay_and_expiry(db, monkeypatch):
    rule = automation(db, source="google_calendar")
    connection = SimpleNamespace(id="calendar-connection")
    monkeypatch.setattr(
        "app.runtime.calendar_runtime.ConnectionStore.get_active", lambda *a, **kw: connection
    )
    batches = [
        ([{"id": "old", "summary": "Existing"}], "t1"),
        ([{"id": "new", "summary": "Review", "updated": "v1"}], "t2"),
        ([{"id": "new", "summary": "Review", "updated": "v1"}], "t3"),
    ]
    client = SimpleNamespace(changes=lambda **kw: batches.pop(0))
    monkeypatch.setattr("app.runtime.calendar_runtime.get_calendar_client", lambda *a: client)
    runtime = CalendarRuntimeService(db=db, settings=Settings())
    assert runtime.poll()["decision"] == "BASELINE_INITIALIZED"
    assert not db.scalars(select(Notification)).all()
    runtime.poll()
    runtime.poll()
    assert len(db.scalars(select(Notification)).all()) == 1
    execution = db.scalars(select(AutomationExecution)).one()
    assert execution.source == "google_calendar"
    assert execution.automation_id == rule.id

    def expired(**kw):
        if kw.get("sync_token"):
            raise HttpError(SimpleNamespace(status=410, reason="Gone"), b"{}")
        return [{"id": "history"}], "fresh"

    client.changes = expired
    assert runtime.poll()["decision"] == "BASELINE_INITIALIZED"
    assert len(db.scalars(select(Notification)).all()) == 1
    assert (
        RuntimeStateStore(db).get("calendar-sync:local-dev-user:calendar-connection")["sync_token"]
        == "fresh"
    )


def test_source_trigger_action_validation():
    with TestClient(app) as client:
        valid = {
            "name": "Calendar changes",
            "source": "google_calendar",
            "trigger_type": "CALENDAR_CHANGED",
            "action_config": {"type": "NOTIFY"},
        }
        response = client.post("/automations", json=valid)
        assert response.status_code == 201
        assert (
            client.put(
                f"/automations/{response.json()['id']}",
                json={"action_config": {"type": "DRAFT_REPLY"}},
            ).status_code
            == 422
        )
        assert (
            client.post("/automations", json={**valid, "trigger_type": "NEW_EMAIL"}).status_code
            == 422
        )
        assert (
            client.post(
                "/automations", json={**valid, "action_config": {"type": "CREATE_CALENDAR_EVENT"}}
            ).status_code
            == 422
        )


def test_calendar_runs_even_when_gmail_thread_is_already_tracked(db, monkeypatch):
    from app.runtime.gmail_runtime import GmailRuntimeService

    rule = automation(db)
    calls = []
    runtime = GmailRuntimeService.__new__(GmailRuntimeService)
    runtime.db = db
    runtime.provider = Provider()
    runtime.gmail = object()
    runtime.actions = SimpleNamespace(
        latest_verified_for_thread=lambda _: SimpleNamespace(id="action", context={})
    )
    runtime.automations = SimpleNamespace(list=lambda **kw: [rule])
    runtime.executor = SimpleNamespace(
        run=lambda **kw: calls.append(kw) or {"decision": "AWAITING_APPROVAL"}
    )
    runtime._notify_reconciliation = lambda **kw: None
    monkeypatch.setattr(
        "app.runtime.gmail_runtime.GmailReconciliationWorkflow",
        lambda **kw: SimpleNamespace(run=lambda **kw: {"decision": "NO_CHANGE"}),
    )
    message = SimpleNamespace(id="new-reply", thread_id="thread", labels=("INBOX",))
    result = runtime.process_message(message)
    assert result["path"] == "RECONCILIATION"
    assert calls[0]["message_id"] == "new-reply"
    assert result["calendar_results"][0]["result"]["decision"] == "AWAITING_APPROVAL"


def test_calendar_agenda_defaults_to_bounded_recurrence_window(monkeypatch):
    from datetime import UTC, datetime, timedelta

    calls = []
    def list_events(**kwargs):
        calls.append(kwargs)
        return [{"id": "birthday_20261025", "recurringEventId": "birthday"}]
    monkeypatch.setattr("app.routes.calendar.get_calendar_client", lambda *a: SimpleNamespace(list_events=list_events))
    before = datetime.now(UTC)
    with TestClient(app) as client:
        response = client.get("/calendar/events")
    assert response.status_code == 200
    body = response.json()
    start = datetime.fromisoformat(calls[0]["time_min"])
    end = datetime.fromisoformat(calls[0]["time_max"])
    assert before <= start <= datetime.now(UTC)
    assert end - start == timedelta(days=30)
    assert body["items"][0]["recurringEventId"] == "birthday"
    assert body["time_max"] == calls[0]["time_max"]
    assert body["limit_reached"] is False


def test_calendar_agenda_passes_selected_range_and_limit(monkeypatch):
    calls = []
    def list_events(**kwargs):
        calls.append(kwargs)
        return [{"id": "one"}, {"id": "two"}]
    monkeypatch.setattr("app.routes.calendar.get_calendar_client", lambda *a: SimpleNamespace(list_events=list_events))
    with TestClient(app) as client:
        response = client.get("/calendar/events", params={"time_min": "2026-10-19T00:00:00+05:30", "time_max": "2026-10-26T00:00:00+05:30", "limit": 2})
    assert response.status_code == 200
    assert calls == [{"calendar_id": "primary", "time_min": "2026-10-19T00:00:00+05:30", "time_max": "2026-10-26T00:00:00+05:30", "max_results": 2}]
    assert response.json()["limit_reached"] is True


@pytest.mark.parametrize("start,end", [
    ("not-a-date", "2026-10-26T00:00:00Z"),
    ("2026-10-19T00:00:00", "2026-10-26T00:00:00Z"),
    ("2026-10-19T00:00:00Z", "2026-10-19T00:00:00Z"),
    ("2026-10-26T00:00:00Z", "2026-10-19T00:00:00Z"),
])
def test_calendar_invalid_range_never_calls_provider(monkeypatch, start, end):
    def unexpected(*args):
        pytest.fail("Invalid dates must be rejected before accessing Google")
    monkeypatch.setattr("app.routes.calendar.get_calendar_client", unexpected)
    with TestClient(app) as client:
        assert client.get("/calendar/events", params={"time_min": start, "time_max": end}).status_code == 422


def test_calendar_connection_failure_is_not_reported_as_missing_event_details(db, monkeypatch):
    from app.integrations.calendar.errors import CalendarConnectionError
    run, calendar = executor(db, monkeypatch)
    monkeypatch.setattr("app.automation.calendar_action.get_calendar_client", lambda *a: (_ for _ in ()).throw(CalendarConnectionError("Missing")))
    result = run.run(automation=automation(db, "AUTO"))
    assert result["decision"] == "FALLBACK_NOTIFICATION"
    assert result["execution"]["output"]["reason_code"] == "CALENDAR_RECONNECT_REQUIRED"
    assert result["execution"]["output"]["stage"] == "CONNECTION"
    assert calendar.inserts == 0


def test_calendar_safety_reason_is_preserved(db, monkeypatch):
    run, calendar = executor(db, monkeypatch, provider=Provider(route="NOTIFY_ONLY"))
    result = run.run(automation=automation(db, "AUTO"))
    assert result["execution"]["output"]["reason_code"] == "ACTION_NOT_AUTHORIZED"
    assert result["execution"]["output"]["reason"] == "Covered"
    assert calendar.inserts == 0


def test_scheduling_rule_processes_new_proposals_in_tracked_thread_once(db, monkeypatch):
    from app.runtime.gmail_runtime import GmailRuntimeService
    from test_generic_automation_execution import make_automation
    rules = [make_automation(), make_automation()]
    for rule in rules:
        rule.action_config["calendar_scheduling"] = {"enabled": True}
    calls = []
    runtime = GmailRuntimeService.__new__(GmailRuntimeService)
    runtime.db = db
    runtime.provider = Provider()
    runtime.gmail = object()
    runtime.actions = SimpleNamespace(latest_verified_for_thread=lambda _: SimpleNamespace(id="action", context={}))
    runtime.automations = SimpleNamespace(list=lambda **kw: rules)
    runtime.executor = SimpleNamespace(run=lambda **kw: calls.append(kw) or {"decision": "SENT"})
    runtime._notify_reconciliation = lambda **kw: None
    monkeypatch.setattr("app.runtime.gmail_runtime.GmailReconciliationWorkflow", lambda **kw: SimpleNamespace(run=lambda **kw: {"decision": "NO_CHANGE"}))
    runtime.process_message(SimpleNamespace(id="proposal", thread_id="thread", labels=("INBOX",)))
    assert len(calls) == 1 and calls[0]["message_id"] == "proposal"
    runtime.process_message(SimpleNamespace(id="sent", thread_id="thread", labels=("SENT",)))
    assert len(calls) == 1


def test_revoked_calendar_token_marks_connection_for_frontend_reconnect(db, monkeypatch):
    from cryptography.fernet import Fernet
    from google.auth.exceptions import RefreshError
    from app.integrations.calendar.connection import get_calendar_client
    from app.integrations.calendar.errors import CalendarConnectionError
    from app.integrations.calendar.oauth import GOOGLE_CALENDAR_SCOPES
    from app.security.tokens import TokenCipher
    from app.services.connection_store import ConnectionStore
    from app.routes.calendar import calendar_status
    from app.routes.connections import list_connections

    settings = Settings(token_encryption_key=Fernet.generate_key().decode())
    store = ConnectionStore(db)
    store.ensure_user(user_id=settings.dev_user_id, email=settings.dev_user_email)
    store.upsert(user_id=settings.dev_user_id, provider="google_calendar", provider_account_id="fixture@example.com",
        display_name="Fixture", scopes=list(GOOGLE_CALENDAR_SCOPES),
        credential_ciphertext=TokenCipher(settings.token_encryption_key).encrypt('{}'), token_expires_at=None)
    def refresh(*args):
        raise RefreshError("invalid_grant: secret-provider-body-must-not-leak")
    credentials = SimpleNamespace(expired=True, refresh_token="not-real", refresh=refresh)
    monkeypatch.setattr("app.integrations.calendar.connection.Credentials.from_authorized_user_info", lambda *a, **kw: credentials)
    with pytest.raises(CalendarConnectionError, match="Reconnect Calendar"):
        get_calendar_client(db, settings)
    health = RuntimeStateStore(db).get(f"oauth-calendar-health:{settings.dev_user_id}")
    assert health["status"] == "RECONNECT_REQUIRED" and "secret-provider" not in str(health)
    monkeypatch.setattr("app.routes.calendar.get_settings", lambda: settings)
    monkeypatch.setattr("app.routes.connections.get_settings", lambda: settings)
    assert calendar_status(db=db)["connected"] is False
    calendar = next(item for item in list_connections(db=db)["catalog"] if item["id"] == "google_calendar")
    assert calendar["status"] != "CONNECTED"


def test_transient_refresh_failure_does_not_invalidate_calendar_authorization(db, monkeypatch):
    from cryptography.fernet import Fernet
    from google.auth.exceptions import RefreshError
    from app.integrations.calendar.connection import get_calendar_client
    from app.integrations.calendar.oauth import GOOGLE_CALENDAR_SCOPES
    from app.security.tokens import TokenCipher
    from app.services.connection_store import ConnectionStore
    settings = Settings(token_encryption_key=Fernet.generate_key().decode())
    store = ConnectionStore(db)
    store.ensure_user(user_id=settings.dev_user_id, email=settings.dev_user_email)
    store.upsert(user_id=settings.dev_user_id, provider="google_calendar", provider_account_id="fixture@example.com", display_name="Fixture", scopes=list(GOOGLE_CALENDAR_SCOPES), credential_ciphertext=TokenCipher(settings.token_encryption_key).encrypt('{}'), token_expires_at=None)
    def refresh(*a):
        raise RefreshError("server unavailable", retryable=True)
    monkeypatch.setattr("app.integrations.calendar.connection.Credentials.from_authorized_user_info", lambda *a, **kw: SimpleNamespace(expired=True, refresh_token="not-real", refresh=refresh))
    with pytest.raises(RefreshError):
        get_calendar_client(db, settings)
    assert RuntimeStateStore(db).get(f"oauth-calendar-health:{settings.dev_user_id}") is None
