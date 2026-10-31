"""Microphone -> AssemblyAI STT bridge for the live meeting test.

Captures your microphone (16 kHz mono 16-bit PCM), base64-encodes each chunk
and POSTs it to the companion's /meeting/stt/feed endpoint. Start STT first
(POST /meeting/stt/start), then run this while talking.

Usage: python scripts/mic_bridge.py [--token TOKEN] [--port 8765] [--seconds 60]
Install: python -m pip install sounddevice numpy requests
"""

import argparse
import base64
import sys
from urllib.request import Request, urlopen

import numpy as np
import sounddevice as sd

SAMPLE_RATE = 16000
BLOCK = 1600  # 100 ms of mono s16le


def post(base: str, token: str, path: str, payload: dict) -> dict:
    import json

    req = Request(
        base + path,
        method="POST",
        data=json.dumps(payload).encode(),
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )
    with urlopen(req, timeout=10) as response:
        return json.loads(response.read())


def main() -> None:
    parser = argparse.ArgumentParser(description="Stream mic audio into the meeting STT.")
    parser.add_argument("--token", required=True, help="Companion pairing token")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--seconds", type=float, default=120, help="Capture duration")
    parser.add_argument("--device", type=int, default=None, help="Input device index")
    args = parser.parse_args()

    base = f"http://127.0.0.1:{args.port}"

    import json

    req = Request(base + "/meeting/status", headers={"Authorization": f"Bearer {args.token}"})
    with urlopen(req, timeout=10) as response:
        st = json.loads(response.read())

    stt = st.get("stt") or {}
    if not stt.get("running"):
        sys.exit("STT is not running. First: POST /meeting/stt/start with your token.")
    print(f"[bridge] STT running, streaming mic for {args.seconds:.0f}s. Speak now!")

    frames_sent = 0

    def audio_callback(indata, frames, time_info, _status):  # noqa: ARG001
        nonlocal frames_sent
        pcm = (np.clip(indata[:, 0], -1.0, 1.0) * 32767).astype("<i2").tobytes()
        post(base, args.token, "/meeting/stt/feed", {"data": base64.b64encode(pcm).decode()})
        frames_sent += 1

    with sd.InputStream(
        samplerate=SAMPLE_RATE, channels=1, dtype="float32",
        blocksize=BLOCK, callback=audio_callback, device=args.device,
    ):
        sd.sleep(int(args.seconds * 1000))

    print(f"[bridge] sent {frames_sent} frames. Check /meeting/transcript for turns.")


if __name__ == "__main__":
    main()
