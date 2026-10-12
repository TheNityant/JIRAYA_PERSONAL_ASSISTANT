import json
import re
from typing import Literal

from pydantic import BaseModel

from app.integrations.gmail.models import GmailMessage
from app.models import Action
from app.providers.base import ModelProvider


class ReconciliationDecision(BaseModel):
    classification: Literal[
        "NEW_OBLIGATION",
        "SUPERSEDED_LATER",
        "EXTERNALLY_INVALIDATED",
        "NO_MATERIAL_CHANGE",
    ]
    original_action_validity: Literal[
        "STILL_VALID",
        "WAS_VALID_AT_EXECUTION",
        "NO_LONGER_CURRENT",
    ]
    requires_follow_up: bool
    reason: str


class EmailReconciler:
    def __init__(self, provider: ModelProvider):
        self.provider = provider

    def reconcile(self, *, action: Action, later_message: GmailMessage) -> ReconciliationDecision:
        if self._looks_like_delivery_failure(later_message):
            return ReconciliationDecision(
                classification="EXTERNALLY_INVALIDATED",
                original_action_validity="WAS_VALID_AT_EXECUTION",
                requires_follow_up=True,
                reason="A later delivery-failure event invalidated the business outcome after the send was verified.",
            )

        prompt = f"""
You are Jiraya's temporal reconciliator.

A prior EMAIL_REPLY action was VERIFIED_SUCCESS at execution time. A newer incoming
message later appeared in the same Gmail thread. Classify what the newer event means.

Return ONLY one JSON object with exactly these fields:
{{
  "classification":"NEW_OBLIGATION|SUPERSEDED_LATER|EXTERNALLY_INVALIDATED|NO_MATERIAL_CHANGE",
  "original_action_validity":"STILL_VALID|WAS_VALID_AT_EXECUTION|NO_LONGER_CURRENT",
  "requires_follow_up":true,
  "reason":"short reason"
}}

Rules:
- NEW_OBLIGATION: the old action remains valid, but the later message introduces a new request, task, question, deadline, interview step, or commitment.
- SUPERSEDED_LATER: the old action succeeded, but a later authoritative message replaces a previously relevant detail or state, such as changing Wednesday to Friday. Use the previous reconciliation context when present.
- EXTERNALLY_INVALIDATED: the old action succeeded when executed, but a later external event makes its intended outcome invalid or unusable.
- NO_MATERIAL_CHANGE: the newer message is acknowledgement, thanks, informational only, or otherwise creates no new obligation and does not replace/invalidate prior state.
- Never rewrite history. A verified action must not be reclassified as an execution failure merely because the world changed afterward.

Prior action:
action_type={action.action_type}
initial_status={action.initial_status}
current_status={action.current_status}
thread_id={action.external_thread_id}
context={json.dumps(action.context, ensure_ascii=False)}
previous_reconciliation={json.dumps(action.last_reconciliation or {}, ensure_ascii=False)}

Later message:
from={later_message.sender}
subject={later_message.subject}
body={later_message.body[:8000]}
""".strip()

        raw = self.provider.complete(prompt, enable_thinking=False)
        return ReconciliationDecision.model_validate(self._extract_json(raw))

    @staticmethod
    def _looks_like_delivery_failure(message: GmailMessage) -> bool:
        sender = message.sender.lower()
        subject = message.subject.lower()
        body = message.body.lower()
        sender_signal = "mailer-daemon" in sender or "postmaster" in sender
        text = f"{subject}\n{body}"
        text_signal = any(
            marker in text
            for marker in (
                "delivery status notification (failure)",
                "delivery failed",
                "undeliverable",
                "message not delivered",
                "address not found",
            )
        )
        return sender_signal and text_signal

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
                raise ValueError(f"Model did not return valid reconciliation JSON: {raw[:200]}")
            return json.loads(match.group(0))
