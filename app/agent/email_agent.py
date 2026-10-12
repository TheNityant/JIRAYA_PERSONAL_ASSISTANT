import json
import re
from typing import Literal

from pydantic import BaseModel, Field

from app.integrations.gmail.models import GmailMessage
from app.providers.base import ModelProvider


class EmailClassification(BaseModel):
    label: Literal["INTERNSHIP_INQUIRY", "OTHER"]
    confidence: float = Field(ge=0.0, le=1.0)
    reason: str


class EmailAgent:
    def __init__(self, provider: ModelProvider):
        self.provider = provider

    def classify(self, message: GmailMessage) -> EmailClassification:
        prompt = f"""
Classify this incoming email for Jiraya's approved internship workflow.

Return ONLY one JSON object with exactly these fields:
{{"label":"INTERNSHIP_INQUIRY|OTHER","confidence":0.0,"reason":"short reason"}}

Use INTERNSHIP_INQUIRY only when the message is materially about an internship,
internship application, internship opportunity, internship availability, recruiter
outreach for an internship, or a direct internship-related follow-up. Do not classify
newsletters, generic jobs, marketing, unrelated academic mail, or vague messages as
INTERNSHIP_INQUIRY.

From: {message.sender}
Subject: {message.subject}
Body:
{message.body[:8000]}
""".strip()
        raw = self.provider.complete(prompt, enable_thinking=False)
        return EmailClassification.model_validate(self._extract_json(raw))

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
                raise ValueError(f"Model did not return valid classification JSON: {raw[:200]}")
            return json.loads(match.group(0))
