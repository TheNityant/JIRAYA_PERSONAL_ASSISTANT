"""One guest browser and one private audio sink per cloud worker session."""

import asyncio
import base64
import json
import logging
import os
import re
from datetime import UTC, datetime
from urllib.parse import urlsplit

from app.meet_agent import selector_fallbacks as sf
from app.meet_agent.joiner import FAKE_MEDIA_FLAGS, MeetJoiner


def should_leave(text):
    """Conservative fixed commands. A bare bye, quotes or a negation never ends attendance.

    Mixed meeting audio cannot prove host identity. This detects an explicit audible
    command from a participant; it never treats a diarization label as a host role.
    """
    text = re.sub(r"\s+", " ", text.lower()).strip(" .!?,")
    if any(
        word in text
        for word in (
            "don't",
            "do not",
            "not over",
            "not ended",
            "not leave",
            '"',
            "'meeting",
            "say bye",
            "if ",
            "when ",
        )
    ):
        return False
    return bool(
        re.fullmatch(
            r"(?:(?:okay|ok|alright|thanks everyone|thank you everyone)[,. ]+)?"
            r"(?:(?:jiraya(?: assistant)?|(?:the )?(?:meeting |note.?taking )?bot)[, ]+(?:please )?(?:leave(?: the (?:meeting|call))?|disconnect)(?: now)?"
            r"|(?:the |this )?meeting (?:is over|has ended)"
            r"|(?:everyone|everybody)[, ]+(?:you (?:can|may) |please )?leave(?: the (?:meeting|call))?(?: now)?"
            r"|(?:that is|that's) all for (?:today|this meeting))"
            r"(?:[,. ]+(?:thank you(?: everyone)?|thanks(?: everyone)?|goodbye|bye(?: everyone)?))*",
            text,
        )
    )


