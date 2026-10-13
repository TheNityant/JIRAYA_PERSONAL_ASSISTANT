from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.db import Base
from app.integrations.gmail.models import GmailMessage
from app.models import Action
from app.providers.base import ModelProvider
from app.services.obligation_store import ObligationStore
from app.workflows.gmail_reconciliation import GmailReconciliationWorkflow


class NeverCalledProvider(ModelProvider):
    @property
    def provider_name(self) -> str:
        return "never-called"

    @property
    def model_name(self) -> str:
        return "never-called"

    def complete(self, message: str, *, enable_thinking: bool = True) -> str:
        raise AssertionError("Manual resolution should not require the LLM")


class FakeGmail:
    def __init__(self, messages: list[GmailMessage]):
        self.messages = messages

    def get_thread_messages(self, thread_id: str) -> list[GmailMessage]:
        assert thread_id == "thread-1"
        return self.messages


def gmail_message(
    message_id: str,
    *,
    when: datetime,
    labels: tuple[str, ...],
    body: str,
) -> GmailMessage:
    return GmailMessage(
        id=message_id,
        thread_id="thread-1",
        sender="user@example.com" if "SENT" in labels else "recruiter@example.com",
        sender_name="User" if "SENT" in labels else "Recruiter",
        reply_to=None,
        to="recruiter@example.com" if "SENT" in labels else "user@example.com",
        subject="Re: Internship",
        rfc_message_id=f"<{message_id}@example.com>",
        snippet="",
        body=body,
        labels=labels,
        internal_date_ms=int(when.timestamp() * 1000),
    )


def test_manual_sent_message_resolves_open_obligation():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    verified_at = datetime.now(timezone.utc)
    trigger_at = verified_at + timedelta(minutes=5)
    manual_reply_at = trigger_at + timedelta(minutes=3)

    with Session(engine) as db:
        action = Action(
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
            last_reconciliation={
                "classification": "NEW_OBLIGATION",
                "message_id": "incoming-1",
                "requires_follow_up": True,
            },
            executed_at=verified_at - timedelta(seconds=1),
            verified_at=verified_at,
        )
        db.add(action)
        db.commit()

        obligations = ObligationStore(db)
        obligation = obligations.upsert_from_reconciliation(
            action=action,
            classification="NEW_OBLIGATION",
            reason="Reply with interview availability",
            message_id="incoming-1",
            message_internal_date_ms=int(trigger_at.timestamp() * 1000),
            evidence={"message_id": "incoming-1"},
        )

        gmail = FakeGmail(
            [
                gmail_message(
                    "incoming-1",
                    when=trigger_at,
                    labels=("INBOX",),
                    body="Can you interview Wednesday at 3 PM?",
                ),
                gmail_message(
                    "manual-sent-1",
                    when=manual_reply_at,
                    labels=("SENT",),
                    body="Wednesday does not work. Friday at 1 PM works for me.",
                ),
            ]
        )
        result = GmailReconciliationWorkflow(
            db=db,
            provider=NeverCalledProvider(),
            gmail=gmail,
        ).run(action_id=action.id)

        assert result["decision"] == "OBLIGATION_RESOLVED"
        assert result["resolution_status"] == "RESOLVED_BY_USER"
        assert result["obligation"]["status"] == "RESOLVED_BY_USER"
        assert obligations.get(obligation.id).status == "RESOLVED_BY_USER"
