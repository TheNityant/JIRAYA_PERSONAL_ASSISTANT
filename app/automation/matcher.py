import json
import re
from dataclasses import dataclass

from app.integrations.gmail.content import extract_authored_text
from app.integrations.gmail.models import GmailMessage
from app.models import Automation
from app.providers.base import ModelProvider


@dataclass(frozen=True)
class AutomationMatchResult:
    matches: bool
    confidence: float
    reason: str
    deterministic_checks: dict[str, bool]


class AutomationMatcher:
    """Match external events against user-defined automation rules.

    Deterministic filters run first and can reject an event without spending an LLM call.
    A semantic condition, when configured, is evaluated only after deterministic filters pass.
    """

    def __init__(self, provider: ModelProvider):
        self.provider = provider

    def match_gmail(self, *, automation: Automation, message: GmailMessage) -> AutomationMatchResult:
        config = automation.match_config or {}
        body = extract_authored_text(message.body) or message.body
        checks = {
            "sender_contains": self._contains(message.sender, config.get("sender_contains")),
            "subject_contains": self._contains(message.subject, config.get("subject_contains")),
            "body_contains": self._contains(body, config.get("body_contains")),
        }
        if not all(checks.values()):
            failed = [name for name, passed in checks.items() if not passed]
            return AutomationMatchResult(
                matches=False,
                confidence=1.0,
                reason=f"Deterministic filter failed: {', '.join(failed)}",
                deterministic_checks=checks,
            )

        semantic_condition = str(config.get("semantic_condition") or "").strip()
        if not semantic_condition:
            return AutomationMatchResult(
                matches=True,
                confidence=1.0,
                reason="All configured deterministic filters matched.",
                deterministic_checks=checks,
            )

        prompt = f"""
You are Jiraya's automation matcher.

Decide whether this Gmail message satisfies the USER-DEFINED semantic condition.
Return ONLY one JSON object:
{{"matches":true,"confidence":0.0,"reason":"short reason"}}

User-defined semantic condition:
{semantic_condition}

Email:
from={message.sender}
subject={message.subject}
newly_authored_body={body[:8000]}

Rules:
- Judge meaning, not exact keyword overlap.
- Do not infer requirements not present in the user's condition.
- If uncertain, lower confidence rather than forcing a match.
""".strip()
        raw = self.provider.complete(prompt, enable_thinking=False)
        parsed = self._extract_json(raw)
        confidence = max(0.0, min(1.0, float(parsed.get("confidence", 0.0))))
        threshold = float(config.get("semantic_threshold", 0.75))
        matches = bool(parsed.get("matches")) and confidence >= threshold
        reason = str(parsed.get("reason") or "Semantic matcher returned no reason.")
        if bool(parsed.get("matches")) and confidence < threshold:
            reason = f"Below semantic threshold {threshold:.2f}: {reason}"
        return AutomationMatchResult(
            matches=matches,
            confidence=confidence,
            reason=reason,
            deterministic_checks=checks,
        )

    @staticmethod
    def _contains(value: str, expected: object) -> bool:
        expected_text = str(expected or "").strip()
        if not expected_text:
            return True
        return expected_text.casefold() in (value or "").casefold()

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
                raise ValueError(f"Model did not return valid automation-match JSON: {raw[:200]}")
            return json.loads(match.group(0))
