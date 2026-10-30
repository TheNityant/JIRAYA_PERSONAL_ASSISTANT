"""Real orchestration/executors with isolated provider clients and controlled model outputs."""

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from test_generic_automation_execution import FakeGmail
from test_github_integration import GitHub
from test_scheduling_reply import BookingCalendar

from app.automation import scheduling_reply
from app.config import Settings
from app.db import Base
from app.integrations.calendar.errors import CalendarConnectionError
from app.models import AutomationExecution, Notification
from app.runtime.github_runtime import GitHubRuntimeService
from app.runtime.gmail_runtime import GmailRuntimeService
from app.services.automation_store import AutomationStore

NOW = datetime(2026, 10, 30, 3, 30, tzinfo=UTC)


class Provider:
    def complete(self, prompt, **kwargs):
        if "action-safety router" in prompt:
            return json.dumps(
                {"decision": "AUTO_ACT", "confidence": 1, "reason": "Fixture authorization"}
            )
        if "Extract meeting timing" in prompt:
            return json.dumps(
                {
                    "kind": "proposed",
                    "timezone_explicit": False,
                    "slots": [{"start": "2026-11-02T15:00:00+05:30"}],
                }
            )
        if "obligation extractor" in prompt:
            return json.dumps({"creates_obligation": False, "confidence": 1})
        if "Draft one GitHub issue" in prompt:
            return json.dumps(
                {
                    "title": "JY-X01 — Review API timeout",
                    "body": "Investigate the reported API timeout.",
                }
            )
        raise AssertionError("Unexpected model call")


@pytest.fixture
def setup(monkeypatch):
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    gmail, calendar, github = FakeGmail(), BookingCalendar(), GitHub()
    gmail.source = replace(
        gmail.source,
        subject="JY-X01 — API incident review",
        body="Review the API timeout on 2 November 2026 at 3 PM and track the defect.",
        internal_date_ms=int(NOW.timestamp() * 1000),
    )
    gmail.messages = {gmail.source.id: gmail.source}
    gmail.get_thread_messages = lambda thread: [
        m for m in gmail.messages.values() if m.thread_id == thread
    ]
    monkeypatch.setattr("app.runtime.gmail_runtime.get_gmail_client", lambda *args: gmail)
    monkeypatch.setattr("app.runtime.gmail_runtime.NebiusProvider", lambda *args: Provider())
    monkeypatch.setattr(
        "app.automation.scheduling_booking.get_calendar_client", lambda *args: calendar
    )
    monkeypatch.setattr(scheduling_reply, "get_calendar_client", lambda *args: calendar)
    monkeypatch.setattr("app.automation.github_action.get_github_client", lambda *args: github)

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW.astimezone(tz or UTC)

    monkeypatch.setattr(scheduling_reply, "datetime", Clock)
    with Session(engine) as db:
        store = AutomationStore(db)
        issue = store.create(
            name="Track defect",
            description="",
            enabled=True,
            source="gmail",
            trigger_type="NEW_EMAIL",
            match_config={"subject_contains": "JY-X01"},
            context_config={},
            approval_policy="AUTO",
            action_config={"type": "CREATE_GITHUB_ISSUE", "repository": "owner/sandbox"},
        )
        reply = store.create(
            name="Book review",
            description="",
            enabled=True,
            source="gmail",
            trigger_type="NEW_EMAIL",
            match_config={"subject_contains": "JY-X01"},
            context_config={},
            approval_policy="AUTO",
            action_config={
                "type": "DRAFT_REPLY",
                "calendar_scheduling": {
                    "enabled": True,
                    "time_zone": "Asia/Kolkata",
                    "duration_minutes": 60,
                    "assume_timezone": True,
                    "book_proposed": True,
                    "horizon_days": 14,
                },
            },
        )
        settings = Settings(enable_external_actions=True)
        runtime = GmailRuntimeService(db=db, settings=settings)
        yield SimpleNamespace(
            db=db,
            gmail=gmail,
            calendar=calendar,
            github=github,
            runtime=runtime,
            issue=issue,
            reply=reply,
            settings=settings,
            store=store,
        )
    engine.dispose()


def statuses(setup):
    return {
        row.action_type: row.status for row in setup.db.scalars(select(AutomationExecution)).all()
    }


