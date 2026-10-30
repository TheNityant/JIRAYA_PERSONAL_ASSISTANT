"""Timeline/persistence tests with controlled model outputs, not model-quality scores."""

import json
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.config import Settings
from app.db import Base
from app.integrations.gmail.models import GmailMessage
from app.models import Action
from app.providers.base import ModelProvider
from app.runtime.gmail_runtime import GmailRuntimeService
from app.services.obligation_store import ObligationStore
from app.workflows.gmail_reconciliation import GmailReconciliationWorkflow

NOW = datetime(2026, 10, 30, 8, tzinfo=UTC)


def message(ident, body, seconds, *, sent=False, thread="thread"):
    return GmailMessage(
        id=ident,
        thread_id=thread,
        sender="me@example.test" if sent else "friend@example.test",
        sender_name="Fixture",
        reply_to=None,
        to="friend@example.test" if sent else "me@example.test",
        subject="JY reconciliation",
        rfc_message_id=f"<{ident}@example.test>",
        snippet=body,
        body=body,
        labels=("SENT",) if sent else ("INBOX",),
        internal_date_ms=int((NOW + timedelta(seconds=seconds)).timestamp() * 1000),
    )


class Mailbox:
    def __init__(self, messages):
        self.messages = messages

    def get_thread_messages(self, thread_id):
        return self.messages


class Provider(ModelProvider):
    def __init__(self):
        self.calls = []

    @property
    def provider_name(self):
        return "fixture"

    @property
    def model_name(self):
        return "controlled-decisions"

    def complete(self, prompt, **kwargs):
        self.calls.append(prompt)
        if "obligation resolver" in prompt:
            resolves = "ANSWER:" in prompt.split("newly_authored_body=")[-1]
            return json.dumps(
                {"resolves": resolves, "confidence": 0.99, "reason": "Fixture answer decision"}
            )
        authored = prompt.split("newly_authored_body=")[-1]
        changed = not authored.startswith("Thanks")
        return json.dumps(
            {
                "classification": "NEW_OBLIGATION" if changed else "NO_MATERIAL_CHANGE",
                "original_action_validity": "STILL_VALID",
                "requires_follow_up": changed,
                "reason": authored,
            }
        )


@pytest.fixture
def setup():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        action = Action(
            id="action",
            action_type="EMAIL_REPLY",
            source="gmail",
            source_entity_id="gmail:source",
            correlation_id="corr",
            external_thread_id="thread",
            initial_status="VERIFIED_SUCCESS",
            current_status="VERIFIED_SUCCESS",
            context={"source_message_id": "source", "sent_message_id": "agent-answer"},
            verification_evidence={},
            last_reconciliation={},
            executed_at=NOW,
            verified_at=NOW,
        )
        db.add(action)
        db.commit()
        yield db, action
    engine.dispose()


@pytest.mark.parametrize(
    "request_text",
    [
        "Please submit the VLSI assignment by 4 November.",
        "Please send your CV and availability for an interview.",
        "Please provide the API migration status and demo link.",
        "Please share the revised invoice for the support renewal.",
    ],
)
def test_request_is_not_lost_when_a_later_ack_arrives_before_the_poll(setup, request_text):
    db, action = setup
    provider = Provider()
    mailbox = Mailbox(
        [message("request", request_text, 1), message("ack", "Thanks for checking.", 2)]
    )
    workflow = GmailReconciliationWorkflow(db=db, provider=provider, gmail=mailbox)
    first = workflow.run(action_id=action.id)
    assert first["later_message"]["message_id"] == "request"
    obligation = ObligationStore(db).open_for_action(action.id)
    assert obligation is not None and request_text in obligation.description
    second = workflow.run(action_id=action.id)
    assert second["later_message"]["message_id"] == "ack"
    assert ObligationStore(db).open_for_action(action.id).id == obligation.id
    assert workflow.run(action_id=action.id)["decision"] == "NO_NEW_EVENT"
    assert len(provider.calls) == 2 and action.initial_status == "VERIFIED_SUCCESS"


