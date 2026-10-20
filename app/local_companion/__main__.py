import argparse
import json
import os
import time
from pathlib import Path
from urllib.request import Request, urlopen
from uuid import uuid4

from app.local_companion.server import Companion, create_companion


def config_directory() -> Path:
    base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / ".local" / "share")
    return base / "Jiraya" / "companion"


def main():
    parser = argparse.ArgumentParser(description="Jiraya local Git workspace companion")
    parser.add_argument("command", choices=["serve", "watch"], nargs="?", default="serve")
    parser.add_argument("folders", nargs="*")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument(
        "--origin", action="append", help="Allowed frontend origin; repeat for more than one"
    )
    parser.add_argument("--config-dir", type=Path, default=config_directory())
    args = parser.parse_args()
    if args.command == "serve":
        import uvicorn

        origins = args.origin or ["http://localhost:3000", "http://127.0.0.1:3000"]
        app = create_companion(args.config_dir, origins)
        print(
            f"Pairing token (enter in Jiraya and your IDE): {app.state.companion.config['token']}",
            flush=True,
        )
        print(
            f"Local service: http://127.0.0.1:{args.port}. Allowed frontend origins: {', '.join(origins)}",
            flush=True,
        )
        uvicorn.run(app, host="127.0.0.1", port=args.port)
        return
    if not args.folders:
        parser.error("watch requires one or more project folders")
    token = Companion(args.config_dir).config["token"]
    session_id = str(uuid4())
    base = f"http://127.0.0.1:{args.port}"
    folders = [str(Path(folder).resolve()) for folder in args.folders]

    def call(path, method, payload=None):
        data = json.dumps(payload).encode() if payload is not None else None
        req = Request(
            base + path,
            method=method,
            data=data,
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        )
        with urlopen(req, timeout=10) as response:
            return response.read()

    print(
        "Projects are open for checkpoints while this terminal command runs. Save editor buffers before checkpointing."
    )
    try:
        while True:
            call("/workspaces/heartbeat", "POST", {"session_id": session_id, "folders": folders})
            time.sleep(15)
    except KeyboardInterrupt:
        call(f"/workspaces/{session_id}", "DELETE")


if __name__ == "__main__":
    main()
