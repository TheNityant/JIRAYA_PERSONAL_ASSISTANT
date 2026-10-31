"""Playwright/Patchright Meet joiner with evidence capture.

States returned (never a bare boolean):

- READY          : preconditions fine (dry run / not executed)
- WAITING_FOR_AUTH: Google session missing -> user notified to log in manually
- JOINED         : in-call UI verified on the page
- KNOCKING       : clicked "Ask to join", awaiting host admission
- FAILED         : deterministic failure (no join control, admission denied, timeout)

Every state carries evidence: screenshots + the URL seen at each step, so the
Event Ledger can record MEET_JOIN_* events without trusting the LLM.

The import of patchright/playwright is deferred so unit tests and non-meet
workflows never require a browser install.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from app.meet_agent import selector_fallbacks as sf
from app.meet_agent import session_check as sc


@dataclass
class JoinEvidence:
    meet_url: str
    started_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    url_trace: list[str] = field(default_factory=list)
    screenshots: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    async def snapshot(self, directory: str, label: str, page) -> None:
        try:
            path = Path(directory)
            path.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
            target = path / f"{stamp}_{label}.png"
            await page.screenshot(path=str(target))
            self.screenshots.append(str(target))
        except Exception:  # evidence capture must never break the join itself
            self.notes.append(f"screenshot_failed:{label}")


@dataclass
class JoinResult:
    status: str  # READY | WAITING_FOR_AUTH | JOINED | KNOCKING | FAILED
    reason: str
    evidence: JoinEvidence | None = None


FAKE_MEDIA_FLAGS = (
    "--use-fake-ui-for-media-stream",
    "--use-fake-device-for-media-stream",
    "--disable-blink-features=AutomationControlled",
)


class MeetJoiner:
    def __init__(
        self,
        *,
        profile_dir: str = "./meet_profile",
        headless: bool = False,
        evidence_dir: str = "./meet_evidence",
        join_timeout_seconds: float = 120.0,
        auth_grace_seconds: float = 180.0,
        stay_seconds: float = 0.0,
        leave_call: bool = True,
    ):
        self.profile_dir = profile_dir
        self.headless = headless
        self.evidence_dir = evidence_dir
        self.join_timeout_seconds = join_timeout_seconds
        self.auth_grace_seconds = auth_grace_seconds
        self.stay_seconds = stay_seconds
        self.leave_call = leave_call

    async def _launch_context(self):
        try:
            from patchright.async_api import async_playwright  # type: ignore
        except ImportError:
            from playwright.async_api import async_playwright  # type: ignore

        pw = await async_playwright().start()
        context = await pw.chromium.launch_persistent_context(
            user_data_dir=self.profile_dir,
            headless=self.headless,
            args=list(FAKE_MEDIA_FLAGS),
            viewport=None,
        )
        return pw, context

    async def _click_first_visible(self, page, selectors) -> tuple[bool, str | None]:
        for selector in selectors:
            locator = page.locator(selector).first
            try:
                if await locator.is_visible():
                    await locator.click(timeout=5000)
                    return True, selector
            except Exception:
                continue
        return False, None

    async def _check_any_visible(self, page, selectors) -> bool:
        for selector in selectors:
            try:
                if await page.locator(selector).first.is_visible():
                    return True
            except Exception:
                continue
        return False

    async def _call_ended(self, page, evidence) -> bool:
        """True when Meet shows the host ended the call / we were removed."""
        try:
            if await self._check_any_visible(page, sf.MEETING_ENDED_MARKERS):
                evidence.notes.append("host_ended_call_detected")
                return True
        except Exception:
            pass  # page navigated/closed mid-check: treat as still-in-call
        return False

    async def _handle_speech_events(self, page, seen_action_count: list[int]) -> None:
        """Poll the local companion; unmute in Meet, wait for speech, re-mute."""
        try:
            import json
            import os
            import urllib.request
            from pathlib import Path

            localapp = os.environ.get("LOCALAPPDATA", "")
            token_path = Path(localapp) / "Jiraya" / "companion" / "companion.json"
            if not token_path.exists():
                return
            token = json.loads(token_path.read_text(encoding="utf-8")).get("token")
            if not token:
                return
            req = urllib.request.Request(
                "http://127.0.0.1:8765/meeting/status",
                headers={"Authorization": f"Bearer {token}"},
            )
            with urllib.request.urlopen(req, timeout=1) as resp:
                data = json.loads(resp.read().decode())
            actions = data.get("actions", [])
            if len(actions) > seen_action_count[0]:
                for act in actions[seen_action_count[0]:]:
                    if act.get("kind") == "SPOKE":
                        # Unmute microphone in Meet
                        await page.keyboard.press("Control+d")
                        print(f"\n[meet_agent] >>> UNMUTED IN GOOGLE MEET to speak: {act.get('reply_text', '')}")
                        words = len(act.get("reply_text", "").split())
                        wait_sec = max(3.0, min(15.0, words * 0.4 + 1.5))
                        await page.wait_for_timeout(int(wait_sec * 1000))
                        # Mute microphone back
                        await page.keyboard.press("Control+d")
                        print("[meet_agent] <<< MUTED MICROPHONE BACK IN GOOGLE MEET.\n")
                seen_action_count[0] = len(actions)
        except Exception:
            pass

        async def _wait_in_call(self, context, evidence, remaining_ms: int) -> None:
            page = context.pages[0]
            waited = 0
            seen_count = [0]
            while waited < remaining_ms:
                # 1️⃣ Check if the companion asked to end the meeting
                from app.local_companion.server import Companion
                if Companion.instance().meeting_should_end:
                    evidence.notes.append("requested_end_meeting")
                    # Click the leave button (reuse existing logic)
                    await self._click_first_visible(page, sf.LEAVE_CALL_SELECTORS)  # selector list defined in selector_fallbacks.py
                # Close the browser context – this will close the window
                    await context.close()
                    await context.owner.close()
                    return

            if await self._call_ended(page, evidence):
                return
            await self._handle_speech_events(page, seen_count)
            step = min(1000, remaining_ms - waited)
            await page.wait_for_timeout(step)
            waited += step

    async def _toggle_media_off(self, page, selectors, label: str, evidence) -> None:
        """Click a pre-join mic/camera toggle that is currently ON (best effort)."""
        for selector in selectors:
            try:
                locator = page.locator(selector).first
                aria = ((await locator.get_attribute("aria-label")) or "").lower()
                # "Turn on ..." means it is already OFF; "Turn off ..." means ON.
                if "turn on" in aria:
                    evidence.notes.append(f"{label}_already_off")
                    return
                if aria and await locator.is_visible():
                    await locator.click(timeout=5000)
                    evidence.notes.append(f"{label}_via:{selector}")
                    await page.wait_for_timeout(800)
                    return
            except Exception:
                continue
        evidence.notes.append(f"{label}_control_not_found")

    async def check_session(self, page) -> bool:
        """Deterministic: is this profile's Google session alive right now?"""
        await page.goto("https://meet.google.com/", wait_until="domcontentloaded")
        url = page.url
        signed_in = await self._check_any_visible(page, sc.SIGNED_IN_PAGE_MARKERS)
        signed_out = await self._check_any_visible(page, sc.SIGNED_OUT_PAGE_MARKERS)
        return sc.is_signed_in_evidence(signed_in, signed_out, url)

    async def _leave_call(self, context, evidence) -> None:
        """Click Meet's own 'Leave call' control if we're in the call."""
        if not self.leave_call:
            return
        try:
            page = context.pages[0]
            clicked, selector = await self._click_first_visible(
                page, sf.LEAVE_BUTTON_SELECTORS
            )
            if clicked:
                evidence.notes.append(f"left_call_via:{selector}")
                await page.wait_for_timeout(2000)  # let Meet register the leave
            else:
                evidence.notes.append("leave_button_not_found")
        except Exception as exc:
            evidence.notes.append(f"leave_error:{type(exc).__name__}")

    async def _finish(self, pw, context, evidence, status: str, reason: str) -> JoinResult:
        """Hold the browser open per stay_seconds, leave the call, then close.

        stay_seconds == 0  -> close immediately (still leaves the call politely)
        stay_seconds  > 0  -> wait that many seconds in the call first
        stay_seconds  < 0  -> wait until the user closes the window
        """
        if self.stay_seconds > 0:
            evidence.notes.append(f"staying_in_call_{int(self.stay_seconds)}s")
            await self._wait_in_call(context, evidence, int(self.stay_seconds * 1000))
        elif self.stay_seconds < 0:
            evidence.notes.append("staying_open_until_call_ends_or_window_closed")
            try:
                # Stay until either the host ends the call (detected on the page)
                # or the user closes the window by hand.
                seen_count = [0]
                while context.pages:
                    page = context.pages[0]
                    if await self._call_ended(page, evidence):
                        break
                    await self._handle_speech_events(page, seen_count)
                    await asyncio.sleep(1)
            except Exception:
                pass
        await self._leave_call(context, evidence)
        await context.close()
        await pw.stop()
        return JoinResult(status, reason, evidence)

    async def join(self, meet_url: str) -> JoinResult:
        evidence = JoinEvidence(meet_url=meet_url)
        pw = None
        context = None
        try:
            pw, context = await self._launch_context()
            page = context.pages[0] if context.pages else await context.new_page()

            # Step 1: session pre-check (never automates the login form).
            # If signed out, keep the browser OPEN for a grace window so the user
            # can sign in manually; re-check periodically, then continue in-place.
            session_alive = await self.check_session(page)
            if not session_alive:
                evidence.url_trace.append(page.url)
                evidence.notes.append("google_session_missing")
                await evidence.snapshot(self.evidence_dir, "auth_required", page)
                evidence.notes.append(
                    f"auth_grace_window_open_{int(self.auth_grace_seconds)}s_wait_for_manual_sign_in"
                )
                grace_deadline = asyncio.get_event_loop().time() + self.auth_grace_seconds
                while asyncio.get_event_loop().time() < grace_deadline:
                    await page.wait_for_timeout(3000)
                    session_alive = await self.check_session(page)
                    if session_alive:
                        evidence.notes.append("manual_sign_in_detected")
                        break
                if not session_alive:
                    await evidence.snapshot(self.evidence_dir, "auth_grace_expired", page)
                    await context.close()
                    await pw.stop()
                    return JoinResult("WAITING_FOR_AUTH", "GOOGLE_SESSION_MISSING", evidence)

            # Step 2: open the meeting.
            await page.goto(meet_url, wait_until="domcontentloaded")
            evidence.url_trace.append(page.url)
            if sc.looks_like_login_wall(page.url):
                evidence.notes.append("login_wall_midflow")
                await evidence.snapshot(self.evidence_dir, "auth_required", page)
                evidence.notes.append(
                    f"auth_grace_window_open_{int(self.auth_grace_seconds)}s_wait_for_manual_sign_in"
                )
                grace_deadline = asyncio.get_event_loop().time() + self.auth_grace_seconds
                while asyncio.get_event_loop().time() < grace_deadline:
                    await page.wait_for_timeout(3000)
                    if not sc.looks_like_login_wall(page.url):
                        evidence.notes.append("manual_sign_in_detected")
                        break
                if sc.looks_like_login_wall(page.url):
                    await evidence.snapshot(self.evidence_dir, "auth_grace_expired", page)
                    await context.close()
                    await pw.stop()
                    return JoinResult("WAITING_FOR_AUTH", "LOGIN_WALL_MIDFLOW", evidence)

            # Step 3: on the pre-join (green room) screen, wait briefly for the
            # device tray to render, then mute the mic and turn the camera OFF.
            try:
                await page.wait_for_selector(
                    '[aria-label*="microphone" i]', timeout=8000
                )
            except Exception:
                pass  # tray never rendered; toggles are best effort anyway
            await self._toggle_media_off(page, sf.PREJOIN_MIC_MUTE_SELECTORS, "mic_muted", evidence)
            await self._toggle_media_off(page, sf.PREJOIN_CAMERA_OFF_SELECTORS, "camera_off", evidence)

            # Step 4: wait for the pre-join control and click the fallback chain.
            deadline = asyncio.get_event_loop().time() + self.join_timeout_seconds
            clicked = False
            clicked_selector: str | None = None
            while asyncio.get_event_loop().time() < deadline and not clicked:
                clicked, clicked_selector = await self._click_first_visible(
                    page, sf.JOIN_BUTTON_SELECTORS
                )
                if not clicked:
                    await page.wait_for_timeout(1000)
            if clicked_selector:
                evidence.notes.append(f"clicked_selector:{clicked_selector}")
            await evidence.snapshot(self.evidence_dir, "after_join_click", page)

            if not clicked:
                evidence.notes.append("no_join_control_found")
                return await self._finish(pw, context, evidence, "FAILED", "NO_JOIN_CONTROL")

            # Step 4: deterministic outcome check.
            check_deadline = asyncio.get_event_loop().time() + self.join_timeout_seconds
            knocking_noted = False
            while asyncio.get_event_loop().time() < check_deadline:
                try:
                    if page.is_closed():
                        evidence.notes.append("window_closed_by_user")
                        return JoinResult("CLOSED", "BROWSER_WINDOW_CLOSED", evidence)
                except Exception:
                    break

                if await self._check_any_visible(page, sf.ADMITTED_MARKERS):
                    evidence.notes.append("in_call_ui_visible")
                    await evidence.snapshot(self.evidence_dir, "joined", page)
                    return await self._finish(pw, context, evidence, "JOINED", "IN_CALL_UI_VISIBLE")
                if await self._check_any_visible(page, sf.KNOCKING_MARKERS):
                    if not knocking_noted:
                        evidence.notes.append("knocking_wait_for_host")
                        await evidence.snapshot(self.evidence_dir, "knocking", page)
                        knocking_noted = True
                        print("\n[meet_agent] Bot is currently KNOCKING ('Asking to join...'). Waiting for host to click 'Admit' in Google Meet...\n")
                    try:
                        await page.wait_for_timeout(2000)
                    except Exception:
                        break
                    continue
                if await self._check_any_visible(page, sf.ADMIT_ERROR_MARKERS):
                    evidence.notes.append("admission_denied_or_call_ended")
                    await evidence.snapshot(self.evidence_dir, "denied", page)
                    return await self._finish(pw, context, evidence, "FAILED", "ADMISSION_DENIED")
                try:
                    await page.wait_for_timeout(1000)
                except Exception:
                    break

            if knocking_noted:
                evidence.notes.append("admission_timeout_while_knocking")
                return await self._finish(pw, context, evidence, "KNOCKING", "WAITING_FOR_HOST_ADMIT")
            evidence.notes.append("timeout_awaiting_admission")
            return await self._finish(pw, context, evidence, "FAILED", "ADMISSION_TIMEOUT")

        except Exception as exc:  # never leak a raw exception past evidence capture
            err_name = type(exc).__name__
            err_str = str(exc)
            if "TargetClosed" in err_name or "closed" in err_str.lower():
                evidence.notes.append("browser_window_closed")
                return JoinResult("CLOSED", "BROWSER_WINDOW_CLOSED", evidence)
            evidence.notes.append(f"exception:{err_name}")
            return JoinResult("FAILED", f"EXCEPTION:{err_name}", evidence)


def run_join(meet_url: str, **kwargs) -> JoinResult:
    """Sync entry point for non-async callers / API routes."""
    return asyncio.run(MeetJoiner(**kwargs).join(meet_url))
def notify_auth_required(*, user_id: str, meet_url: str, reason: str, db_session) -> None:
    """Record an ACTION_REQUIRED notification asking the user to log in manually.

    Called by the workflow layer when join() returns WAITING_FOR_AUTH. Kept here
    as a thin helper so the joiner stays framework-agnostic in core paths.
    """
    from app.services.notification_store import NotificationStore

    NotificationStore(db_session).create_once(
        user_id=user_id,
        category="ACTION_REQUIRED",
        title="Google sign-in needed to join meeting",
        message=(
            "Jiraya could not join the Google Meet because its browser profile is "
            "signed out. Open the bot's browser window and sign in once; the join "
            "will then proceed."
        ),
        severity="WARNING",
        source="meet_agent",
        source_entity_id=meet_url,
        dedupe_key=f"meet_auth_required:{meet_url}",
        metadata={"meet_url": meet_url, "reason": reason},
    )
