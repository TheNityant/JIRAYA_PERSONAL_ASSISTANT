from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.config import Settings
from app.db import Base
from app.integrations.gmail.models import GmailMessage
from app.providers.base import ModelProvider
from app.services.event_ledger import EventLedger
from app.workflows.internship_inquiry import InternshipInquiryWorkflow


class FakeProvider(ModelProvider):
    @property
    def provider_name(self) -> str:
        return "fake"

    @property
    def model_name(self) -> str:
        return "fake-model"

    def complete(self, message: str, *, enable_thinking: bool = True) -> str:
        return '{"label":"INTERNSHIP_INQUIRY","confidence":0.99,"reason":"clear internship inquiry"}'


class FakeGmail:
    def __init__(self):
        self.send_calls = 0

    def get_latest_unread_inbox(self):
        return GmailMessage(
            id="in-1",
            thread_id="thread-1",
            sender="recruiter@example.com",
            sender_name="Recruiter",
            reply_to=None,
            to="me@example.com",
            subject="Internship",
            rfc_message_id="<in-1@example.com>",
            snippet="",
            body="Are you available for an internship interview?",
        )

    def get_message(self, message_id: str):
        return self.get_latest_unread_inbox()

    def send_reply(self, *, source, body_text: str):
        self.send_calls += 1
        return {"id": "sent-1", "threadId": source.thread_id}


def make_settings(tmp_path: Path, *, external_actions: bool = False) -> Settings:
    template = tmp_path / "reply.txt"
    template.write_text("Hi, thanks. - {signature}", encoding="utf-8")
    return Settings(
        _env_file=None,
        nebius_api_key="fake",
        database_url="sqlite+pysqlite:///:memory:",
        gmail_reply_template_path=str(template),
        enable_external_actions=external_actions,
    )


def test_dry_run_never_sends(tmp_path: Path):
    settings = make_settings(tmp_path)
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    gmail = FakeGmail()

    with Session(engine) as db:
        result = InternshipInquiryWorkflow(
            settings=settings,
            db=db,
            provider=FakeProvider(),
            gmail=gmail,
        ).run(message_id=None, dry_run=True)

    assert result["decision"] == "DRY_RUN"
    assert "proposed_reply" in result
    assert gmail.send_calls == 0


def test_already_sent_message_is_not_sent_again(tmp_path: Path):
    settings = make_settings(tmp_path, external_actions=True)
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    gmail = FakeGmail()

    with Session(engine) as db:
        EventLedger(db).append(
            source="gmail",
            event_type="EMAIL_REPLY_SENT",
            entity_id="gmail:in-1",
            correlation_id="previous-run",
            payload={"sent_message_id": "sent-previous"},
        )
        result = InternshipInquiryWorkflow(
            settings=settings,
            db=db,
            provider=FakeProvider(),
            gmail=gmail,
        ).run(message_id=None, dry_run=False)

    assert result["decision"] == "ALREADY_HANDLED"
    assert gmail.send_calls == 0
