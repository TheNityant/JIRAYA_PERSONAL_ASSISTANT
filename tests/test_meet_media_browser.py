"""Real Chromium control regression, using local HTML only (never Google Meet).

Set JIRAYA_CHROMIUM_CONFIG to a JSON file with executablePath and args to enable.
"""

import asyncio
import json
import os
from pathlib import Path

import pytest

from app.meet_agent import selector_fallbacks as sf
from app.meet_agent.joiner import JoinEvidence
from app.meet_cloud.browser import CloudJoiner

pytestmark = pytest.mark.skipif(
    not os.getenv("JIRAYA_CHROMIUM_CONFIG"), reason="Browser fixture not configured"
)


def run_case(html, label="mic_muted", expected=True):
    async def run():
        from playwright.async_api import async_playwright

        config = json.loads(Path(os.environ["JIRAYA_CHROMIUM_CONFIG"]).read_text())
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(
                executable_path=config["executablePath"], args=config["args"], headless=True
            )
            try:
                page = await browser.new_page()
                await page.set_content(html)
                evidence = JoinEvidence("https://meet.google.com/abc-defg-hij")
                selectors = (
                    sf.PREJOIN_MIC_MUTE_SELECTORS
                    if label == "mic_muted"
                    else sf.PREJOIN_CAMERA_OFF_SELECTORS
                )
                joiner = CloudJoiner(
                    audio=object(),
                    settings=object(),
                    on_turn=None,
                    tick=None,
                    leave_on_speech=False,
                )
                assert await joiner._toggle_media_off(page, selectors, label, evidence) is expected
                return await page.evaluate("window.clicks || 0"), evidence.notes
            finally:
                await browser.close()

    return asyncio.run(run())


@pytest.mark.parametrize("media,label", [("microphone", "mic_muted"), ("camera", "camera_off")])
def test_both_word_orders_and_hidden_duplicate(media, label):
    clicks, notes = run_case(
        f"""<button style="display:none" aria-label="Turn on {media}"></button>
      <button aria-label="Turn {media} off (ctrl+d)" onclick="window.clicks=(window.clicks||0)+1; this.setAttribute('aria-label','Turn {media} on (ctrl+d)')">Control</button>""",
        label,
    )
    assert clicks == 1 and f"{label}_confirmed_off" in notes


@pytest.mark.parametrize("media,label", [("microphone", "mic_muted"), ("camera", "camera_off")])
def test_already_off_with_hidden_duplicate_never_unmutes(media, label):
    clicks, _ = run_case(
        f"""<button style="display:none" aria-label="Turn on {media}"></button>
      <button aria-label="Turn on {media} (ctrl+d)" onclick="window.clicks=1">Control</button>""",
        label,
    )
    assert clicks == 0


def test_delayed_control_and_delayed_state_change_are_retried_once():
    clicks, _ = run_case("""<script>setTimeout(()=>{
      document.body.innerHTML=`<button aria-label="Mute microphone" onclick="window.clicks=(window.clicks||0)+1; setTimeout(()=>this.setAttribute('aria-label','Unmute microphone'),600)">Mic</button>`;
    },600)</script>""")
    assert clicks == 1


def test_tooltip_label_is_supported():
    clicks, _ = run_case(
        """<div role="button" data-tooltip="Turn camera off" onclick="window.clicks=1; this.setAttribute('data-tooltip','Turn camera on')">Video</div>""",
        "camera_off",
    )
    assert clicks == 1


@pytest.mark.parametrize(
    "html",
    [
        '<button aria-label="Microphone options">Settings</button><p>Microphone is off</p>',
        '<button aria-label="Turn on microphone effects">Effects</button>',
        '<button aria-label="Turn off microphone" onclick="window.clicks=(window.clicks||0)+1">Mic</button>',
    ],
)
def test_unknown_or_unchanged_state_is_never_confirmed_off(html):
    clicks, notes = run_case(html, expected=False)
    assert clicks <= 1 and "mic_muted_not_confirmed_off" in notes


def test_failed_prejoin_capture_masks_inputs_and_does_not_capture_login_pages():
    async def run():
        from playwright.async_api import async_playwright

        config = json.loads(Path(os.environ["JIRAYA_CHROMIUM_CONFIG"]).read_text())
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(
                executable_path=config["executablePath"], args=config["args"], headless=True
            )
            try:
                page = await browser.new_page()
                await page.route(
                    "https://meet.google.com/**",
                    lambda route: route.fulfill(
                        content_type="text/html",
                        body='<input value="PRIVATE-INPUT"><button>Use microphone and camera</button><button aria-label="Google Account: private@example.test">Account</button>',
                    ),
                )
                await page.goto("https://meet.google.com/abc-defg-hij")
                joiner = CloudJoiner(
                    audio=object(),
                    settings=object(),
                    on_turn=None,
                    tick=None,
                    leave_on_speech=False,
                )
                await joiner._media_failure(page, JoinEvidence(page.url))
                assert joiner.failure_debug["screenshot_base64"]
                assert joiner.failure_debug["controls"] == ["Use microphone and camera"]
                assert "PRIVATE-INPUT" not in json.dumps(joiner.failure_debug)
                assert "private@example.test" not in json.dumps(joiner.failure_debug)
                joiner.failure_debug = None
                await page.route(
                    "https://accounts.google.com/**", lambda route: route.fulfill(body="Sign in")
                )
                await page.goto("https://accounts.google.com/v3/signin")
                await joiner._media_failure(page, JoinEvidence(page.url))
                assert joiner.failure_debug is None
            finally:
                await browser.close()

    asyncio.run(run())
