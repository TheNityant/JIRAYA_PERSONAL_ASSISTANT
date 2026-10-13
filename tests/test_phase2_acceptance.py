from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.db import Base
from app.integrations.gmail.models import GmailMessage
from app.models import Action
from app.providers.base import ModelProvider
from app.reconciler.email_reconciler import EmailReconciler
from app.reconciler.obligation_resolver import ObligationResolver
from app.services.obligation_store import ObligationStore
from app.workflows.gmail_reconciliation import GmailReconciliationWorkflow


class StaticProvider(ModelProvider):
    def __init__(self, response: str):
        self.response = response
        self.calls = 0
        self.last_message = ""

    @property
    def provider_name(self) -> str:
        return "static"

    @property
    def model_name(self) -> str:
        return "static-model"

    def complete(self, message: str, *, enable_thinking: bool = True) -> str:
        self.calls += 1
        self.last_message = message
        return self.response


class FakeGmail:
    def __init__(self, messages: list[GmailMessage]):
        self.messages = messages

    def get_thread_messages(self, thread_id: str) -> list[GmailMessage]:
        return [message for message in self.messages if message.thread_id == thread_id]


def make_action(*, now: datetime | None = None) -> Action:
    now = now or datetime.now(timezone.utc)
    return Action(
        id="phase2-action",
        action_type="EMAIL_REPLY",
        source="gmail",
        source_entity_id="gmail:source-1",
        correlation_id="phase2-corr",
        external_thread_id="thread-1",
        initial_status="VERIFIED_SUCCESS",
        current_status="VERIFIED_SUCCESS",
        context={"source_message_id": "source-1", "sent_message_id": "sent-1"},
        verification_evidence={},
        last_reconciliation={},
        executed_at=now - timedelta(seconds=1),
        verified_at=now,
    )


def message(
    message_id: str,
    *,
    when: datetime,
    body: str,
    labels: tuple[str, ...],
    sender: str | None = None,
    subject: str = "Re: Internship",
) -> GmailMessage:
    sent = "SENT" in labels
    return GmailMessage(
        id=message_id,
        thread_id="thread-1",
        sender=sender or ("me@example.com" if sent else "recruiter@example.com"),
        sender_name="Me" if sent else "Recruiter",
        reply_to=None,
        to="recruiter@example.com" if sent else "me@example.com",
        subject=subject,
        rfc_message_id=f"<{message_id}@example.com>",
        snippet="",
        body=body,
        labels=labels,
        internal_date_ms=int(when.timestamp() * 1000),
    )


def test_01_new_incoming_request_keeps_original_action_valid():
    provider = StaticProvider(
        '{"classification":"NEW_OBLIGATION",'
        '"original_action_validity":"STILL_VALID",'
        '"requires_follow_up":true,'
        '"reason":"A new interview scheduling request requires a response."}'
    )
    later = message(
        "incoming-1",
        when=datetime.now(timezone.utc),
        labels=("INBOX",),
        body="Could you interview Tuesday at 4 PM?",
    )

    decision = EmailReconciler(provider).reconcile(action=make_action(), later_message=later)

    assert decision.classification == "NEW_OBLIGATION"
    assert decision.original_action_validity == "STILL_VALID"
    assert decision.requires_follow_up is True


def test_02_incoming_quoted_history_is_not_semantic_input():
    provider = StaticProvider(
        '{"classification":"NEW_OBLIGATION",'
        '"original_action_validity":"STILL_VALID",'
        '"requires_follow_up":true,'
        '"reason":"Tuesday at 4 PM is a new request."}'
    )
    body = (
        "Could you interview Tuesday at 4 PM?\n\n"
        "On Mon, Oct 12, 2026 at 9:00 PM Me <me@example.com> wrote:\n"
        "> I can do Wednesday at 1 PM.\n"
    )

    EmailReconciler(provider).reconcile(
        action=make_action(),
        later_message=message(
            "incoming-2",
            when=datetime.now(timezone.utc),
            labels=("INBOX",),
            body=body,
        ),
    )

    assert "Tuesday at 4 PM" in provider.last_message
    assert "I can do Wednesday at 1 PM" not in provider.last_message


def test_03_quote_only_incoming_message_is_not_a_new_obligation():
    provider = StaticProvider(
        '{"classification":"NEW_OBLIGATION",'
        '"original_action_validity":"STILL_VALID",'
        '"requires_follow_up":true,"reason":"should not be called"}'
    )
    body = (
        "On Mon, Oct 12, 2026 at 9:00 PM Recruiter <recruiter@example.com> wrote:\n"
        "> Could you interview Tuesday at 4 PM?\n"
    )

    decision = EmailReconciler(provider).reconcile(
        action=make_action(),
        later_message=message(
            "incoming-quote-only",
            when=datetime.now(timezone.utc),
            labels=("INBOX",),
            body=body,
        ),
    )

    assert decision.classification == "NO_MATERIAL_CHANGE"
    assert decision.requires_follow_up is False
    assert provider.calls == 0


def test_04_delivery_failure_invalidates_outcome_without_rewriting_history():
    provider = StaticProvider("{}")
    later = message(
        "bounce-1",
        when=datetime.now(timezone.utc),
        labels=("INBOX",),
        sender="mailer-daemon@googlemail.com",
        subject="Delivery Status Notification (Failure)",
        body="Your message was not delivered because the address was not found.",
    )

    decision = EmailReconciler(provider).reconcile(action=make_action(), later_message=later)

    assert decision.classification == "EXTERNALLY_INVALIDATED"
    assert decision.original_action_validity == "WAS_VALID_AT_EXECUTION"
    assert provider.calls == 0


