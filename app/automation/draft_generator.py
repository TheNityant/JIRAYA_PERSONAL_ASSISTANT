from app.integrations.gmail.content import extract_authored_text
from app.integrations.gmail.models import GmailMessage
from app.models import Automation
from app.providers.base import ModelProvider


class DraftReplyGenerator:
    """Generate a reply from user-owned automation instructions, not a hardcoded workflow."""

    def __init__(self, provider: ModelProvider):
        self.provider = provider

    def generate(self, *, automation: Automation, message: GmailMessage) -> str:
        action_config = automation.action_config or {}
        context_config = automation.context_config or {}
        instructions = str(action_config.get("instructions") or "").strip()
        personal_context = str(context_config.get("instructions") or "").strip()
        body = extract_authored_text(message.body) or message.body

        prompt = f"""
You are Jiraya's reply-drafting agent.

Write the email reply that satisfies this USER-DEFINED automation. Return ONLY the
reply body as plain text. Do not return JSON, markdown fences, analysis, a subject line,
or commentary about the draft.

Automation name:
{automation.name}

Action instructions:
{instructions or 'Draft an appropriate concise reply to the email.'}

Personal context / operating instructions:
{personal_context or 'No additional personal context was supplied.'}

Incoming email:
from={message.sender}
sender_name={message.sender_name}
subject={message.subject}
newly_authored_body={body[:8000]}

Rules:
- Follow the user's action instructions and personal context.
- Do not invent availability, commitments, facts, dates, credentials, or preferences.
- If the email asks for information that the supplied context does not contain, write a
  safe response that acknowledges the request without fabricating the missing fact.
- Do not quote the original email unless the user's instructions explicitly require it.
- Keep the tone appropriate to the incoming message.
""".strip()
        result = self.provider.complete(prompt, enable_thinking=False).strip()
        if result.startswith("```") and result.endswith("```"):
            lines = result.splitlines()[1:-1]
            result = "\n".join(lines).strip()
        if not result:
            raise ValueError("Draft generator returned an empty reply")
        return result
