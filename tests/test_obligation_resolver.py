from datetime import datetime, timezone

from app.integrations.gmail.models import GmailMessage
from app.models import Obligation
from app.providers.base import ModelProvider
from app.reconciler.obligation_resolver import ObligationResolver


class FakeProvider(ModelProvider):
    def __init__(self, response: str):
        self.response = response
        self.last_message = ""
        self.calls = 0

    @property
    def provider_name(self) -> str:
        return "fake"

    @property
    def model_name(self) -> str:
        return "fake-model"

    def complete(self, message: str, *, enable_thinking: bool = True) -> str:
        self.calls += 1
        self.last_message = message
        return self.response


def obligation() -> Obligation:
    now = datetime.now(timezone.utc)
    return Obligation(
        id="obligation-1",
        obligation_type="NEW_OBLIGATION",
        source="gmail",
        source_entity_id="gmail:source-1",
        related_action_id="action-1",
        external_thread_id="thread-1",
        trigger_message_id="incoming-1",
        status="OPEN",
        title="Follow-up required",
        description="Reply with interview availability",
        context={},
        resolution={},
        created_at=now,
        updated_at=now,
    )


def sent_message(body: str) -> GmailMessage:
    return GmailMessage(
        id="sent-candidate",
        thread_id="thread-1",
        sender="me@example.com",
        sender_name="Me",
        reply_to=None,
        to="recruiter@example.com",
        subject="Re: Internship",
        rfc_message_id="<sent-candidate@example.com>",
        snippet="",
        body=body,
        labels=("SENT",),
        internal_date_ms=2_000_000_000_000,
    )


def test_concrete_availability_can_resolve_obligation():
    provider = FakeProvider(
        '{"resolves":true,"confidence":0.98,'
        '"reason":"The message provides Friday at 1 PM as an alternative."}'
    )
    decision = ObligationResolver(provider).evaluate(
        obligation=obligation(),
        sent_message=sent_message("Wednesday does not work. Friday at 1 PM works for me."),
    )
    assert decision.resolves is True
    assert decision.confidence == 0.98


def test_acknowledgement_does_not_resolve_obligation():
    provider = FakeProvider(
        '{"resolves":false,"confidence":0.97,'
        '"reason":"The message only acknowledges the request and gives no availability."}'
    )
    decision = ObligationResolver(provider).evaluate(
        obligation=obligation(),
        sent_message=sent_message("Thanks, I will get back to you shortly."),
    )
    assert decision.resolves is False


def test_quoted_history_is_removed_before_model_evaluation():
    provider = FakeProvider(
        '{"resolves":true,"confidence":0.99,'
        '"reason":"The newly authored text provides Friday at 1 PM."}'
    )
    body = (
        "Wednesday does not work for me. Friday at 1 PM works well.\n\n"
        "On Tue, Oct 13, 2026 at 9:00 AM Recruiter <recruiter@example.com> wrote:\n"
        "> Would you be available for an interview on Wednesday at 3 PM?\n"
    )

    decision = ObligationResolver(provider).evaluate(
        obligation=obligation(),
        sent_message=sent_message(body),
    )

    assert decision.resolves is True
    assert "Friday at 1 PM works well" in provider.last_message
    assert "Would you be available for an interview on Wednesday at 3 PM?" not in provider.last_message


def test_quote_only_sent_message_cannot_resolve_obligation():
    provider = FakeProvider(
        '{"resolves":true,"confidence":1.0,"reason":"should never be called"}'
    )
    body = (
        "On Tue, Oct 13, 2026 at 9:00 AM Recruiter <recruiter@example.com> wrote:\n"
        "> Would you be available for an interview on Wednesday at 3 PM?\n"
    )

    decision = ObligationResolver(provider).evaluate(
        obligation=obligation(),
        sent_message=sent_message(body),
    )

    assert decision.resolves is False
    assert provider.calls == 0
