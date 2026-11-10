"""Direct headed Chromium with a private CDP control connection; no sign-in automation."""

import asyncio
import os
import socket
from pathlib import Path

import httpx


class NativeContext:
    """A native default context closes by closing its owned browser, not disposing a context."""

    def __init__(self, context, browser):
        self.context, self.browser = context, browser
        self.closed = False

    def __getattr__(self, name):
        return getattr(self.context, name)

    async def close(self):
        if not self.closed:
            self.closed = True
            await self.browser.close()


class NativeHandle:
    def __init__(self, engine):
        self.engine = engine
        self.processes = []
        self.stopped = False

    async def stop(self):
        if self.stopped:
            return
        self.stopped = True
        try:
            await self.engine.stop()
        finally:
            for process in reversed(self.processes):
                if process.returncode is None:
                    process.terminate()
                    try:
                        await asyncio.wait_for(process.wait(), 5)
                    except TimeoutError:
                        process.kill()
                        await process.wait()


async def launch(engine, profile_dir, audio_name):
    handle = NativeHandle(engine)
    try:
        display = await asyncio.create_subprocess_exec(
            "Xvfb",
            ":97",
            "-screen",
            "0",
            "1280x800x24",
            "-nolisten",
            "tcp",
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        handle.processes.append(display)
        for _ in range(50):
            if display.returncode is not None:
                raise RuntimeError("Native browser display stopped")
            if Path("/tmp/.X11-unix/X97").exists():
                break
            await asyncio.sleep(0.1)
        else:
            raise RuntimeError("Native browser display unavailable")
        env = {
            k: v
            for k, v in os.environ.items()
            if k in {"PATH", "HOME", "LANG", "LD_LIBRARY_PATH", "XDG_RUNTIME_DIR", "PULSE_SERVER"}
        }
        env.update(DISPLAY=":97", PULSE_SINK=audio_name)
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        endpoint = f"http://127.0.0.1:{port}"
        chrome = await asyncio.create_subprocess_exec(
            engine.chromium.executable_path,
            "--no-sandbox",
            "--user-data-dir=" + profile_dir,
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-sync",
            "--lang=en-US",
            "--start-maximized",
            "--remote-debugging-address=127.0.0.1",
            f"--remote-debugging-port={port}",
            "--autoplay-policy=no-user-gesture-required",
            "about:blank",
            env=env,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        handle.processes.append(chrome)
        async with httpx.AsyncClient(trust_env=False, timeout=1) as client:
            for _ in range(100):
                if chrome.returncode is not None:
                    raise RuntimeError("Native browser stopped during startup")
                try:
                    response = await client.get(endpoint + "/json/version")
                    if response.status_code == 200:
                        break
                except httpx.HTTPError:
                    pass
                await asyncio.sleep(0.1)
            else:
                raise RuntimeError("Native browser control connection unavailable")
        browser = await engine.chromium.connect_over_cdp(endpoint)
        return handle, NativeContext(browser.contexts[0], browser)
    except BaseException:
        await handle.stop()
        raise
