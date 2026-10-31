from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.db import Base
from app.integrations.gmail.models import GmailMessage
from app.models import Action
from app.providers.base import ModelProvider
from app.services.obligation_store import ObligationStore
from app.workflows.gmail_reconciliation import GmailReconciliationWorkflow


class StaticProvider(ModelProvider):
    def __init__(self, response: str):
        self.response = response

    @property
    def provider_name(self) -> str:
        return "test"

    @property
    def model_name(self) -> str:
        return "test"

    def complete(self, message: str, *, enable_thinking: bool = True) -> str:
        return self.response


class FakeGmail:
    def __init__(self, messages: list[GmailMessage]):
        self.messages = messages

    def get_thread_messages(self, thread_id: str) -> list[GmailMessage]:
        return sorted(
            [item for item in self.messages if item.thread_id == thread_id],
            key=lambda item: item.internal_date_ms,
        )


def gmail_message(
    message_id: str,
    *,
    when: datetime,
    body: str,
    labels: tuple[str, ...],
) -> GmailMessage:
    sent = "SENT" in labels
    return GmailMessage(
        id=message_id,
        thread_id="thread-1",
        sender="me@example.com" if sent else "friend@example.com",
        sender_name="Me" if sent else "Friend",
        reply_to=None,
        to="friend@example.com" if sent else "me@example.com",
        subject="Re: Hackathon update",
        rfc_message_id=f"<{message_id}@example.com>",
        snippet=body,
        body=body,
        labels=labels,
        internal_date_ms=int(when.timestamp() * 1000),
    )


def seeded_action(now: datetime) -> Action:
    return Action(
        id="action-1",
        action_type="EMAIL_REPLY",
        source="gmail",
        source_entity_id="gmail:source-1",
        correlation_id="corr-1",
        external_thread_id="thread-1",
        initial_status="VERIFIED_SUCCESS",
        current_status="VERIFIED_SUCCESS",
        context={"source_message_id": "source-1", "sent_message_id": "sent-1"},
        verification_evidence={},
        last_reconciliation={},
        executed_at=now - timedelta(seconds=1),
        verified_at=now,
    )


def seed_resolved_obligation(db: Session, action: Action, now: datetime) -> None:
    store = ObligationStore(db)
    obligation = store.upsert_from_reconciliation(
        action=action,
        classification="NEW_OBLIGATION",
        reason="Provide the requested hackathon progress update.",
        message_id="request-1",
        message_internal_date_ms=int((now + timedelta(seconds=1)).timestamp() * 1000),
        evidence={"message_id": "request-1"},
    )
    store.resolve(
        obligation=obligation,
        status="RESOLVED_BY_USER",
        resolved_at=now + timedelta(seconds=2),
        message_id="manual-answer",
        reason="The user provided the requested update.",
        evidence={"message_id": "manual-answer"},
    )


def test_acknowledgement_after_resolution_does_not_create_fake_user_obligation():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    now = datetime.now(timezone.utc)

    with Session(engine) as db:
        action = seeded_action(now)
        db.add(action)
        db.commit()
        seed_resolved_obligation(db, action, now)
        later = gmail_message(
            "ack-1",
            when=now + timedelta(seconds=3),
            labels=("INBOX",),
            body="Okay, I will check the GitHub repository.",
        )
        provider = StaticProvider(
            '{"classification":"NO_MATERIAL_CHANGE","original_action_validity":"STILL_VALID","requires_follow_up":false,"reason":"The sender only acknowledges the update and creates no new work for the user."}'
        )

        result = GmailReconciliationWorkflow(
            db=db,
            provider=provider,
            gmail=FakeGmail([later]),
        ).run(action_id=action.id)

        assert result["decision"] == "RECONCILED"
        assert result["classification"] == "NO_MATERIAL_CHANGE"
        assert ObligationStore(db).open_for_action(action.id) is None
        assert len(ObligationStore(db).list_recent()) == 1


def test_new_request_after_resolution_opens_a_new_obligation():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    now = datetime.now(timezone.utc)

    with Session(engine) as db:
        action = seeded_action(now)
        db.add(action)
        db.commit()
        seed_resolved_obligation(db, action, now)
        later = gmail_message(
            "new-request-2",
            when=now + timedelta(seconds=3),
            labels=("INBOX",),
            body="Can you also send me the demo link and the latest architecture diagram?",
        )
        provider = StaticProvider(
            '{"classification":"NEW_OBLIGATION","original_action_validity":"STILL_VALID","requires_follow_up":true,"reason":"The sender asks for two additional deliverables."}'
        )

        result = GmailReconciliationWorkflow(
            db=db,
            provider=provider,
            gmail=FakeGmail([later]),
        ).run(action_id=action.id)

        assert result["decision"] == "RECONCILED"
        assert result["classification"] == "NEW_OBLIGATION"
        reopened = ObligationStore(db).open_for_action(action.id)
        assert reopened is not None
        assert reopened.trigger_message_id == "new-request-2"
        assert len(ObligationStore(db).list_recent()) == 2
