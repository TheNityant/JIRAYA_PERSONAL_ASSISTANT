"""Generate short spoken replies from meeting transcript context.

The reply is produced through the existing Nebius provider so the meeting
assistant uses the same reasoning backend as the rest of Jiraya. The system
prompt keeps replies: short, spoken-style, and always disclosing that the
owner's assistant is speaking on their behalf.
"""

from __future__ import annotations

from app.providers.base import ModelProvider

MAX_CONTEXT_TURNS = 10
MAX_REPLY_CHARS = 400

SYSTEM_PROMPT = (
    "You are a helpful and intelligent meeting participant. Rules:\n"
    "(1) Answer directly and contextually to what was asked or said in the recent transcript.\n"
    "(2) NEVER introduce yourself. DO NOT say 'I am Jiraya' or 'I am the owner's assistant'. Jump straight into answering.\n"
    "(3) If someone simply says 'hello' or greets, greet them back warmly and ask how you can help.\n"
    "(4) Keep replies natural, concise (under 40 words), spoken-style, without markdown or bullet points.\n"
    "(5) If a question is asked, answer it accurately from the context."
)


class ReplyGenerator:
    def __init__(self, provider: ModelProvider):
        self.provider = provider

    def generate(self, *, transcript_tail: list[dict], owner_name: str, first_reply: bool = False) -> str:
        """transcript_tail: [{'speaker': str, 'text': str}, ...] oldest first."""
        lines = [
            f"{turn.get('speaker', 'unknown')}: {turn.get('text', '')}"
            for turn in transcript_tail[-MAX_CONTEXT_TURNS:]
        ]
        if not lines:
            raise ValueError("No transcript context available to draft a reply.")
        if self.provider is None:
            raise ValueError("No reasoning provider configured for the assistant.")
        intro_clause = (
            "This is the assistant's first reply: answer directly, warmly and helpfully without reciting introductions.\n"
            if first_reply
            else ""
        )
        prompt = (
            f"The meeting owner is {owner_name}. Participants addressed them by name.\n"
            f"{intro_clause}"
            f"{SYSTEM_PROMPT}\n\n"
            f"Recent transcript:\n"
            + "\n".join(lines)
            + "\n\nDraft your spoken direct response now:"
        )
        reply = self.provider.complete(prompt, enable_thinking=False).strip()
        if not reply:
            raise ValueError("The reasoning provider returned an empty reply.")
        return reply[:MAX_REPLY_CHARS]