def test_one_email_books_replies_creates_issue_and_feeds_github_watcher_once(setup, monkeypatch):
    watcher = setup.store.create(
        name="Issue watcher",
        description="",
        enabled=True,
        source="github",
        trigger_type="GITHUB_ISSUE_CHANGED",
        match_config={},
        context_config={},
        approval_policy="DRAFT_ONLY",
        action_config={"type": "NOTIFY", "repository": "owner/sandbox"},
    )
    setup.github.list_issues = lambda **kwargs: [
        {"id": number, **data} for number, data in setup.github.issues.items()
    ]
    monkeypatch.setattr("app.runtime.github_runtime.get_github_client", lambda *args: setup.github)
    monkeypatch.setattr(
        "app.runtime.github_runtime.ConnectionStore.get_active",
        lambda *args, **kwargs: SimpleNamespace(id="fixture-connection"),
    )
    watcher_runtime = GitHubRuntimeService(db=setup.db, settings=setup.settings)
    assert watcher_runtime.poll()["baselines_initialized"] == 1

    original_send = setup.gmail.send_reply

    def send(**kwargs):
        assert setup.calendar.inserts == 1  # Calendar must be verified before confirmation.
        assert "I have added this time to my calendar" in kwargs["body_text"]
        assert (
            "GitHub issue created" not in kwargs["body_text"]
        )  # Independent action has not run yet.
        return original_send(**kwargs)

    setup.gmail.send_reply = send
    setup.runtime.process_message(setup.gmail.source)
    assert statuses(setup) == {
        "DRAFT_REPLY": "VERIFIED_SUCCESS",
        "CREATE_GITHUB_ISSUE": "VERIFIED_SUCCESS",
    }
    assert setup.calendar.inserts == setup.gmail.send_calls == setup.github.issue_calls == 1
    event = next(iter(setup.calendar.events.values()))
    assert datetime.fromisoformat(event["end"]["dateTime"]) - datetime.fromisoformat(
        event["start"]["dateTime"]
    ) == timedelta(hours=1)

    assert watcher_runtime.poll()["processed"] == 1
    setup.runtime.process_message(setup.gmail.source)
    assert watcher_runtime.poll()["processed"] == 0
    assert setup.calendar.inserts == setup.gmail.send_calls == setup.github.issue_calls == 1
    notices = setup.db.scalars(
        select(Notification).where(Notification.automation_id == watcher.id)
    ).all()
    assert len(notices) == 1


def test_calendar_failure_does_not_send_false_confirmation_or_suppress_github(setup, monkeypatch):
    def unavailable(*args):
        raise CalendarConnectionError("Fixture disconnected")

    monkeypatch.setattr(scheduling_reply, "get_calendar_client", unavailable)
    setup.runtime.process_message(setup.gmail.source)
    assert statuses(setup) == {
        "DRAFT_REPLY": "FALLBACK_NOTIFICATION",
        "CREATE_GITHUB_ISSUE": "VERIFIED_SUCCESS",
    }
    assert setup.calendar.inserts == setup.gmail.send_calls == 0
    assert setup.github.issue_calls == 1


def test_github_failure_keeps_verified_calendar_and_reply(setup, monkeypatch):
    def unavailable(*args):
        raise RuntimeError("Fixture disconnected")

    monkeypatch.setattr("app.automation.github_action.get_github_client", unavailable)
    setup.runtime.process_message(setup.gmail.source)
    assert statuses(setup) == {
        "DRAFT_REPLY": "VERIFIED_SUCCESS",
        "CREATE_GITHUB_ISSUE": "FALLBACK_NOTIFICATION",
    }
    assert setup.calendar.inserts == setup.gmail.send_calls == 1
    assert setup.github.issue_calls == 0


def test_external_action_gate_blocks_every_connector_write(setup):
    setup.runtime.settings.enable_external_actions = False
    setup.runtime.executor.settings.enable_external_actions = False
    setup.runtime.process_message(setup.gmail.source)
    assert set(statuses(setup).values()) == {"BLOCKED_BY_POLICY"}
    assert setup.calendar.inserts == setup.gmail.send_calls == setup.github.issue_calls == 0
