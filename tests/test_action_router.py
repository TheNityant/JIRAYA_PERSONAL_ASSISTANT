from app.automation.action_router import AutomationActionRouter
from app.integrations.gmail.models import GmailMessage
from app.models import Automation
from app.providers.base import ModelProvider


class JsonProvider(ModelProvider):
    def __init__(self, payload: str):
        self.payload = payload

    @property
    def provider_name(self) -> str:
        return "test"

    @property
    def model_name(self) -> str:
        return "test-router"

    def complete(self, message: str, *, enable_thinking: bool = True) -> str:
        return self.payload


def automation() -> Automation:
    return Automation(
        name="Submission acknowledgement",
        description="",
        enabled=True,
        source="gmail",
        trigger_type="NEW_EMAIL",
        match_config={"semantic_condition": "Emails about assignment submission deadlines"},
        context_config={},
        action_config={"type": "DRAFT_REPLY", "instructions": "Acknowledge a routine deadline reminder."},
        approval_policy="AUTO",
        system_pipeline=[],
    )


def message(body: str) -> GmailMessage:
    return GmailMessage(
        id="m1",
        thread_id="t1",
        sender="professor@example.edu",
        sender_name="Professor",
        reply_to=None,
        to="student@example.com",
        subject="Submission",
        rfc_message_id=None,
        snippet=body,
        body=body,
        labels=("INBOX",),
        internal_date_ms=1,
    )


def test_router_can_downgrade_broad_match_to_notification():
    provider = JsonProvider(
        '{"decision":"NOTIFY_ONLY","confidence":0.96,"reason":"The professor reports the submission is missing and asks for additional work."}'
    )
    result = AutomationActionRouter(provider).route(
        automation=automation(),
        message=message("I did not receive your file. The supplementary document is also missing."),
    )
    assert result.decision == "NOTIFY_ONLY"
    assert result.confidence == 0.96


def test_low_confidence_auto_action_falls_back_to_notification_not_approval():
    provider = JsonProvider(
        '{"decision":"AUTO_ACT","confidence":0.50,"reason":"Possibly a routine reminder."}'
    )
    result = AutomationActionRouter(provider).route(
        automation=automation(),
        message=message("Please see the updated submission information."),
    )
    assert result.decision == "NOTIFY_ONLY"


def test_legacy_require_approval_output_is_normalized_to_notification_for_auto():
    provider = JsonProvider(
        '{"decision":"REQUIRE_APPROVAL","confidence":0.91,"reason":"The model is uncertain about a changed detail."}'
    )
    result = AutomationActionRouter(provider).route(
        automation=automation(),
        message=message("There is a changed requirement in the submission."),
    )
    assert result.decision == "NOTIFY_ONLY"
    assert "Automatic" in result.reason
