"""AssemblyAI streaming speech-to-text for the live Meet assistant.

Wires the meeting assistant to real-time transcription using AssemblyAI's
Universal-Streaming API (see their docs: StreamingClient / TurnEvent). The
module owns nothing about playback or the LLM; it converts raw PCM audio
frames into finalized transcript turns and hands them to the ingest callback.

Design:
- Lazy import of `assemblyai` so the app runs without the SDK installed.
- A background reader thread streams PCM frames (16 kHz, mono, s16le) into
  the WebSocket; finalized turns (end_of_turn + formatted) go to the callback.
- Thread-safe: one session at a time, mirroring MeetingManager's ownership.

The audio source is decided by the caller (system loopback capture of the
Meet call, a test generator, or any iterable of PCM bytes). All state that
persists stays free of audio and transcript content, like the rest of the
pipeline.
"""

from __future__ import annotations

import queue
import threading
from dataclasses import dataclass, field
from typing import Callable

from app.config import get_settings

SAMPLE_RATE = 16000  # AssemblyAI requires 16 kHz mono 16-bit PCM


@dataclass
class TranscriptTurn:
    """One finalized utterance from the stream."""

    text: str
    speaker: str = "unknown"
    audio_seconds: float = 0.0


@dataclass
class SpeechToTextStatus:
    connected: bool = False
    running: bool = False
    turns: int = 0
    audio_seconds: float = 0.0
    last_error: str = ""
    ready: bool = field(default=False)  # True when API key is configured


class SpeechToTextUnavailable(RuntimeError):
    """Raised when AssemblyAI is not configured or the SDK is missing."""


class AssemblyAIStream:
    """Thread-safe wrapper over AssemblyAI's v3 StreamingClient."""

    def __init__(
        self,
        on_turn: Callable[[TranscriptTurn], None],
        *,
        api_key: str = "",
    ):
        self._on_turn = on_turn
        self._settings = get_settings()
        self._api_key = api_key or self._settings.assemblyai_api_key
        self._client = None
        self._status = SpeechToTextStatus(ready=bool(self._api_key))
        self._guard = threading.RLock()
        self._frames: queue.Queue[bytes | None] = queue.Queue()
        self._thread: threading.Thread | None = None
        self._speaking = False

    # ------------------------------------------------------------------ status

    @property
    def status(self) -> SpeechToTextStatus:
        with self._guard:
            return SpeechToTextStatus(
                connected=self._status.connected,
                running=self._status.running,
                turns=self._status.turns,
                audio_seconds=self._status.audio_seconds,
                last_error=self._status.last_error,
                ready=self._status.ready,
            )

    @property
    def is_configured(self) -> bool:
        return bool(self._api_key)

    # ------------------------------------------------------------------ control

    def start(self, audio_source=None) -> None:
        """Open the WebSocket and start transcribing.

        audio_source: optional iterable of PCM frame chunks (16 kHz mono
        s16le). If omitted, frames pushed via `feed()` are used.
        """
        if not self.is_configured:
            raise SpeechToTextUnavailable(
                "ASSEMBLYAI_API_KEY is missing. Set the real key in your local "
                ".env file; never place credentials in .env.example."
            )
        with self._guard:
            if self._status.running:
                raise RuntimeError("A transcription session is already running.")
            client = self._build_client()
            client.connect(
                __import__(
                    "assemblyai.streaming.v3", fromlist=["StreamingParameters"]
                ).StreamingParameters(
                    sample_rate=SAMPLE_RATE,
                    format_turns=True,
                )
            )
            self._client = client
            self._status.connected = True
            self._status.running = True
            self._status.last_error = ""
            self._thread = threading.Thread(
                target=self._pump, args=(audio_source,), daemon=True
            )
            self._thread.start()

    def feed(self, frame: bytes) -> None:
        """Push one raw PCM chunk into the running stream."""
        if self._status.running:
            self._frames.put(frame)

    def stop(self) -> None:
        with self._guard:
            if not self._status.running:
                return
            self._status.running = False
            self._frames.put(None)  # unblock the pump
            if self._thread is not None:
                self._thread.join(timeout=5.0)
                self._thread = None
            if self._client is not None:
                client, self._client = self._client, None
                # disconnect() can block waiting on the server; never hang stop().
                threading.Thread(
                    target=self._disconnect_quietly, args=(client,), daemon=True
                ).start()
            self._status.connected = False

    @staticmethod
    def _disconnect_quietly(client) -> None:
        try:
            client.disconnect(terminate=True)
        except Exception:
            pass

    # ----------------------------------------------------------------- private

    def _build_client(self):
        try:
            from assemblyai.streaming.v3 import (
                StreamingClient,
                StreamingClientOptions,
                StreamingEvents,
            )
        except ImportError as exc:  # pragma: no cover - env install dependent
            raise SpeechToTextUnavailable(
                "The `assemblyai` package is not installed. Run: pip install assemblyai"
            ) from exc

        client = StreamingClient(
            StreamingClientOptions(api_key=self._api_key)
        )
        client.on(StreamingEvents.Begin, self._on_begin)
        client.on(StreamingEvents.Turn, self._on_turn_event)
        client.on(StreamingEvents.Termination, self._on_terminated)
        client.on(StreamingEvents.Error, self._on_error)
        return client

    def _pump(self, audio_source) -> None:
        """Drain the queue (or the given source) into the WebSocket."""
        import assemblyai.streaming.v3 as v3

        stream = self._client
        if stream is None:
            return

        def frame_iter():
            if audio_source is not None:
                yield from audio_source
                return
            while True:
                frame = self._frames.get()
                if frame is None:
                    return
                yield frame

        try:
            stream.stream(frame_iter())
        except Exception as exc:
            with self._guard:
                self._status.last_error = str(exc)

    # ------------------------------------------------------------- SDK events

    def _on_begin(self, client, event) -> None:
        self._speaking = False

    def _on_turn_event(self, client, event) -> None:
        # Only finalized, formatted turns become transcript rows; partial
        # turns (immutable, growing) are skipped so the LLM never sees
        # mid-word text.
        if not getattr(event, "end_of_turn", False):
            return
        text = (getattr(event, "transcript", "") or "").strip()
        if not text:
            return
        if getattr(event, "turn_is_formatted", False) is False:
            return
        with self._guard:
            self._status.turns += 1
        try:
            self._on_turn(TranscriptTurn(text=text, speaker="unknown"))
        except Exception:
            pass  # the assistant logs its own errors; never kill the stream

    def _on_terminated(self, client, event) -> None:
        with self._guard:
            self._status.audio_seconds = float(
                getattr(event, "audio_duration_seconds", 0.0) or 0.0
            )
            self._status.connected = False
            self._status.running = False

    def _on_error(self, client, error) -> None:
        with self._guard:
            self._status.last_error = str(error)
