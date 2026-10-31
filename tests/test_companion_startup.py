import json
import socket
import subprocess
import sys
import time
from urllib.error import URLError
from urllib.request import ProxyHandler, Request, build_opener


def test_occupied_port_exits_without_creating_state_or_printing_a_token(tmp_path):
    directory = tmp_path / "never-created"
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        port = listener.getsockname()[1]
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "app.local_companion",
                "--port",
                str(port),
                "--config-dir",
                str(directory),
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=15,
        )
        assert result.returncode == 1
        assert "occupied or unavailable" in result.stderr
        assert "No new companion was started" in result.stderr
        assert "Pairing token" not in result.stdout
        assert not directory.exists()
        assert listener.getsockname()[1] == port


def test_reserved_socket_serves_authenticated_health_and_releases_on_exit(tmp_path):
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "app.local_companion",
            "--port",
            str(port),
            "--config-dir",
            str(tmp_path),
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    opener = build_opener(ProxyHandler({}))
    try:
        deadline = time.monotonic() + 15
        while True:
            try:
                token = json.loads((tmp_path / "companion.json").read_text())["token"]
                with opener.open(
                    Request(
                        f"http://127.0.0.1:{port}/health",
                        headers={"Authorization": f"Bearer {token}"},
                    ),
                    timeout=1,
                ) as response:
                    assert json.load(response)["service"] == "jiraya-local-companion"
                    break
            except (FileNotFoundError, URLError, TimeoutError):
                assert process.poll() is None
                assert time.monotonic() < deadline
                time.sleep(0.05)
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
