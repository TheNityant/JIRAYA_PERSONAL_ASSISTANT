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


def valid_web_url(value: str) -> bool:
    """Validate link syntax without opening it or resolving its hostname."""
    from urllib.parse import urlsplit

    if len(value) > 2048 or any(char.isspace() or ord(char) < 32 or ord(char) == 127 for char in value):
        return False
    try:
        parsed = urlsplit(value)
        port = parsed.port
        return bool(
            parsed.scheme.lower() in {"http", "https"}
            and parsed.hostname and not parsed.username and not parsed.password
            and (port is None or 0 < port <= 65535)
        )
    except ValueError:
        return False


def source_links(body: str) -> list[str]:
    """Copy unique authored HTTP(S) links, excluding quoted conversation history."""
    authored = extract_authored_text(body)
    links = []
    for found in re.finditer(r'''https?://[^\s<>"'`]+''', authored, flags=re.IGNORECASE):
        value = found.group().rstrip(".,")
        for last, first in ((")", "("), ("]", "["), ("}", "{")):
            while value.endswith(last) and value.count(last) > value.count(first):
                value = value[:-1]
        if valid_web_url(value) and value not in links:
            links.append(value)
        if len(links) == 20:
            break
    return links
