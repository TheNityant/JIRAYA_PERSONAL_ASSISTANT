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
