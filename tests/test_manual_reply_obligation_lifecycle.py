from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.db import Base
from app.integrations.gmail.models import GmailMessage
from app.models import Action
from app.providers.base import ModelProvider
from app.services.obligation_store import ObligationStore
from app.workflows.gmail_reconciliation import GmailReconciliationWorkflow


class ResolutionProvider(ModelProvider):
    @property
    def provider_name(self) -> str:
        return "test"

    @property
    def model_name(self) -> str:
        return "test-resolution"

    def complete(self, message: str, *, enable_thinking: bool = True) -> str:
        return (
            '{"resolves":true,"confidence":0.99,'
            '"reason":"The user directly supplied the requested project update."}'
        )


class FakeGmail:
    def __init__(self, messages: list[GmailMessage]):
        self.messages = messages

    def get_thread_messages(self, thread_id: str) -> list[GmailMessage]:
        return sorted(
            [item for item in self.messages if item.thread_id == thread_id],
            key=lambda item: item.internal_date_ms,
        )


def msg(
    message_id: str,
    *,
    when: datetime,
    sender: str,
    body: str,
    labels: tuple[str, ...],
) -> GmailMessage:
    return GmailMessage(
        id=message_id,
        thread_id="thread-1",
        sender=sender,
        sender_name="Me" if sender == "me@example.com" else "Friend",
        reply_to=None,
        to="friend@example.com" if sender == "me@example.com" else "me@example.com",
        subject="Re: Hackathon update",
        rfc_message_id=f"<{message_id}@example.com>",
        snippet=body,
        body=body,
        labels=labels,
        internal_date_ms=int(when.timestamp() * 1000),
    )


def test_manual_reply_resolves_existing_obligation_and_remains_in_closed_history():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    now = datetime.now(timezone.utc)

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
            context={"source_message_id": "source-1", "sent_message_id": "agent-sent-1"},
            verification_evidence={},
            last_reconciliation={},
            executed_at=now,
            verified_at=now,
        )
        db.add(action)
        db.commit()

        store = ObligationStore(db)
        obligation = store.upsert_from_reconciliation(
            action=action,
            classification="NEW_OBLIGATION",
            reason="Send the requested hackathon progress update.",
            message_id="request-1",
            message_internal_date_ms=int((now + timedelta(seconds=1)).timestamp() * 1000),
            evidence={"message_id": "request-1"},
        )

        # The manual reply deliberately has no SENT label. The reconciliator must still
        # recognize it as user-authored from the verified action's mailbox identity.
        gmail = FakeGmail(
            [
                msg(
                    "agent-sent-1",
                    when=now,
                    sender="me@example.com",
                    body="I am working on the project.",
                    labels=("SENT",),
                ),
                msg(
                    "request-1",
                    when=now + timedelta(seconds=1),
                    sender="friend@example.com",
                    body="Can you send me the latest project update?",
                    labels=("INBOX",),
                ),
                msg(
                    "manual-answer",
                    when=now + timedelta(seconds=2),
                    sender="me@example.com",
                    body="Check the GitHub repo; the latest changes are there.",
                    labels=(),
                ),
            ]
        )

        result = GmailReconciliationWorkflow(
            db=db,
            provider=ResolutionProvider(),
            gmail=gmail,
        ).run(action_id=action.id)

        assert result["decision"] == "OBLIGATION_RESOLVED"
        assert result["resolution_status"] == "RESOLVED_BY_USER"
        assert store.open_for_action(action.id) is None

        all_items = store.list_recent()
        assert len(all_items) == 1
        assert all_items[0].id == obligation.id
        assert all_items[0].status == "RESOLVED_BY_USER"
        assert all_items[0].resolved_at is not None
        assert all_items[0].resolution["message_id"] == "manual-answer"
