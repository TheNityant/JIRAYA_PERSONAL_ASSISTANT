import json
import re

from pydantic import BaseModel

from app.integrations.gmail.content import extract_authored_text
from app.integrations.gmail.models import GmailMessage
from app.providers.base import ModelProvider


class ObligationExtraction(BaseModel):
    creates_obligation: bool
    confidence: float
    title: str
    description: str


class ObligationExtractor:
    """Extract durable work that remains after replying to an incoming email."""

    def __init__(self, provider: ModelProvider):
        self.provider = provider

    def extract(self, *, message: GmailMessage) -> ObligationExtraction:
        body = extract_authored_text(message.body) or message.body
        prompt = f"""
You are Jiraya's obligation extractor.

Decide whether this incoming email creates a concrete task, deadline, commitment,
submission, meeting requirement, follow-up, or other work that remains outstanding for
the user even after an acknowledgement reply is sent.

Return ONLY JSON:
{{
  "creates_obligation":true,
  "confidence":0.0,
  "title":"short human-readable work item",
  "description":"what remains to be done, including deadline/detail when stated"
}}

Email:
from={message.sender}
subject={message.subject}
newly_authored_body={body[:8000]}

Rules:
- A request to submit something by a date is an obligation.
- An interview request requiring scheduling/attendance is an obligation.
- Pure announcements, thanks, receipts, and FYI messages are not obligations.
- Do not invent deadlines or requirements that are not present.
""".strip()
        raw = self.provider.complete(prompt, enable_thinking=False)
        parsed = self._extract_json(raw)
        confidence = max(0.0, min(1.0, float(parsed.get("confidence", 0.0))))
        creates = bool(parsed.get("creates_obligation")) and confidence >= 0.70
        title = str(parsed.get("title") or message.subject or "Follow-up required").strip()
        description = str(parsed.get("description") or "").strip()
        return ObligationExtraction(
            creates_obligation=creates,
            confidence=confidence,
            title=title[:255],
            description=description[:2000],
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
                    "creates_obligation": False,
                    "confidence": 0.0,
                    "title": "",
                    "description": "",
                }
            return json.loads(match.group(0))
