"""Deterministic Google session check for the Meet joiner.

The joiner never automates the Google login form (that triggers Google's
automation detection). Instead, before joining, it checks whether the
persistent browser profile still holds a live Google session. If not, the
workflow returns WAITING_FOR_AUTH and the user is notified to log in
manually in the bot's browser window.

Pure helpers here (URL/page-markers) are dependency-free so they are
trivially testable; the async page probe lives in app.meet_agent.joiner.
"""

from __future__ import annotations

GOOGLE_SIGNIN_URL_MARKERS = ("accounts.google.com/ServiceLogin", "accounts.google.com/signin")
GOOGLE_CONSENT_URL_MARKERS = ("accounts.google.com/o/oauth2", "accounts.google.com/signin/oauth")
# A signed-out visit to meet.google.com redirects to the Workspace marketing
# site instead of showing the Meet app; that page proves no session exists.
GOOGLE_SIGNED_OUT_URL_MARKERS = ("workspace.google.com",)
# Only a URL on the Meet app host can carry a live Meet session.
MEET_APP_HOST_MARKERS = ("meet.google.com",)


def looks_like_signed_out_landing(url: str) -> bool:
    """True if ``url`` is a marketing/redirect page rather than the Meet app."""
    if not url:
        return False
    return any(marker in url for marker in GOOGLE_SIGNED_OUT_URL_MARKERS)


def looks_like_meet_app(url: str) -> bool:
    """True if ``url`` is on the Meet app host."""
    if not url:
        return False
    return any(marker in url for marker in MEET_APP_HOST_MARKERS)


def looks_like_login_wall(url: str) -> bool:
    """True if ``url`` is a Google sign-in / consent page rather than Meet."""
    if not url:
        return False
    return any(marker in url for marker in GOOGLE_SIGNIN_URL_MARKERS)


def looks_like_consent_screen(url: str) -> bool:
    """True if ``url`` is a Google OAuth consent redirect."""
    if not url:
        return False
    return any(marker in url for marker in GOOGLE_CONSENT_URL_MARKERS)


# Selectors that only exist on a signed-in Meet page (account chip).
SIGNED_IN_PAGE_MARKERS = (
    'img[alt*="Google Account" i]',
    'a[aria-label*="Google Account" i]',
    'button[aria-label*="Google Account" i]',
)

# Selectors that only exist on a signed-out page ("Sign in" button, top right).
SIGNED_OUT_PAGE_MARKERS = (
    'a:has-text("Sign in")',
    'a:has-text("Sign in with Google")',
    'button:has-text("Sign in")',
)


def is_signed_in_evidence(signed_in_visible: bool, signed_out_visible: bool, url: str) -> bool:
    """Deterministic decision: does the page show a live Google session?

    A live session requires: a URL on the Meet app host AND not a login-wall,
    consent, or signed-out landing URL AND (a signed-in marker visible OR no
    signed-out marker visible).
    """
    if looks_like_login_wall(url) or looks_like_consent_screen(url):
        return False
    if looks_like_signed_out_landing(url):
        return False
    if not looks_like_meet_app(url):
        return False
    if signed_out_visible and not signed_in_visible:
        return False
    return signed_in_visible or not signed_out_visible
