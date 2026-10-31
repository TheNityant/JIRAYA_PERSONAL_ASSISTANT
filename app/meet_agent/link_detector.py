"""Detect and validate Google Meet links inside arbitrary text (e.g. email bodies).

Part of the new meet_agent layer. Kept dependency-free and pure so it is
trivially testable and cannot break the rest of the project.

Meet codes are always: 3 letters, hyphen, 4 letters, hyphen, 3 letters.
Example: https://meet.google.com/abc-defg-hij
"""

from __future__ import annotations

import re

MEET_LINK_RE = re.compile(
    r"https?://meet\.google\.com/[a-z]{3}-[a-z]{4}-[a-z]{3}",
    re.IGNORECASE,
)


def extract_meet_links(text: str) -> list[str]:
    """Return all unique Google Meet links found in ``text``.

    Trims trailing punctuation so links inside sentences/parentheses work:
    "join https://meet.google.com/abc-defg-hij." -> the URL without the dot.
    """
    if not text:
        return []
    found: list[str] = []
    for match in MEET_LINK_RE.finditer(text):
        url = match.group(0).rstrip(".,;:!?)>(")
        if url not in found:
            found.append(url)
    return found


def is_valid_meet_link(url: str) -> bool:
    """True if ``url`` is a well-formed Google Meet link (format check only).

    Rejects look-alikes with digits in the code (e.g. abc-defg-hi1),
    since real Meet codes contain only letters.
    """
    if not url:
        return False
    url = url.strip()
    if not MEET_LINK_RE.fullmatch(url):
        return False
    code = url.rstrip("/").rsplit("/", 1)[-1]
    if any(ch.isdigit() for ch in code):
        return False
    return True