class AudioSink:
    def __init__(self, name):
        self.name = name
        self.module = None
        self.process = None

    async def setup(self):
        process = await asyncio.create_subprocess_exec(
            "pactl",
            "load-module",
            "module-null-sink",
            "sink_name=" + self.name,
            "rate=16000",
            "channels=1",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        output, _ = await process.communicate()
        if process.returncode:
            raise RuntimeError("Cloud audio sink could not start. Check PulseAudio in the worker.")
        self.module = output.decode().strip()

    async def frames(self):
        self.process = await asyncio.create_subprocess_exec(
            "ffmpeg",
            "-nostdin",
            "-loglevel",
            "error",
            "-f",
            "pulse",
            "-i",
            self.name + ".monitor",
            "-ac",
            "1",
            "-ar",
            "16000",
            "-f",
            "s16le",
            "pipe:1",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        while True:
            try:
                frame = await self.process.stdout.readexactly(6400)  # 200 ms PCM16
            except asyncio.IncompleteReadError:
                raise RuntimeError("Cloud meeting audio capture stopped.") from None
            yield frame

    async def close(self):
        if self.process and self.process.returncode is None:
            self.process.terminate()
            try:
                await asyncio.wait_for(self.process.wait(), 5)
            except TimeoutError:
                self.process.kill()
                await self.process.wait()
        if self.module:
            process = await asyncio.create_subprocess_exec(
                "pactl",
                "unload-module",
                self.module,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await process.wait()


async def listen(settings, audio, on_turn):
    from urllib.parse import urlencode

    from websockets.asyncio.client import connect

    query = urlencode(
        {
            "sample_rate": 16000,
            "speech_model": settings.assemblyai_speech_model,
            "format_turns": "true",
        }
    )
    async with connect(
        "wss://streaming.assemblyai.com/v3/ws?" + query,
        additional_headers={"Authorization": settings.assemblyai_api_key},
        open_timeout=20,
        close_timeout=5,
        max_size=1_000_000,
    ) as socket:

        async def send():
            async for frame in audio.frames():
                await socket.send(frame)

        sender = asyncio.create_task(send())

        async def receive():
            async for raw in socket:
                data = json.loads(raw)
                if (
                    data.get("type") == "Turn"
                    and data.get("end_of_turn")
                    and data.get("transcript", "").strip()
                ):
                    await on_turn(
                        {
                            "order": data.get("turn_order"),
                            "text": data["transcript"],
                            "speaker": str(data.get("speaker_label") or "unidentified participant"),
                            "at": datetime.now(UTC).isoformat(),
                        }
                    )
                elif data.get("type") == "Error" or data.get("error"):
                    raise RuntimeError("Live transcription provider returned an error.")
            raise RuntimeError("Live transcription disconnected before attendance ended.")

        reader = asyncio.create_task(receive())
        try:
            done, _ = await asyncio.wait([sender, reader], return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                task.result()
        finally:
            sender.cancel()
            reader.cancel()
            await asyncio.gather(sender, reader, return_exceptions=True)
            try:
                await socket.send(json.dumps({"type": "Terminate"}))
            except Exception:  # noqa: BLE001 - socket may already be closed during cleanup
                logging.getLogger(__name__).debug("Streaming socket already closed during cleanup")


class CloudJoiner(MeetJoiner):
    def __init__(
        self, *, audio, settings, on_turn, tick, leave_on_speech, expected_account="", **kwargs
    ):
        super().__init__(headless=True, auth_grace_seconds=0, **kwargs)
        self.expected_account = expected_account
        self.audio = audio
        self.settings = settings
        self.on_turn = on_turn
        self.tick = tick
        self.leave_on_speech = leave_on_speech
        self.leave_reason = ""
        self.failure_debug = None

    async def join(self, meet_url):
        from app.meet_agent.link_detector import is_valid_meet_link

        if is_valid_meet_link(meet_url):
            # Calendar links can select a different browser account with authuser.
            # Keep the canonical room, never transfer a local account index to AWS.
            parsed = urlsplit(meet_url)
            meet_url = f"https://meet.google.com{parsed.path}"
        return await super().join(meet_url)

    async def _launch_context(self):
        from playwright.async_api import async_playwright

        engine = await async_playwright().start()
        # Do not pass application/provider credentials into the Chromium environment.
        env = {
            key: value
            for key, value in os.environ.items()
            if key in {"PATH", "HOME", "LANG", "XDG_RUNTIME_DIR", "PULSE_SERVER", "LD_LIBRARY_PATH"}
        }
        env["PULSE_SINK"] = self.audio.name
        try:
            context = await engine.chromium.launch_persistent_context(
                self.profile_dir,
                headless=True,
                locale="en-US",
                ignore_default_args=["--mute-audio"],
                env=env,
                args=[
                    *FAKE_MEDIA_FLAGS,
                    "--autoplay-policy=no-user-gesture-required",
                    "--lang=en-US",
                ],
            )
            # Set permissions before Meet checks navigator.permissions. Chromium's
            # fake-device flags alone do not explicitly configure the origin grant.
            # Both virtual devices still have to be proven OFF before admission.
            await context.grant_permissions(
                ["microphone", "camera"], origin="https://meet.google.com"
            )
            return engine, context
        except Exception:
            await engine.stop()
            raise

    async def _check_any_visible(self, page, selectors):
        if selectors == sf.ADMITTED_MARKERS:
            # Do not mistake a waiting-room audio control for actual admission.
            return (
                await super()._check_any_visible(page, sf.LEAVE_BUTTON_SELECTORS)
                and await super()._check_any_visible(page, sf.ADMITTED_MARKERS)
                and not await super()._check_any_visible(page, sf.KNOCKING_MARKERS)
            )
        return await super()._check_any_visible(page, selectors)

    async def check_session(self, page, **kwargs):
        if getattr(self.settings, "meet_cloud_identity_mode", "guest") == "google_profile":
            from app.meet_cloud.profiles import account_matches

            if not await super().check_session(page, **kwargs):
                return False
            return await account_matches(page, self.expected_account)
        return True  # Guest mode has no Google browser session.

    async def _toggle_media_off(self, page, selectors, label, evidence):
        for selector in (
            ()
            if getattr(self.settings, "meet_cloud_identity_mode", "guest") == "google_profile"
            else ('input[placeholder="Your name"]', 'input[aria-label="Your name"]')
        ):
            field = page.locator(selector).first
            if await field.is_visible():
                await field.fill("Jiraya · AI meeting notes")
                break
        return await super()._toggle_media_off(page, selectors, label, evidence)

    async def _media_failure(self, page, evidence):
        """Capture only a failed pre-join screen, never an admitted call or login page."""
        if urlsplit(page.url).scheme != "https" or urlsplit(page.url).hostname != "meet.google.com":
            return
        debug = {"captured_at": datetime.now(UTC).isoformat(), "host": "meet.google.com"}
        try:
            # Do not inspect or include input values, cookies, URLs or page HTML.
            labels = await page.locator('button, [role="button"]').evaluate_all(
                """elements => elements.filter(el => el.getBoundingClientRect().width && el.getBoundingClientRect().height)
                .map(el => el.getAttribute('aria-label') || el.getAttribute('data-tooltip') || el.getAttribute('title') || el.innerText || '')
                .filter(text => /microphone|camera|join|continue|allow|permission/i.test(text))
                .slice(0, 30).map(text => text.slice(0, 160))"""
            )
            debug["controls"] = [
                re.sub(
                    r"https?://\S+", "[link]", re.sub(r"[\w.+-]+@[\w.-]+", "[email]", str(value))
                )
                for value in labels
            ]
            image = await page.screenshot(
                type="jpeg",
                quality=40,
                scale="css",
                timeout=5000,
                mask=[page.locator("input"), page.locator('[aria-label*="Google Account" i]')],
            )
            if len(image) <= 256_000:
                debug["screenshot_base64"] = base64.b64encode(image).decode("ascii")
            else:
                debug["capture_note"] = "Screenshot exceeded the diagnostic size limit."
        except Exception:  # noqa: BLE001 - diagnostics must not override the blocked join
            debug["capture_note"] = "The pre-join screen could not be fully captured."
        self.failure_debug = debug

    async def _wait_in_call(self, context, evidence, remaining_ms):
        async def turn(row):
            await self.on_turn(row)
            if self.leave_on_speech and should_leave(row["text"]):
                self.leave_reason = "EXPLICIT_SPOKEN_LEAVE"
                self.stop_event.set()

        listener = asyncio.create_task(listen(self.settings, self.audio, turn))
        deadline = asyncio.get_running_loop().time() + remaining_ms / 1000
        try:
            while context.pages and asyncio.get_running_loop().time() < deadline:
                if await self.tick() or self._cancelled():
                    self.leave_reason = self.leave_reason or "STOP_OR_TIME_LIMIT"
                    break
                if listener.done():
                    listener.result()
                    raise RuntimeError("Live listening stopped.")
                page = context.pages[0]
                if page.is_closed() or await self._call_ended(page, evidence):
                    self.leave_reason = "MEETING_ENDED_OR_REMOVED"
                    break
                for selectors, label in (
                    (sf.PREJOIN_MIC_MUTE_SELECTORS, "mic_muted"),
                    (sf.PREJOIN_CAMERA_OFF_SELECTORS, "camera_off"),
                ):
                    if not await super()._toggle_media_off(page, selectors, label, evidence):
                        raise RuntimeError("Microphone/camera off state could not be confirmed.")
                await asyncio.sleep(1)
        finally:
            listener.cancel()
            await asyncio.gather(listener, return_exceptions=True)
