from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.automation.reliable_executor import ReliableAutomationExecutor
from app.config import Settings
from app.db import Base
from app.integrations.gmail.models import GmailMessage
from app.models import AutomationExecution, Notification
from app.providers.base import ModelProvider
from app.services.automation_execution_store import AutomationExecutionStore
from app.services.automation_store import AutomationStore
from app.services.notification_store import NotificationStore
from app.services.obligation_store import ObligationStore


class SequenceProvider(ModelProvider):
    def __init__(self, responses: list[str]):
        self.responses = list(responses)
        self.calls: list[str] = []

    @property
    def provider_name(self) -> str:
        return "test"

    @property
    def model_name(self) -> str:
        return "sequence"

    def complete(self, message: str, *, enable_thinking: bool = True) -> str:
        self.calls.append(message)
        if not self.responses:
            raise AssertionError("Unexpected provider call")
        return self.responses.pop(0)


class FakeGmail:
    def __init__(self):
        self.source = GmailMessage(
            id="incoming-update",
            thread_id="thread-update",
            sender="friend@example.com",
            sender_name="Friend",
            reply_to=None,
            to="me@example.com",
            subject="Hackathon update",
            rfc_message_id="<incoming-update@example.com>",
            snippet="How is the hackathon going?",
            body="What is the current update on the Nebius x NVIDIA hackathon?",
            labels=("INBOX",),
            internal_date_ms=1_700_000_000_000,
        )
        self.messages = {self.source.id: self.source}
        self.send_calls = 0

    def get_latest_unread_inbox(self):
        return self.source

    def get_latest_inbox(self):
        return self.source

    def get_message(self, message_id: str):
        return self.messages[message_id]

    def send_reply(self, *, source: GmailMessage, body_text: str):
        self.send_calls += 1
        sent_id = f"sent-{self.send_calls}"
        self.messages[sent_id] = GmailMessage(
            id=sent_id,
            thread_id=source.thread_id,
            sender="me@example.com",
            sender_name="Me",
            reply_to=None,
            to=source.sender,
            subject=f"Re: {source.subject}",
            rfc_message_id=f"<{sent_id}@example.com>",
            snippet=body_text,
            body=body_text,
            labels=("SENT",),
            internal_date_ms=source.internal_date_ms + 1_000,
        )
        return {"id": sent_id, "threadId": source.thread_id}

    @staticmethod
    def addresses(value: str) -> set[str]:
        return {value.lower()} if value else set()


def test_verified_update_reply_closes_reply_type_obligation_immediately():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    gmail = FakeGmail()
    provider = SequenceProvider(
        [
            "I am currently working on Phase E, focused on complete end-to-end automation of the connected tools.",
            '{"creates_obligation":true,"confidence":0.97,"title":"Provide hackathon update","description":"Reply with the current Nebius x NVIDIA hackathon progress."}',
            '{"resolves":true,"confidence":0.98,"reason":"The sent reply directly provides the requested hackathon progress update."}',
        ]
    )

    with Session(engine) as db:
        automation = AutomationStore(db).create(
            name="Hackathon updates",
            description="",
            enabled=True,
            source="gmail",
            trigger_type="NEW_EMAIL",
            match_config={},
            context_config={},
            action_config={"type": "DRAFT_REPLY", "instructions": "Give my current Phase E update."},
            approval_policy="REQUIRED",
        )
        runner = ReliableAutomationExecutor(
            settings=Settings(enable_external_actions=True),
            db=db,
            provider=provider,
            gmail=gmail,
        )

        prepared = runner.run(automation=automation)
        execution = db.get(AutomationExecution, prepared["execution"]["id"])
        result = runner.approve(execution=execution)

        assert result["verification_status"] == "VERIFIED_SUCCESS"
        assert result["obligation_status"] == "RESOLVED_BY_AGENT"
        obligation = ObligationStore(db).list_recent()[0]
        assert obligation.status == "RESOLVED_BY_AGENT"
        assert NotificationStore(db).unread_count(user_id="local-dev-user") >= 1
        stale_attention = db.query(Notification).filter(
            Notification.obligation_id == obligation.id,
            Notification.category == "ACTION_REQUIRED",
            Notification.read_at.is_(None),
        ).count()
        assert stale_attention == 0


def test_editing_automation_supersedes_old_waiting_approval_execution():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)

    with Session(engine) as db:
        store = AutomationStore(db)
        automation = store.create(
            name="Hackathon updates",
            description="",
            enabled=True,
            source="gmail",
            trigger_type="NEW_EMAIL",
            match_config={},
            context_config={},
            action_config={"type": "DRAFT_REPLY", "instructions": "Draft an update."},
            approval_policy="REQUIRED",
        )
        execution_store = AutomationExecutionStore(db)
        execution = execution_store.create(
            automation_id=automation.id,
            correlation_id="corr-old",
            source_event_id="message-1",
            source_thread_id="thread-1",
            action_type="DRAFT_REPLY",
            approval_policy="REQUIRED",
            status="AWAITING_APPROVAL",
            match_result={"matches": True},
            draft_body="Old draft",
        )
        NotificationStore(db).create_once(
            user_id="local-dev-user",
            category="ACTION_REQUIRED",
            severity="WARNING",
            title="Draft ready",
            message="Waiting for approval",
            source="automation",
            automation_id=automation.id,
            dedupe_key="old-approval",
            metadata={"execution_id": execution.id},
        )

        store.update(automation, {"approval_policy": "AUTO"})

        db.refresh(execution)
        assert execution.status == "SUPERSEDED_BY_CONFIG"
        assert execution_store.latest_for_event(
            automation_id=automation.id,
            source_event_id="message-1",
        ) is None
        notification = db.query(Notification).filter(Notification.dedupe_key == "old-approval").one()
        assert notification.read_at is not None
