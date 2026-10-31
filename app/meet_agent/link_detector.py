"""Validate Meet URLs structurally; never follow or rewrite an email URL."""

import re
from html import unescape
from urllib.parse import urlsplit


def is_valid_meet_link(url: str) -> bool:
    try:
        parsed = urlsplit(url)
        return bool(
            len(url) <= 2048
            and not any(char.isspace() for char in url)
            and parsed.scheme == "https"
            and parsed.hostname == "meet.google.com"
            and not parsed.username
            and not parsed.password
            and parsed.port in {None, 443}
            and re.fullmatch(r"/[a-z]{3}-[a-z]{4}-[a-z]{3}", parsed.path, re.IGNORECASE)
            and not parsed.fragment
        )
    except (ValueError, TypeError):
        return False


def extract_meet_links(text: str) -> list[str]:
    links = []
    for match in re.finditer(r"https?://[^\s<>\"']+", unescape(text or ""), re.IGNORECASE):
        url = match.group().rstrip(".,;:!?)")
        if is_valid_meet_link(url) and url not in links:
            links.append(url)
    return links
