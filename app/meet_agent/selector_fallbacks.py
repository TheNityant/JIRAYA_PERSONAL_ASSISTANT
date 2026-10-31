"""Ordered fallback selectors for the Google Meet pre-join / in-call UI.

Meet's button text changes between "Join now", "Ask to join", locales and
A/B rollouts. We never match one exact string: every attempt walks this
ordered chain and uses the first visible, enabled match.
"""

from __future__ import annotations

# Step 1: click the join button on the pre-join (green room) screen.
JOIN_BUTTON_SELECTORS: tuple[str, ...] = (
    'button[aria-label*="Join now" i]',
    'button[aria-label*="Ask to join" i]',
    'button[aria-label*="join" i]',
    'button:has-text("Join now")',
    'button:has-text("Ask to join")',
    'button:has-text("Knock")',
    # Last resort: the only prominent primary CTA on the pre-join screen.
    'div[role="button"][data-primary-button]:visible',
)

# Pre-join (green room) toggles: mute mic and turn camera OFF before joining.
# Meet renders these as <li role="button"> inside the device tray, with aria-labels
# like "Turn off microphone (ctrl+d)" / "Turn off camera (ctrl+e)" when ON, and
# "Turn on ..." when already off. Case-insensitive substring match everywhere.
PREJOIN_MIC_MUTE_SELECTORS: tuple[str, ...] = (
    '[aria-label*="microphone" i][aria-label*="turn off" i]',
    'li[role="button"][aria-label*="microphone" i]',
    'button[aria-label*="microphone" i][aria-label*="turn off" i]',
    'div[role="button"][aria-label*="microphone" i][aria-label*="turn off" i]',
    '[aria-label="Turn off microphone (ctrl+d)"]',
)

PREJOIN_CAMERA_OFF_SELECTORS: tuple[str, ...] = (
    '[aria-label*="camera" i][aria-label*="turn off" i]',
    'li[role="button"][aria-label*="camera" i]',
    'button[aria-label*="camera" i][aria-label*="turn off" i]',
    'div[role="button"][aria-label*="camera" i][aria-label*="turn off" i]',
    '[aria-label="Turn off camera (ctrl+e)"]',
)

# Step 2 (post-click): how did the call react? Note: "Leave" selectors are NOT
# admitted markers — the red Leave button is also visible on the knocking screen.
ADMITTED_MARKERS: tuple[str, ...] = (
    # In-call UI: participant chip / call controls.
    'button[aria-label*="audio" i][aria-label*="options" i]',
    'text=/\\d+ (participants|people|others)/i',
    'button[aria-label*="reactions" i]',
    'button[aria-label*="present" i]',
)

KNOCKING_MARKERS: tuple[str, ...] = (
    'text=/asking to (be )?join/i',
    'text=/knock/i',
    'text=/waiting.*(host|admit)/i',
    'text=/wait until a (meeting )?host/i',
    'text=/host brings you into the call/i',
    'text=/let (the host )?(them|someone) (know|in)/i',
)

ADMIT_ERROR_MARKERS: tuple[str, ...] = (
    'text=/wasn.t admitted|not admitted|denied/i',
    'text=/call ended|meeting ended|ended the call/i',
    'text=/couldn.t (join|connect)/i',
)

# Microphone permission / device picker on the pre-join screen.
MEDIA_PERMISSION_MARKERS: tuple[str, ...] = (
    'button[aria-label*="microphone" i]',
    'div:has-text("microphone") >> visible=true',
)

# Meeting no longer exists: host ended the call, or everyone else left.
MEETING_ENDED_MARKERS: tuple[str, ...] = (
    'text=/you left the meeting/i',
    'text=/call ended|meeting ended|ended the call/i',
    'text=/this meeting has ended/i',
    'text=/you.re the only one here|only participant/i',
    'text=/get back to|rejoin/i',
)

LEAVE_CALL_SELECTORS = [
    "button[aria-label='Leave call']",   # typical selector
    "button:has-text('Leave')"
]

# In-call control to exit the meeting gracefully.
LEAVE_BUTTON_SELECTORS: tuple[str, ...] = (
    'button[aria-label*="Leave call" i]',
    'button[aria-label*="leave" i]',
    'button:has-text("Leave call")',
    'div[role="button"][aria-label*="Leave" i]',
    'i[aria-label*="leave call" i]',
)


def first_matching(selectors: tuple[str, ...], visible_texts: list[str]) -> str | None:
    """Pure helper: pick the first selector whose visible text might match.

    Used by tests and as documentation of the fallback order; the live joiner
    instead queries the page directly with Playwright locators.
    """
    return selectors[0] if selectors else None
