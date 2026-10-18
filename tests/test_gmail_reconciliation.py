from datetime import datetime, timedelta, timezone

from app.integrations.gmail.models import GmailMessage
from app.models import Action
from app.workflows.gmail_reconciliation import GmailReconciliationWorkflow


def make_action() -> Action:
    verified_at = datetime.now(timezone.utc)
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
        executed_at=verified_at - timedelta(seconds=1),
        verified_at=verified_at,
    )


def message(message_id: str, *, when: datetime, labels: tuple[str, ...]) -> GmailMessage:
    return GmailMessage(
        id=message_id,
        thread_id="thread-1",
        sender="recruiter@example.com",
        sender_name="Recruiter",
        reply_to=None,
        to="me@example.com",
        subject="Re: Internship",
        rfc_message_id=f"<{message_id}@example.com>",
        snippet="",
        body="Follow-up",
        labels=labels,
        internal_date_ms=int(when.timestamp() * 1000),
    )


def test_only_later_incoming_messages_are_reconciled():
    action = make_action()
    before = action.verified_at - timedelta(seconds=1)
    after = action.verified_at + timedelta(seconds=5)

    messages = [
        message("source-1", when=before, labels=("INBOX",)),
        message("sent-1", when=after, labels=("SENT",)),
        message("later-1", when=after, labels=("INBOX",)),
    ]

    later = GmailReconciliationWorkflow._later_incoming_messages(action, messages)

    assert [item.id for item in later] == ["later-1"]
