from datetime import datetime, timezone

from app.integrations.gmail.models import GmailMessage
from app.models import Action
from app.providers.base import ModelProvider
from app.reconciler.email_reconciler import EmailReconciler


class FakeProvider(ModelProvider):
    @property
    def provider_name(self) -> str:
        return "fake"

    @property
    def model_name(self) -> str:
        return "fake-model"

    def complete(self, message: str, *, enable_thinking: bool = True) -> str:
        return (
            '{"classification":"NEW_OBLIGATION",'
            '"original_action_validity":"STILL_VALID",'
            '"requires_follow_up":true,'
            '"reason":"The recruiter asked for interview availability."}'
        )


def make_action() -> Action:
    now = datetime.now(timezone.utc)
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
        executed_at=now,
        verified_at=now,
    )


def make_message(*, sender: str, subject: str, body: str) -> GmailMessage:
    return GmailMessage(
        id="later-1",
        thread_id="thread-1",
        sender=sender,
        sender_name="Recruiter",
        reply_to=None,
        to="me@example.com",
        subject=subject,
        rfc_message_id="<later-1@example.com>",
        snippet="",
        body=body,
        labels=("INBOX",),
        internal_date_ms=2_000_000_000_000,
    )


def test_follow_up_becomes_new_obligation():
    decision = EmailReconciler(FakeProvider()).reconcile(
        action=make_action(),
        later_message=make_message(
            sender="recruiter@example.com",
            subject="Re: Internship",
            body="Great. Are you available Wednesday at 3 PM?",
        ),
    )
    assert decision.classification == "NEW_OBLIGATION"
    assert decision.original_action_validity == "STILL_VALID"
    assert decision.requires_follow_up is True


def test_delivery_failure_is_deterministic_external_invalidation():
    decision = EmailReconciler(FakeProvider()).reconcile(
        action=make_action(),
        later_message=make_message(
            sender="mailer-daemon@googlemail.com",
            subject="Delivery Status Notification (Failure)",
            body="Your message was not delivered because the address was not found.",
        ),
    )
    assert decision.classification == "EXTERNALLY_INVALIDATED"
    assert decision.original_action_validity == "WAS_VALID_AT_EXECUTION"
    assert decision.requires_follow_up is True
