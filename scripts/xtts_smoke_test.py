
"""One-time XTTS v2 smoke test: load model, synthesize with a stub reference."""

import os
import sys

# Non-commercial CPML license must be accepted non-interactively.
os.environ.setdefault("COQUI_TOS_AGREED", "1")

# torchcodec needs FFmpeg DLLs registered before it loads (Windows).
sys.path.insert(0, str(__import__("pathlib").Path(__file__).parent))
import ffmpeg_dlls  # noqa: F401,E402

import tempfile
import time
import wave
from pathlib import Path

from TTS.api import TTS

t0 = time.time()
print("Loading XTTS v2 (first run downloads ~1.8 GB)...", flush=True)
tts = TTS("tts_models/multilingual/multi-dataset/xtts_v2", progress_bar=False)
print(f"Model loaded in {time.time() - t0:.1f}s", flush=True)

tmp = Path(tempfile.mkdtemp())
ref = tmp / "ref.wav"
with wave.open(str(ref), "wb") as h:
    h.setnchannels(1)
    h.setsampwidth(2)
    h.setframerate(16000)
    h.writeframes(b"\x00\x02" * int(16000 * 25))  # 25s silent stub reference

t = time.time()
tts.tts_to_file(
    text="Hello, this is a voice clone test.",
    speaker_wav=str(ref),
    language="en",
    file_path=str(tmp / "out.wav"),
)
size = (tmp / "out.wav").stat().st_size
print(f"Synthesis OK in {time.time() - t:.1f}s, output {size} bytes at {tmp / 'out.wav'}")
print("XTTS_V2_SMOKE_TEST_PASSED")
