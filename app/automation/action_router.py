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
    action forever. For an automation configured as AUTO, the runtime must never silently
    turn the user's policy back into an approval workflow: it either performs the allowed
    action or falls back to an in-app notification when the situation is no longer safely
    covered by the configured instructions.
    """

    def __init__(self, provider: ModelProvider):
        self.provider = provider

    def route(self, *, automation: Automation, message: GmailMessage) -> ActionRoute:
        body = extract_authored_text(message.body) or message.body
        prompt = f"""
You are Jiraya's action-safety router.

The email already MATCHED a user-defined automation configured for automatic execution.
Decide whether Jiraya may perform its configured automatic action for THIS PARTICULAR message.

Return ONLY JSON:
{{"decision":"AUTO_ACT|NOTIFY_ONLY","confidence":0.0,"reason":"short reason"}}

Automation semantic condition:
{str((automation.match_config or {}).get('semantic_condition') or '')}

Configured action type:
{str((automation.action_config or {}).get("type") or "DRAFT_REPLY")}

Configured action instructions:
{str((automation.action_config or {}).get('instructions') or '')}

Personal/context instructions:
{str((automation.context_config or {}).get('instructions') or '')}

Email:
from={message.sender}
subject={message.subject}
newly_authored_body={body[:8000]}

Rules:
- AUTO_ACT when this is clearly the routine situation described by the semantic condition AND the configured action instructions fully cover the response.
- A request that is exactly what the automation was created to handle is NOT, by itself, a reason to stop automatic execution.
- NOTIFY_ONLY when the message introduces material facts, changed requirements, complaints, failures, rejection, missing/incorrect work, disputes, sensitive changes, unsupported requests, or ambiguity that the configured action does not safely cover.
- A broad topical match (for example 'submission') does NOT authorize a generic auto-reply to every message on that topic.
- If uncertain, choose NOTIFY_ONLY. Never ask for approval here: the user already chose Automatic. The safe fallback for Automatic is notification, not silently changing policy.
""".strip()
        raw = self.provider.complete(prompt, enable_thinking=False)
        parsed = self._extract_json(raw)
        confidence = max(0.0, min(1.0, float(parsed.get("confidence", 0.0))))
        decision = str(parsed.get("decision") or "NOTIFY_ONLY")
        reason = str(parsed.get("reason") or "Action safety was uncertain.")

        # Backward-compatible fail-safe if a model still emits the old REQUIRE_APPROVAL
        # value: AUTO means automatic. Unsafe/uncertain AUTO work becomes a notification,
        # never an approval queue item.
        if decision == "REQUIRE_APPROVAL":
            decision = "NOTIFY_ONLY"
            reason = f"{reason} Jiraya notified instead because this automation is configured as Automatic."
        if decision not in {"AUTO_ACT", "NOTIFY_ONLY"}:
            decision = "NOTIFY_ONLY"
        if confidence < 0.65 and decision == "AUTO_ACT":
            decision = "NOTIFY_ONLY"
            reason = "The automatic action was not confident enough to execute safely; Jiraya notified instead."

        return ActionRoute(
            decision=decision,
            confidence=confidence,
            reason=reason,
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
                return {
                    "decision": "NOTIFY_ONLY",
                    "confidence": 0.0,
                    "reason": "Router returned invalid output.",
                }
            return json.loads(match.group(0))
