"""Tests for the SCHEDULE_MEET action: valid link -> pending proposal, invalid -> ignored."""

import json
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.automation.executor import AutomationExecutor
from app.config import Settings
from app.db import Base
from app.services.automation_store import AutomationStore

PROPOSAL = {
    "summary": "Meet review",
    "description": "Review via Meet",
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


def automation(db):
    return AutomationStore(db).create(
        name="Meet scheduler",
        description="",
        enabled=True,
        source="gmail",
        trigger_type="NEW_EMAIL",
        match_config={},
        context_config={"instructions": "Use Asia/Kolkata and 30 minutes."},
        action_config={"type": "SCHEDULE_MEET"},
        approval_policy="REQUIRED",
    )


class Provider:
    def complete(self, message, **kwargs):
        return json.dumps(PROPOSAL)


class Calendar:
    def __init__(self):
        self.events = {}
        self.inserts = 0

    def create_event(self, *, event, **kwargs):
        self.events[event["id"]] = event
        return event

    def get_event(self, *, event_id, **kwargs):
        return self.events[event_id]


def executor(db, monkeypatch, body):
    calendar = Calendar()
    monkeypatch.setattr(
        "app.automation.calendar_action.get_calendar_client", lambda *a: calendar
    )
    message = SimpleNamespace(
        id="email1",
        thread_id="thread1",
        sender="friend@example.com",
        subject="Join the meeting",
        body=body,
        internal_date_ms=1792230000000,
    )
    gmail = SimpleNamespace(get_message=lambda _: message, get_latest_unread_inbox=lambda: message)
    run = AutomationExecutor(
        settings=Settings(enable_external_actions=True),
        db=db,
        provider=Provider(),
        gmail=gmail,
    )
    return run, calendar


def test_valid_meet_link_creates_pending_execution_with_link(db, monkeypatch):
    body = "Join here: https://meet.google.com/abc-defg-hij on Monday 3pm IST."
    run, calendar = executor(db, monkeypatch, body)
    result = run.run(automation=automation(db))
    assert result["decision"] == "AWAITING_APPROVAL"
    assert result["execution"]["output"]["meet_link"] == "https://meet.google.com/abc-defg-hij"
    assert calendar.inserts == 0  # nothing written before manual approval


def test_meet_link_lands_on_calendar_event_after_approval(db, monkeypatch):
    body = "Join https://meet.google.com/abc-defg-hij Monday 19 October, 3pm IST. Thirty minute review."
    run, calendar = executor(db, monkeypatch, body)
    result = run.run(automation=automation(db))
    from app.models import AutomationExecution

    execution = db.get(AutomationExecution, result["execution"]["id"])
    approved = run.approve(execution=execution)
    assert approved["decision"] == "VERIFIED_SUCCESS"
    event = next(iter(calendar.events.values()))
    assert "https://meet.google.com/abc-defg-hij" in event["description"]
    assert event["extendedProperties"]["private"]["jirayaMeetLink"] == (
        "https://meet.google.com/abc-defg-hij"
    )


def test_meet_link_with_digits_is_ignored(db, monkeypatch):
    body = "Fake link https://meet.google.com/abc-defg-hi1 do not schedule."
    run, calendar = executor(db, monkeypatch, body)
    result = run.run(automation=automation(db))
    assert result["decision"] == "MEET_LINK_IGNORED"
    assert calendar.inserts == 0


def test_no_meet_link_is_ignored(db, monkeypatch):
    run, calendar = executor(db, monkeypatch, "Plain email, no meeting link at all.")
    result = run.run(automation=automation(db))
    assert result["decision"] == "MEET_LINK_IGNORED"
    assert calendar.inserts == 0
