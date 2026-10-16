from app.automation.obligation_extractor import ObligationExtractor
from app.integrations.gmail.models import GmailMessage
from app.providers.base import ModelProvider


class JsonProvider(ModelProvider):
    @property
    def provider_name(self) -> str:
        return "test"

    @property
    def model_name(self) -> str:
        return "test-obligation"

    def complete(self, message: str, *, enable_thinking: bool = True) -> str:
        return (
            '{"creates_obligation":true,"confidence":0.97,'
            '"title":"Submit Venus assignment by Oct 19",'
            '"description":"Submit the Venus assignment by Monday, October 19."}'
        )


def test_deadline_email_creates_obligation():
    message = GmailMessage(
        id="deadline-1",
        thread_id="thread-1",
        sender="professor@example.edu",
        sender_name="Professor",
        reply_to=None,
        to="student@example.com",
        subject="Submission",
        rfc_message_id=None,
        snippet="Submission due Monday",
        body="The Venus submission is due on Monday, October 19.",
        labels=("INBOX",),
        internal_date_ms=1,
    )
    result = ObligationExtractor(JsonProvider()).extract(message=message)
    assert result.creates_obligation is True
    assert result.confidence == 0.97
    assert "Venus" in result.title
