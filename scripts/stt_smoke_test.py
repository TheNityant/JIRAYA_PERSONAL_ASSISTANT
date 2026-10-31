"""Live smoke test: AssemblyAI streaming STT -> MeetingManager pipeline.

Generates a spoken phrase via Windows SAPI, streams the PCM over a real
AssemblyAI WebSocket, and prints each transcript turn plus the assistant
action. Run from the project root: python scripts/stt_smoke_test.py
"""

import io
import queue
import sys
import threading
import time
import wave

sys.path.insert(0, ".")

from app.config import get_settings
from app.meet_agent.speech_to_text import AssemblyAIStream

settings = get_settings()
if not settings.assemblyai_api_key:
    sys.exit("ASSEMBLYAI_API_KEY missing in .env")
print(f"[cfg] key loaded ({settings.assemblyai_api_key[:4]}..., rate={settings.assemblyai_sample_rate})")

# --- 1. Make a spoken WAV with Windows TTS (real speech to transcribe) ------
import subprocess

tts_wav = "runtime_smoke_tts.wav"
ps_script = (
    "Add-Type -AssemblyName System.Speech; "
    "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
    "$s.SetOutputToWaveFile('runtime_smoke_tts.wav'); "
    "$s.Speak('Hey Jay, how is the hackathon going today'); "
    "$s.Dispose()"
)
subprocess.run(["powershell", "-Command", ps_script], check=True, capture_output=True)

with wave.open(tts_wav, "rb") as w:
    in_rate, n_frames, n_ch, sw = w.getframerate(), w.getnframes(), w.getnchannels(), w.getsampwidth()
    pcm = w.readframes(n_frames)
print(f"[audio] {n_frames/in_rate:.1f}s spoken audio ({in_rate}Hz {n_ch}ch {sw*8}bit)")

# Resample to 16kHz mono s16le via ffmpeg if needed
if in_rate != 16000 or n_ch != 1 or sw != 2:
    import subprocess as sp
    from pathlib import Path

    local = Path("ffmpeg7s_build")
    found = sorted(local.rglob("ffmpeg.exe")) if local.is_dir() else []
    ffmpeg = str(found[0]) if found else None
    if ffmpeg is None:
        sys.exit("ffmpeg.exe not found for resampling")
    out = "runtime_smoke_16k.wav"
    sp.run([ffmpeg, "-y", "-i", tts_wav, "-ar", "16000", "-ac", "1", out],
           check=True, capture_output=True)
    with wave.open(out, "rb") as w:
        pcm = w.readframes(w.getnframes())
print(f"[audio] converted to 16kHz mono s16le, {len(pcm)} bytes")

# --- 2. Connect and stream ---------------------------------------------------
turns = queue.Queue()
actions = []

def on_turn(turn):
    print(f"[stt ] finalized turn: {turn.text!r}")
    turns.put(turn.text)

stream = AssemblyAIStream(on_turn)
print("[stt ] connecting WebSocket...")
stream.start()
print("[stt ] connected, streaming audio...")

FRAME = 3200  # 100ms of 16kHz s16le
for i in range(0, len(pcm), FRAME):
    stream.feed(pcm[i : i + FRAME])
    time.sleep(0.1)  # real-time-ish pacing so end_of_turn detection works

# give the API a few seconds to finalize turns
for _ in range(60):
    if not turns.empty():
        break
    time.sleep(0.5)
stream.stop()

final_text = ""
while not turns.empty():
    final_text += turns.get() + " "

print(f"[stt ] final transcript: {final_text.strip()!r}")
if not final_text.strip():
    print("[stt ] STATUS: FAIL - no finalized turn received")
    sys.exit(1)

# --- 3. Push the transcript through the meeting pipeline ---------------------
from app.meet_agent.manager import MeetingManager

manager = MeetingManager()
result = manager.ingest(final_text.strip(), "tester")
print(f"[mgr ] pipeline action: kind={result['kind']} reason={result['trigger_reason']!r}")
reply = result.get("reply_text") or ""
print(f"[mgr ] reply_text: {reply!r}")
status = manager.status()
print(f"[mgr ] transcript_turns={status['transcript_turns']} spoken={status['spoken_replies']}")

ok = final_text.strip() and "hackathon" in final_text.lower()
print("SMOKE TEST:", "PASS" if ok else "PARTIAL (transcript ok, keyword mismatch)")
