"""Exercise Gmail history and the real automatic executor without a debug HTTP call."""

import asyncio
import threading
from dataclasses import replace
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from test_calendar_integration import Calendar, Provider

from app.config import Settings
from app.db import Base
from app.integrations.gmail.models import GmailMessage
from app.models import AutomationExecution
from app.runtime import background
from app.runtime.gmail_runtime import GmailRuntimeService
from app.services.automation_store import AutomationStore
from app.services.runtime_state_store import RuntimeStateStore

LINK = "https://meet.google.com/abc-defg-hij"


@pytest.fixture
def system(tmp_path, monkeypatch):
    engine = create_engine("sqlite+pysqlite:///" + str(tmp_path / "automatic.db"))
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    source = GmailMessage(
        id="first-email",
        thread_id="first-thread",
        sender="owner@example.test",
        sender_name="Owner",
        reply_to=None,
        to="owner@example.test",
        subject="Meeting",
        rfc_message_id=None,
        snippet="",
        body="Meet on 30 October at 9 PM. Join: " + LINK,
        labels=("INBOX", "SENT"),
        internal_date_ms=1793362200000,
    )

    class Gmail:
        def __init__(self):
            self.history = "100"
            self.messages = {source.id: source}
            self.pending = []

        def get_profile(self):
            return {"historyId": self.history, "emailAddress": "owner@example.test"}

        def get_latest_inbox(self):
            return None

        def get_message(self, ident):
            return self.messages[ident]

        def list_history_message_ids(self, **kwargs):
            return list(self.pending), self.history

    gmail, calendar = Gmail(), Calendar()
    monkeypatch.setattr("app.runtime.gmail_runtime.get_gmail_client", lambda *a: gmail)
    monkeypatch.setattr("app.runtime.gmail_runtime.NebiusProvider", lambda *a: Provider())
    monkeypatch.setattr("app.automation.calendar_action.get_calendar_client", lambda *a: calendar)
    monkeypatch.setattr(background, "SessionLocal", sessions)
    settings = Settings(_env_file=None, enable_external_actions=True, auth_required=False)
    with sessions() as db:
        store = AutomationStore(db)
        rule = store.create(
            name="Meeting",
            description="",
            enabled=True,
            source="gmail",
            trigger_type="NEW_EMAIL",
            match_config={},
            context_config={},
            action_config={"type": "CREATE_CALENDAR_EVENT"},
            approval_policy="AUTO",
        )
        yield SimpleNamespace(
            db=db,
            sessions=sessions,
            gmail=gmail,
            calendar=calendar,
            source=source,
            settings=settings,
            rule=rule,
            store=store,
            runtime=GmailRuntimeService(db=db, settings=settings),
        )
    engine.dispose()


@pytest.mark.parametrize(
    "labels,created", [(("INBOX", "SENT"), 1), (("INBOX",), 1), (("SENT",), 0)]
)
def test_automatic_history_supports_inbox_self_mail_but_ignores_ordinary_sent_mail(
    system, labels, created
):
    assert system.runtime.poll()["decision"] == "CURSOR_INITIALIZED"
    system.gmail.messages[system.source.id] = replace(system.source, labels=labels)
    system.gmail.pending, system.gmail.history = [system.source.id], "200"
    result = system.runtime.poll()
    assert result["processed"] == 1
    assert system.calendar.inserts == created
    if created:
        assert (
            result["results"][0]["results"][0]["result"]["verification_status"]
            == "VERIFIED_SUCCESS"
        )
        event = next(iter(system.calendar.events.values()))
        assert event["location"] == LINK and LINK in event["description"]
    assert system.runtime.poll()["decision"] == "NO_CHANGE"
    assert system.calendar.inserts == created


def test_self_sent_inbox_never_starts_an_automatic_reply_loop(system):
    system.store.create(
        name="Reply",
        description="",
        enabled=True,
        source="gmail",
        trigger_type="NEW_EMAIL",
        match_config={},
        context_config={},
        action_config={"type": "DRAFT_REPLY"},
        approval_policy="AUTO",
    )
    # A reply run would need send_reply; this fake deliberately does not implement it.
    result = system.runtime.process_message(system.source)
    assert len(result["results"]) == 1
    assert system.calendar.inserts == 1
    assert [row.action_type for row in system.db.scalars(select(AutomationExecution))] == [
        "CREATE_CALENDAR_EVENT"
    ]


def test_editing_a_rule_does_not_replay_consumed_mail_but_new_mail_runs(system):
    system.store.update(system.rule, {"match_config": {"subject_contains": "Wrong subject"}})
    system.runtime.poll()
    system.gmail.history, system.gmail.pending = "200", [system.source.id]
    assert system.runtime.poll()["results"][0]["results"][0]["result"]["decision"] == "NO_MATCH"
    system.store.update(system.rule, {"match_config": {"subject_contains": "Meeting"}})
    assert system.runtime.poll()["decision"] == "NO_CHANGE"
    assert system.calendar.inserts == 0
    new = replace(system.source, id="fresh-email", thread_id="fresh-thread")
    system.gmail.messages[new.id] = new
    system.gmail.history, system.gmail.pending = "300", [new.id]
    system.runtime.poll()
    assert system.calendar.inserts == 1


def test_real_background_loop_creates_and_verifies_calendar_event_without_browser(system):
    system.runtime.poll()  # Existing baseline before the email arrives.
    system.gmail.history, system.gmail.pending = "200", [system.source.id]
    stop = threading.Event()
    original = system.calendar.create_event

    def create(**kwargs):
        result = original(**kwargs)
        stop.set()
        return result

    system.calendar.create_event = create

    async def run():
        await asyncio.wait_for(
            background.monitor_loop(
                system.settings,
                "google",
                "gmail",
                GmailRuntimeService,
                0.01,
                stop,
            ),
            timeout=5,
        )

    asyncio.run(run())
    assert system.calendar.inserts == 1
    with system.sessions() as db:
        execution = db.scalars(select(AutomationExecution)).one()
        assert execution.status == "VERIFIED_SUCCESS"
        health = RuntimeStateStore(db).get("gmail-monitor-health:" + system.settings.dev_user_id)
        assert health["last_status"] == "OK" and health["last_decision"] == "PROCESSED"
