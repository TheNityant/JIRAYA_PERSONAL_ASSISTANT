import re


def extract_authored_text(body: str) -> str:
    """Return newly authored top-posted email text, excluding quoted history.

    Gmail, Outlook, and many plain-text clients append earlier conversation content
    underneath the new reply. Semantic agents should reason over the new contribution,
    while the raw body remains available separately as evidence.
    """
    if not body:
        return ""

    text = body.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not text:
        return ""

    gmail_quote = re.search(
        r"(?:^|\n)On\s.+?\bwrote:\s*(?:\n|$)",
        text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if gmail_quote:
        text = text[: gmail_quote.start()].strip()

    original_message = re.search(
        r"(?:^|\n)-{2,}\s*Original Message\s*-{2,}(?:\n|$)",
        text,
        flags=re.IGNORECASE,
    )
    if original_message:
        text = text[: original_message.start()].strip()

    if not text:
        return ""

    kept: list[str] = []
    for line in text.split("\n"):
        stripped = line.strip()
        if re.match(r"^_{5,}$", stripped):
            break
        if stripped.startswith(">"):
            break
        kept.append(line)

    authored = "\n".join(kept).strip()

    header_block = re.search(
        r"(?:^|\n)(?:From|Sent|To|Subject):\s.+(?:\n|$)",
        authored,
        flags=re.IGNORECASE,
    )
    if header_block:
        authored = authored[: header_block.start()].strip()

    return authored
