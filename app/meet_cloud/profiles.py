"""Operator-assisted, account-bound Google browser profiles; no password handling."""

import hashlib
import re
from pathlib import Path


def profile_key(user_id: str, calendar_account: str) -> str:
    return hashlib.sha256((user_id + "\0" + calendar_account.lower()).encode()).hexdigest()


def profile_path(root: str, user_id: str, calendar_account: str) -> Path:
    return Path(root) / profile_key(user_id, calendar_account)


async def account_matches(page, expected: str) -> bool:
    from app.meet_agent.session_check import looks_like_meet_app

    if not expected or not looks_like_meet_app(page.url):
        return False
    controls = page.locator('[aria-label*="Google Account" i]:visible')
    for index in range(await controls.count()):
        label = await controls.nth(index).get_attribute("aria-label") or ""
        emails = re.findall(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", label)
        if expected.casefold() in [email.casefold() for email in emails]:
            return True
    return False
