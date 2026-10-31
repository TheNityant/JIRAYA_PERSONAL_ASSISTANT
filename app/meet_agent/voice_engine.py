"""Owner voice cloning with Coqui XTTS-v2.

One-time setup: the owner records a clean 30-60 second sample. XTTS zero-shot
clones the timbre from that reference clip, so every generated reply sounds
like the owner.

Design constraints:
- Heavy imports (torch / TTS) are DEFERRED. Importing this module, running the
  tests, or configuring the voice never loads a multi-GB model.
- The reference sample is validated (duration, format) before any model call.
- Generated audio is written to disk and returned as a path; playback/injection
  into the Meet microphone is a separate layer (virtual mic / loopback).
- Sample files live under a private runtime directory and are never committed
  (same hygiene policy as .env and credentials).
"""

from __future__ import annotations

import os
import shutil
import subprocess
import wave
from dataclasses import dataclass
from pathlib import Path

MIN_SAMPLE_SECONDS = 20.0
MAX_SAMPLE_SECONDS = 120.0
MAX_SAMPLE_BYTES = 20 * 1024 * 1024

DEFAULT_OUTPUT_DIR = Path("./meet_voice_output")
DEFAULT_SAMPLE_PATH = Path("./runtime/owner_voice_sample.wav")


class VoiceSampleProblem(ValueError):
    """Raised for invalid or unsafe voice samples."""


@dataclass(frozen=True)
class VoiceConfig:
    language: str = "en"
    sample_path: Path = DEFAULT_SAMPLE_PATH
    output_dir: Path = DEFAULT_OUTPUT_DIR
    ready: bool = False  # True only after a validated sample exists


def validate_voice_sample(path: Path) -> float:
    """Return the sample duration in seconds; raise VoiceSampleProblem if unusable."""
    if not path.exists():
        raise VoiceSampleProblem("Voice sample file not found. Record the sample first.")
    if path.stat().st_size > MAX_SAMPLE_BYTES:
        raise VoiceSampleProblem("Voice sample is too large; keep it under 20 MiB.")
    if path.suffix.lower() != ".wav":
        raise VoiceSampleProblem("Voice sample must be a .wav file.")
    try:
        with wave.open(str(path), "rb") as handle:
            frames = handle.getnframes()
            rate = handle.getframerate()
            channels = handle.getnchannels()
    except (wave.Error, EOFError, OSError) as exc:
        raise VoiceSampleProblem("Voice sample is not a readable WAV file.") from exc
    if rate < 16000:
        raise VoiceSampleProblem("Voice sample must be recorded at 16 kHz or higher.")
    if channels not in (1, 2):
        raise VoiceSampleProblem("Voice sample must be mono or stereo.")
    duration = frames / float(rate)
    if duration < MIN_SAMPLE_SECONDS:
        raise VoiceSampleProblem(
            f"Voice sample is too short ({duration:.0f}s). Record at least "
            f"{MIN_SAMPLE_SECONDS:.0f} seconds of natural speech."
        )
    if duration > MAX_SAMPLE_SECONDS:
        raise VoiceSampleProblem(
            f"Voice sample is too long ({duration:.0f}s). Keep it under "
            f"{MAX_SAMPLE_SECONDS:.0f} seconds."
        )
    return duration


def _register_ffmpeg_dlls() -> None:
    """Windows: torchcodec needs FFmpeg DLLs registered before it imports."""
    if os.name != "nt":
        return
    candidates = [
        Path(__file__).resolve().parents[2] / "scripts" / "ffmpeg_dlls",
    ]
    for candidate in candidates:
        if candidate.is_dir() and any(candidate.glob("avcodec-*.dll")):
            try:
                os.add_dll_directory(str(candidate))
            except OSError:  # pragma: no cover - defensive
                pass
            return


def convert_to_wav(source: Path, destination: Path) -> float:
    """Convert any audio/video file to a 16 kHz mono WAV via FFmpeg."""
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        local = Path(__file__).resolve().parents[2] / "ffmpeg7s_build"
        found = list(local.rglob("ffmpeg.exe")) if local.is_dir() else []
        if not found:
            raise VoiceSampleProblem(
                "FFmpeg is unavailable; install it or provide a 16 kHz mono WAV sample."
            )
        ffmpeg = str(found[0])
    destination.parent.mkdir(parents=True, exist_ok=True)
    completed = subprocess.run(
        [ffmpeg, "-y", "-i", str(source), "-ar", "16000", "-ac", "1", str(destination)],
        capture_output=True,
        text=True,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    if completed.returncode:  # pragma: no cover - depends on user file
        raise VoiceSampleProblem(
            "FFmpeg could not read this recording. Provide a WAV, MP3 or M4A file."
        )
    return validate_voice_sample(destination)


class VoiceEngine:
    """Lazy XTTS wrapper: configuration is cheap, speech synthesis is heavy."""

    def __init__(self, config: VoiceConfig | None = None):
        self.config = config or VoiceConfig()
        self._tts = None  # loaded on first speak()

    # ------------------------------------------------------------- readiness

    def is_ready(self) -> bool:
        try:
            validate_voice_sample(self.config.sample_path)
            return True
        except VoiceSampleProblem:
            return False

    def status(self) -> dict:
        ready = self.is_ready()
        return {
            "ready": ready,
            "language": self.config.language,
            "sample_path": str(self.config.sample_path),
        }

    # -------------------------------------------------------------- synthesis

    def _load_model(self):
        if self._tts is not None:
            return self._tts
        try:
            from TTS.api import TTS  # type: ignore
        except ImportError as exc:  # pragma: no cover - depends on env install
            raise RuntimeError(
                "Coqui TTS is not installed. Run: pip install TTS torch torchaudio"
            ) from exc
        self._tts = TTS("tts_models/multilingual/multi-dataset/xtts_v2")
        return self._tts

    def _speak_pyttsx3(self, text: str, output: Path) -> Path:
        try:
            import pyttsx3  # type: ignore
        except ImportError as exc:
            raise RuntimeError(
                "Neither Coqui TTS nor pyttsx3 is installed. Run: pip install pyttsx3"
            ) from exc
        engine = pyttsx3.init()
        engine.save_to_file(text, str(output))
        engine.runAndWait()
        return output

    def speak(self, text: str, *, filename: str | None = None) -> Path:
        """Synthesize `text` in the owner's cloned voice (or fallback); return the WAV path."""
        text = (text or "").strip()
        if not text:
            raise ValueError("Nothing to speak: the reply text is empty.")
        if len(text) > 600:
            raise ValueError("Reply too long for one synthesis; split it into shorter turns.")
        validate_voice_sample(self.config.sample_path)
        self.config.output_dir.mkdir(parents=True, exist_ok=True)
        if not filename:
            import time

            filename = f"reply_{int(time.time() * 1000)}.wav"
        output = self.config.output_dir / filename

        # Try Coqui XTTS first; fall back to lightweight pyttsx3 if XTTS is not installed
        try:
            tts = self._load_model()
            tts.tts_to_file(
                text=text,
                speaker_wav=str(self.config.sample_path),
                language=self.config.language,
                file_path=str(output),
            )
        except (RuntimeError, ImportError):
            self._speak_pyttsx3(text, output)

        # Play sound through system audio output (async so it doesn't block)
        try:
            import winsound
            winsound.PlaySound(str(output), winsound.SND_FILENAME | winsound.SND_ASYNC)
        except Exception:
            pass

        return output