def seed_obligation(db, action):
    return ObligationStore(db).upsert_from_reconciliation(
        action=action,
        classification="NEW_OBLIGATION",
        reason="Provide the API migration status.",
        message_id="request",
        message_internal_date_ms=message("request", "", 1).internal_date_ms,
        evidence={"message_id": "request"},
    )


def test_substantive_sent_answer_is_not_hidden_by_a_later_sent_ack(setup):
    db, action = setup
    seed_obligation(db, action)
    mailbox = Mailbox(
        [
            message("agent-answer", "Acknowledged.", 0, sent=True),
            message("request", "Provide the API migration status.", 1),
            message("answer", "ANSWER: migration is complete; the demo is ready.", 2, sent=True),
            message("ack", "Thanks, noted.", 3, sent=True),
        ]
    )
    workflow = GmailReconciliationWorkflow(db=db, provider=Provider(), gmail=mailbox)
    result = workflow.run(action_id=action.id)
    assert result["decision"] == "OBLIGATION_RESOLVED"
    assert result["resolving_message"]["message_id"] == "answer"
    assert result["resolution_status"] == "RESOLVED_BY_USER"
    assert workflow.run(action_id=action.id)["decision"] == "NO_NEW_EVENT"


def test_new_incoming_requirement_is_processed_before_a_later_sent_answer(setup):
    db, action = setup
    seed_obligation(db, action)
    mailbox = Mailbox(
        [
            message("request", "Provide the API migration status.", 1),
            message("new-requirement", "Also include the rollback procedure.", 2),
            message("answer", "ANSWER: migration and rollback details are ready.", 3, sent=True),
        ]
    )
    workflow = GmailReconciliationWorkflow(db=db, provider=Provider(), gmail=mailbox)
    result = workflow.run(action_id=action.id)
    assert result["later_message"]["message_id"] == "new-requirement"
    assert workflow.run(action_id=action.id)["decision"] == "OBLIGATION_RESOLVED"


def test_same_timestamp_messages_are_each_processed_once(setup):
    db, action = setup
    provider = Provider()
    mailbox = Mailbox(
        [message("b-ack", "Thanks.", 1), message("a-request", "Send the demo link.", 1)]
    )
    workflow = GmailReconciliationWorkflow(db=db, provider=provider, gmail=mailbox)
    assert workflow.run(action_id=action.id)["later_message"]["message_id"] == "a-request"
    assert workflow.run(action_id=action.id)["later_message"]["message_id"] == "b-ack"
    assert workflow.run(action_id=action.id)["decision"] == "NO_NEW_EVENT"
    assert len(provider.calls) == 2


def test_other_thread_cannot_create_or_resolve_work(setup):
    db, action = setup
    seed_obligation(db, action)
    provider = Provider()
    mailbox = Mailbox(
        [message("foreign", "ANSWER: all done.", 3, sent=True, thread="another-thread")]
    )
    result = GmailReconciliationWorkflow(db=db, provider=provider, gmail=mailbox).run(
        action_id=action.id
    )
    assert result["decision"] == "NO_NEW_EVENT"
    assert ObligationStore(db).open_for_action(action.id) is not None
    assert not provider.calls


def test_runtime_drains_a_burst_from_one_history_wakeup(setup, monkeypatch):
    db, action = setup
    request = message("request", "Send the demo link.", 1)
    ack = message("ack", "Thanks for checking.", 2)
    mailbox = Mailbox([request, ack])
    monkeypatch.setattr("app.runtime.gmail_runtime.get_gmail_client", lambda *args: mailbox)
    monkeypatch.setattr("app.runtime.gmail_runtime.NebiusProvider", lambda *args: Provider())
    result = GmailRuntimeService(db=db, settings=Settings()).process_message(ack)
    assert [r["later_message"]["message_id"] for r in result["reconciliation_results"]] == [
        "request",
        "ack",
    ]
    assert ObligationStore(db).open_for_action(action.id) is not None
