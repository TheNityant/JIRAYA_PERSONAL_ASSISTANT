"""Real voice clone test using the owner's actual recorded sample."""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import ffmpeg_dlls  # noqa: F401  (torchcodec DLL bootstrap, Windows)

sys.path.insert(0, str(Path(__file__).parents[1]))

from app.meet_agent.voice_engine import VoiceConfig, VoiceEngine  # noqa: E402

engine = VoiceEngine(
    VoiceConfig(sample_path=Path("runtime/owner_voice_sample.wav"))
)
print("voice ready:", engine.is_ready(), flush=True)

audio = engine.speak(
    "Hello everyone, this is Jay speaking through my cloned voice. "
    "The build is green and deployment is on track.",
    filename="clone_test.wav",
)
print("synthesized:", audio)
print("size:", Path(audio).stat().st_size, "bytes")
print("VOICE_CLONE_TEST_PASSED")