def test_05_manual_reply_must_semantically_satisfy_the_obligation():
    provider = StaticProvider(
        '{"resolves":false,"confidence":0.99,'
        '"reason":"Acknowledgement does not provide interview availability."}'
    )
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)

    with Session(engine) as db:
        action = make_action()
        db.add(action)
        db.commit()
        obligation = ObligationStore(db).upsert_from_reconciliation(
            action=action,
            classification="NEW_OBLIGATION",
            reason="Reply with interview availability",
            message_id="incoming-1",
            message_internal_date_ms=1_000,
            evidence={"message_id": "incoming-1"},
        )
        sent = message(
            "manual-ack",
            when=datetime.now(timezone.utc),
            labels=("SENT",),
            body="Thanks, I will get back to you shortly.",
        )

        semantic = ObligationResolver(provider).evaluate(
            obligation=obligation,
            sent_message=sent,
        )

        assert semantic.resolves is False
        assert obligation.status == "OPEN"


def test_06_manual_reply_with_concrete_availability_can_resolve_obligation():
    provider = StaticProvider(
        '{"resolves":true,"confidence":0.98,'
        '"reason":"The user supplied Wednesday at 1 PM as a concrete alternative."}'
    )
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)

    with Session(engine) as db:
        action = make_action()
        db.add(action)
        db.commit()
        obligation = ObligationStore(db).upsert_from_reconciliation(
            action=action,
            classification="NEW_OBLIGATION",
            reason="Reply with interview availability",
            message_id="incoming-1",
            message_internal_date_ms=1_000,
            evidence={"message_id": "incoming-1"},
        )
        sent = message(
            "manual-answer",
            when=datetime.now(timezone.utc),
            labels=("SENT",),
            body=(
                "Tuesday at 4 PM will not work, but Wednesday at 1 PM works.\n\n"
                "On Tue, Oct 13, 2026 at 9:00 AM Recruiter wrote:\n"
                "> Could you do Tuesday at 4 PM?\n"
            ),
        )

        semantic = ObligationResolver(provider).evaluate(
            obligation=obligation,
            sent_message=sent,
        )

        assert semantic.resolves is True
        assert "Wednesday at 1 PM works" in provider.last_message
        assert "Could you do Tuesday at 4 PM?" not in provider.last_message


def test_07_superseding_event_updates_existing_open_obligation_not_duplicate():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)

    with Session(engine) as db:
        action = make_action()
        db.add(action)
        db.commit()
        store = ObligationStore(db)
        first = store.upsert_from_reconciliation(
            action=action,
            classification="NEW_OBLIGATION",
            reason="Wednesday interview proposed",
            message_id="incoming-1",
            message_internal_date_ms=1_000,
            evidence={"message_id": "incoming-1"},
        )
        second = store.upsert_from_reconciliation(
            action=action,
            classification="SUPERSEDED_LATER",
            reason="Interview detail changed",
            message_id="incoming-2",
            message_internal_date_ms=2_000,
            evidence={"message_id": "incoming-2"},
        )

        assert second.id == first.id
        assert second.status == "OPEN"
        assert len(store.list_recent()) == 1


def test_08_resolved_obligation_is_not_reopened_on_replay():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    verified_at = datetime.now(timezone.utc)
    trigger_at = verified_at + timedelta(minutes=1)
    reply_at = trigger_at + timedelta(minutes=1)
    trigger_ms = int(trigger_at.timestamp() * 1000)

    with Session(engine) as db:
        action = make_action(now=verified_at)
        action.last_reconciliation = {
            "classification": "NEW_OBLIGATION",
            "reason": "Reply with interview availability",
            "requires_follow_up": True,
            "message_id": "incoming-replay",
            "evidence": {
                "message_id": "incoming-replay",
                "thread_id": "thread-1",
                "internal_date_ms": trigger_ms,
            },
        }
        db.add(action)
        db.commit()

        store = ObligationStore(db)
        obligation = store.upsert_from_reconciliation(
            action=action,
            classification="NEW_OBLIGATION",
            reason="Reply with interview availability",
            message_id="incoming-replay",
            message_internal_date_ms=trigger_ms,
            evidence={"message_id": "incoming-replay"},
        )
        obligation = store.resolve(
            obligation=obligation,
            status="RESOLVED_BY_USER",
            resolved_at=reply_at,
            message_id="manual-replay",
            reason="User supplied availability",
            evidence={"message_id": "manual-replay"},
        )

        gmail = FakeGmail(
            [
                message(
                    "incoming-replay",
                    when=trigger_at,
                    labels=("INBOX",),
                    body="Could you interview Tuesday at 4 PM?",
                ),
                message(
                    "manual-replay",
                    when=reply_at,
                    labels=("SENT",),
                    body="Wednesday at 1 PM works for me.",
                ),
            ]
        )
        provider = StaticProvider(
            '{"resolves":true,"confidence":0.99,'
            '"reason":"The reply supplies availability."}'
        )

        result = GmailReconciliationWorkflow(
            db=db,
            provider=provider,
            gmail=gmail,
        ).run(action_id=action.id)

        assert result["decision"] == "NO_NEW_EVENT"
        assert store.open_for_action(action.id) is None
        assert store.for_action_trigger(
            action_id=action.id,
            trigger_message_id="incoming-replay",
        ).status == "RESOLVED_BY_USER"
        assert len(store.list_recent()) == 1
