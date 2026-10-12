from app.agent.email_agent import EmailAgent
from app.integrations.gmail.models import GmailMessage
from app.providers.base import ModelProvider


class FakeProvider(ModelProvider):
    @property
    def provider_name(self) -> str:
        return "fake"

    @property
    def model_name(self) -> str:
        return "fake-model"

    def complete(self, message: str, *, enable_thinking: bool = True) -> str:
        return '{"label":"INTERNSHIP_INQUIRY","confidence":0.97,"reason":"internship recruiter outreach"}'


def test_email_agent_parses_structured_classification():
    message = GmailMessage(
        id="m1",
        thread_id="t1",
        sender="recruiter@example.com",
        sender_name="Recruiter",
        reply_to=None,
        to="me@example.com",
        subject="Software Engineering Internship",
        rfc_message_id="<m1@example.com>",
        snippet="",
        body="Would you be interested in our internship program?",
    )
    result = EmailAgent(FakeProvider()).classify(message)
    assert result.label == "INTERNSHIP_INQUIRY"
    assert result.confidence == 0.97
