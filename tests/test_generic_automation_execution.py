from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.automation.executor import AutomationExecutor
from app.config import Settings
from app.db import Base
from app.integrations.gmail.models import GmailMessage
from app.models import Action, Automation, AutomationExecution
from app.providers.base import ModelProvider


class DraftProvider(ModelProvider):
    def __init__(self, draft: str = "Thanks for the reminder. I will submit it before Friday."):
        self.draft = draft
        self.calls = 0

    @property
    def provider_name(self) -> str:
        return "test"

    @property
    def model_name(self) -> str:
        return "draft-test"

    def complete(self, message: str, *, enable_thinking: bool = True) -> str:
        self.calls += 1
        return self.draft


class FakeGmail:
    def __init__(self):
        self.source = GmailMessage(
            id="incoming-1",
            thread_id="thread-1",
            sender="admin@college.edu",
            sender_name="College Admin",
            reply_to=None,
            to="student@example.com",
            subject="Assignment deadline",
            rfc_message_id="<incoming-1@example.com>",
            snippet="Please submit before Friday",
            body="Please submit your VLSI assignment before Friday.",
            labels=("INBOX",),
            internal_date_ms=1_700_000_000_000,
        )
        self.messages = {self.source.id: self.source}
        self.send_calls = 0

    def get_latest_unread_inbox(self):
        return self.source

    def get_message(self, message_id: str):
        return self.messages[message_id]

    def send_reply(self, *, source: GmailMessage, body_text: str):
        self.send_calls += 1
        sent_id = f"sent-{self.send_calls}"
        self.messages[sent_id] = GmailMessage(
            id=sent_id,
            thread_id=source.thread_id,
            sender="student@example.com",
            sender_name="Student",
            reply_to=None,
            to=source.sender,
            subject=f"Re: {source.subject}",
            rfc_message_id=f"<{sent_id}@example.com>",
            snippet=body_text,
            body=body_text,
            labels=("SENT",),
            internal_date_ms=source.internal_date_ms + self.send_calls * 1000,
        )
        return {"id": sent_id, "threadId": source.thread_id}

    @staticmethod
    def addresses(value: str) -> set[str]:
        return {value.lower()} if value else set()


def make_automation(*, policy: str = "REQUIRED") -> Automation:
    return Automation(
        name="College deadline watch",
        description="",
        enabled=True,
        source="gmail",
        trigger_type="NEW_EMAIL",
        match_config={},
        context_config={"instructions": "Do not invent commitments."},
        action_config={
            "type": "DRAFT_REPLY",
            "instructions": "Acknowledge the deadline and reply concisely.",
        },
        approval_policy=policy,
        system_pipeline=[],
    )


def executor(db: Session, *, external: bool, gmail: FakeGmail, provider: DraftProvider):
    settings = Settings(enable_external_actions=external)
    return AutomationExecutor(
        settings=settings,
        db=db,
        provider=provider,
        gmail=gmail,
    )


def test_draft_only_generates_persisted_draft_without_send():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    gmail = FakeGmail()
    provider = DraftProvider()

    with Session(engine) as db:
        automation = make_automation(policy="DRAFT_ONLY")
        db.add(automation)
        db.commit()
        db.refresh(automation)

        result = executor(db, external=False, gmail=gmail, provider=provider).run(automation=automation)

        assert result["decision"] == "DRAFT_READY"
        assert result["execution"]["status"] == "DRAFT_READY"
        assert "Friday" in result["execution"]["draft_body"]
        assert gmail.send_calls == 0
        assert db.query(AutomationExecution).count() == 1


def test_required_policy_waits_for_approval():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    gmail = FakeGmail()
    provider = DraftProvider()

    with Session(engine) as db:
        automation = make_automation(policy="REQUIRED")
        db.add(automation)
        db.commit()
        db.refresh(automation)

        result = executor(db, external=True, gmail=gmail, provider=provider).run(automation=automation)

        assert result["decision"] == "AWAITING_APPROVAL"
        assert result["execution"]["status"] == "AWAITING_APPROVAL"
        assert gmail.send_calls == 0


def test_approval_sends_verifies_and_creates_action():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    gmail = FakeGmail()
    provider = DraftProvider()

    with Session(engine) as db:
        automation = make_automation(policy="REQUIRED")
        db.add(automation)
        db.commit()
        db.refresh(automation)
        runner = executor(db, external=True, gmail=gmail, provider=provider)
        prepared = runner.run(automation=automation)
        execution = db.get(AutomationExecution, prepared["execution"]["id"])

        result = runner.approve(
            execution=execution,
            edited_draft="Thank you. I have noted the Friday deadline.",
        )

        assert result["decision"] == "SENT"
        assert result["verification_status"] == "VERIFIED_SUCCESS"
        assert result["execution"]["status"] == "VERIFIED_SUCCESS"
        assert result["execution"]["draft_body"] == "Thank you. I have noted the Friday deadline."
        assert gmail.send_calls == 1
        assert db.query(Action).count() == 1


def test_required_approval_is_blocked_by_global_safety_gate():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    gmail = FakeGmail()
    provider = DraftProvider()

    with Session(engine) as db:
        automation = make_automation(policy="REQUIRED")
        db.add(automation)
        db.commit()
        db.refresh(automation)
        runner = executor(db, external=False, gmail=gmail, provider=provider)
        prepared = runner.run(automation=automation)
        execution = db.get(AutomationExecution, prepared["execution"]["id"])

        result = runner.approve(execution=execution)

        assert result["decision"] == "BLOCKED_BY_POLICY"
        assert gmail.send_calls == 0


def test_repeated_run_reuses_existing_execution_instead_of_duplicate_send():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    gmail = FakeGmail()
    provider = DraftProvider()

    with Session(engine) as db:
        automation = make_automation(policy="REQUIRED")
        db.add(automation)
        db.commit()
        db.refresh(automation)
        runner = executor(db, external=True, gmail=gmail, provider=provider)

        first = runner.run(automation=automation)
        second = runner.run(automation=automation)

        assert first["decision"] == "AWAITING_APPROVAL"
        assert second["decision"] == "EXISTING_EXECUTION"
        assert second["execution"]["id"] == first["execution"]["id"]
        assert db.query(AutomationExecution).count() == 1
        assert gmail.send_calls == 0
