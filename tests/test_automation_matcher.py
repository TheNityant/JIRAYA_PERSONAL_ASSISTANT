from datetime import datetime, timezone

from app.automation.matcher import AutomationMatcher
from app.integrations.gmail.models import GmailMessage
from app.models import Automation
from app.providers.base import ModelProvider


class FakeProvider(ModelProvider):
    def __init__(self, response: str):
        self.response = response
        self.calls = 0
        self.last_prompt = ""

    @property
    def provider_name(self) -> str:
        return "fake"

    @property
    def model_name(self) -> str:
        return "fake-model"

    def complete(self, message: str, *, enable_thinking: bool = True) -> str:
        self.calls += 1
        self.last_prompt = message
        return self.response


def make_automation(match_config: dict) -> Automation:
    now = datetime.now(timezone.utc)
    return Automation(
        id="automation-1",
        name="User automation",
        description="",
        enabled=True,
        source="gmail",
        trigger_type="NEW_EMAIL",
        match_config=match_config,
        context_config={},
        action_config={"type": "DRAFT_REPLY"},
        approval_policy="REQUIRED",
        system_pipeline=[],
        created_at=now,
        updated_at=now,
    )


def message(*, sender: str = "recruiter@example.com", subject: str = "Next steps", body: str) -> GmailMessage:
    return GmailMessage(
        id="message-1",
        thread_id="thread-1",
        sender=sender,
        sender_name="Recruiter",
        reply_to=None,
        to="me@example.com",
        subject=subject,
        rfc_message_id="<message-1@example.com>",
        snippet="",
        body=body,
        labels=("INBOX",),
        internal_date_ms=2_000_000_000_000,
    )


def test_deterministic_filter_can_reject_without_llm_call():
    provider = FakeProvider('{"matches":true,"confidence":1.0,"reason":"unused"}')
    automation = make_automation(
        {
            "sender_contains": "@company.com",
            "semantic_condition": "An employer wants an interview.",
        }
    )

    result = AutomationMatcher(provider).match_gmail(
        automation=automation,
        message=message(body="Can we schedule an interview?"),
    )

    assert result.matches is False
    assert provider.calls == 0


def test_semantic_match_is_user_defined_and_not_subject_hardcoded():
    provider = FakeProvider(
        '{"matches":true,"confidence":0.94,'
        '"reason":"The employer is asking to schedule an interview."}'
    )
    automation = make_automation(
        {
            "semantic_condition": "An employer is asking to schedule an interview.",
            "semantic_threshold": 0.8,
        }
    )

    result = AutomationMatcher(provider).match_gmail(
        automation=automation,
        message=message(
            subject="Quick chat",
            body=(
                "We enjoyed reviewing your profile. Could you meet with the engineering team Friday?\n\n"
                "On Tue, Someone wrote:\n> Internship application"
            ),
        ),
    )

    assert result.matches is True
    assert result.confidence == 0.94
    assert "Quick chat" in provider.last_prompt
    assert "Could you meet with the engineering team Friday?" in provider.last_prompt
    assert "> Internship application" not in provider.last_prompt
