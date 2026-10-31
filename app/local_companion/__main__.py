import argparse
import json
import os
import socket
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
    if not 1024 <= args.port <= 65535:
        parser.error("Use a local port from 1024 to 65535")
    if args.command == "serve":
        import uvicorn

        origins = args.origin or ["http://localhost:3000", "http://127.0.0.1:3000"]
        # Reserve the actual socket before creating state or printing a token.
        # Passing that same socket to Uvicorn avoids a check-then-bind race.
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
            if os.name == "nt":
                listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            else:
                listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                listener.bind(("127.0.0.1", args.port))
            except OSError:
                parser.exit(
                    1,
                    f"Cannot start companion on 127.0.0.1:{args.port}: the port is occupied or unavailable.\n"
                    "Keep using the existing companion, or stop it in its original terminal with Ctrl+C and restart.\n"
                    "To identify the listener in PowerShell: Get-NetTCPConnection -LocalPort "
                    f"{args.port} -State Listen | Select-Object OwningProcess\n"
                    "No new companion was started. Do not stop an unidentified process.\n",
                )
            listener.listen(128)
            listener.setblocking(False)
            app = create_companion(args.config_dir, origins)
            # Retrieve token from Companion singleton
            token = Companion.instance().config["token"]
            print(
                f"Pairing token (enter in Jiraya and your IDE): {token}",
                flush=True,
            )
            print(
                f"Local service: http://127.0.0.1:{args.port}. Allowed frontend origins: {', '.join(origins)}",
                flush=True,
            )
            uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=args.port)).run(
                sockets=[listener]
            )
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
