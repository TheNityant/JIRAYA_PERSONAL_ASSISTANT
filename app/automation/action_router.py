import json
import re
from typing import Literal

from pydantic import BaseModel

from app.integrations.gmail.content import extract_authored_text
from app.integrations.gmail.models import GmailMessage
from app.models import Automation
from app.providers.base import ModelProvider


class ActionRoute(BaseModel):
    decision: Literal["AUTO_ACT", "REQUIRE_APPROVAL", "NOTIFY_ONLY"]
    confidence: float
    reason: str


class AutomationActionRouter:
    """Decide whether an AUTO automation is still safe to execute for this event.

    A semantic match establishes relevance, not blanket permission to repeat the same
    action forever. Later complaints, changed requirements, failures, or ambiguous
    requests are intentionally downgraded to user review/notification.
    """

    def __init__(self, provider: ModelProvider):
        self.provider = provider

    def route(self, *, automation: Automation, message: GmailMessage) -> ActionRoute:
        body = extract_authored_text(message.body) or message.body
        prompt = f"""
You are Jiraya's action-safety router.

The email already MATCHED a user-defined automation. Decide whether Jiraya may perform
its configured automatic reply for THIS PARTICULAR message.

Return ONLY JSON:
{{"decision":"AUTO_ACT|REQUIRE_APPROVAL|NOTIFY_ONLY","confidence":0.0,"reason":"short reason"}}

Automation semantic condition:
{str((automation.match_config or {}).get('semantic_condition') or '')}

Configured action instructions:
{str((automation.action_config or {}).get('instructions') or '')}

Personal/context instructions:
{str((automation.context_config or {}).get('instructions') or '')}

Email:
from={message.sender}
subject={message.subject}
newly_authored_body={body[:8000]}

Rules:
- AUTO_ACT only when the message is clearly the routine situation the automatic action was meant for.
- REQUIRE_APPROVAL when a reply could still be useful but the situation contains changed details, a new request, uncertainty, or facts requiring human review.
- NOTIFY_ONLY for complaints, failures, rejection, missing/incorrect work, disputes, sensitive changes, requests outside the configured instructions, or when repeating the prior style of reply would be inappropriate.
- A broad topical match (for example 'submission') does NOT authorize a generic auto-reply to every message on that topic.
- If uncertain, prefer REQUIRE_APPROVAL or NOTIFY_ONLY over AUTO_ACT.
""".strip()
        raw = self.provider.complete(prompt, enable_thinking=False)
        parsed = self._extract_json(raw)
        confidence = max(0.0, min(1.0, float(parsed.get("confidence", 0.0))))
        decision = str(parsed.get("decision") or "NOTIFY_ONLY")
        if decision not in {"AUTO_ACT", "REQUIRE_APPROVAL", "NOTIFY_ONLY"}:
            decision = "NOTIFY_ONLY"
        if confidence < 0.65 and decision == "AUTO_ACT":
            decision = "REQUIRE_APPROVAL"
        return ActionRoute(
            decision=decision,
            confidence=confidence,
            reason=str(parsed.get("reason") or "Action safety was uncertain."),
        )

    @staticmethod
    def _extract_json(raw: str) -> dict:
        text = raw.strip()
        if text.startswith("```"):
            text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
            text = re.sub(r"\s*```$", "", text)
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            match = re.search(r"\{.*\}", text, flags=re.DOTALL)
            if not match:
                return {"decision": "NOTIFY_ONLY", "confidence": 0.0, "reason": "Router returned invalid output."}
            return json.loads(match.group(0))
