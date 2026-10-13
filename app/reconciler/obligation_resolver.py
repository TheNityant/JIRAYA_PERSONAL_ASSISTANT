import json
import re

from pydantic import BaseModel, Field

from app.integrations.gmail.models import GmailMessage
from app.models import Obligation
from app.providers.base import ModelProvider


class ObligationResolutionDecision(BaseModel):
    resolves: bool
    confidence: float = Field(ge=0.0, le=1.0)
    reason: str


class ObligationResolver:
    """Judge whether a later sent message actually satisfies an open obligation.

    Email replies commonly contain the entire previous conversation below the user's
    newly authored text. The resolver must never treat that quoted history as a new
    user action, otherwise an old recruiter question can accidentally satisfy its own
    obligation.
    """

    def __init__(self, provider: ModelProvider):
        self.provider = provider

    def evaluate(
        self,
        *,
        obligation: Obligation,
        sent_message: GmailMessage,
    ) -> ObligationResolutionDecision:
        authored_body = self.extract_authored_text(sent_message.body)
        if not authored_body:
            return ObligationResolutionDecision(
                resolves=False,
                confidence=0.99,
                reason="No new user-authored email content was found before the quoted thread history.",
            )

        prompt = f"""
You are Jiraya's obligation resolver.

An open obligation represents work the user still needs to complete. A later email was
sent from the user's Gmail account in the same thread. Decide whether the NEWLY AUTHORED
content of that sent message actually and materially satisfies the obligation.

Return ONLY one JSON object:
{{"resolves":true,"confidence":0.0,"reason":"short reason"}}

Rules:
- A reply existing is NOT enough by itself.
- Evaluate ONLY the newly authored content shown below. Quoted thread history has already
  been removed and must never be inferred as a new user answer.
- resolves=true only when the authored content answers, performs, or clearly settles the
  requested task represented by the obligation.
- For an interview-availability request, giving availability or a concrete alternative
  time resolves it.
- Acknowledgements such as "thanks", "noted", or "I'll reply later" do not resolve it.
- If uncertain, return resolves=false.

Open obligation:
type={obligation.obligation_type}
title={obligation.title}
description={obligation.description}
context={json.dumps(obligation.context or {}, ensure_ascii=False)}

Later sent email:
subject={sent_message.subject}
newly_authored_body={authored_body[:8000]}
""".strip()
        raw = self.provider.complete(prompt, enable_thinking=False)
        return ObligationResolutionDecision.model_validate(self._extract_json(raw))

    @staticmethod
    def extract_authored_text(body: str) -> str:
        """Return the top-posted text authored in this reply, excluding quoted history.

        Handles common Gmail/Outlook/plain-text reply separators. This is intentionally
        conservative: when the body appears to contain only quoted history, return an
        empty string rather than allowing stale text to satisfy an obligation.
        """
        if not body:
            return ""

        text = body.replace("\r\n", "\n").replace("\r", "\n").strip()
        if not text:
            return ""

        lines = text.split("\n")
        kept: list[str] = []

        for line in lines:
            stripped = line.strip()

            # Gmail/plain-text replies: "On Mon, ... wrote:"
            if re.match(r"^On\s.+\bwrote:\s*$", stripped, flags=re.IGNORECASE):
                break

            # Outlook and many enterprise mail clients.
            if re.match(r"^-{2,}\s*Original Message\s*-{2,}$", stripped, flags=re.IGNORECASE):
                break
            if re.match(r"^_{5,}$", stripped):
                break

            # If quoting starts, everything below normally belongs to prior history.
            if stripped.startswith(">"):
                break

            kept.append(line)

        authored = "\n".join(kept).strip()

        # Some clients begin the quoted block with From:/Sent:/To:/Subject: instead of a
        # visible separator. Cut only when those headers appear after authored content.
        header_block = re.search(
            r"\n(?:From|Sent|To|Subject):\s.+(?:\n|$)",
            authored,
            flags=re.IGNORECASE,
        )
        if header_block:
            authored = authored[: header_block.start()].strip()

        return authored

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
                raise ValueError(f"Model did not return valid obligation-resolution JSON: {raw[:200]}")
            return json.loads(match.group(0))
