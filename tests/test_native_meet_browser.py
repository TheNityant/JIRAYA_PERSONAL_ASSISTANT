import asyncio
from types import SimpleNamespace

import pytest

from app.meet_cloud import native_browser as native


@pytest.mark.parametrize("failure", [False, True])
def test_native_launch_uses_direct_headed_chrome_and_cleans_up(monkeypatch, failure):
    processes, commands, envs, stopped, endpoints = [], [], [], [], []

    class Process:
        returncode = None

        def terminate(self):
            self.returncode = 0

        def kill(self):
            self.returncode = -9

        async def wait(self):
            return self.returncode

    async def spawn(*args, **kwargs):
        process = Process()
        processes.append(process)
        commands.append(args)
        envs.append(kwargs.get("env", {}))
        return process

    class Client:
        def __init__(self, **kwargs):
            assert not kwargs["trust_env"]

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def get(self, endpoint):
            endpoints.append(endpoint)
            return SimpleNamespace(status_code=200)

    async def stop():
        stopped.append(True)

    context = object()

    async def attach(endpoint):
        endpoints.append(endpoint)
        if failure:
            raise RuntimeError("fixture connection failure")
        return SimpleNamespace(contexts=[context], close=stop)

    monkeypatch.setattr(native.asyncio, "create_subprocess_exec", spawn)
    monkeypatch.setattr(native.Path, "exists", lambda path: True)
    monkeypatch.setattr(native.httpx, "AsyncClient", Client)
    monkeypatch.setenv("ASSEMBLYAI_API_KEY", "must-not-reach-browser")
    engine = SimpleNamespace(
        stop=stop, chromium=SimpleNamespace(executable_path="/chrome", connect_over_cdp=attach)
    )

    async def run():
        if failure:
            with pytest.raises(RuntimeError):
                await native.launch(engine, "/temporary-profile", "private-audio")
        else:
            handle, result = await native.launch(engine, "/temporary-profile", "private-audio")
            assert result.context is context
            await handle.stop()
            await handle.stop()

    asyncio.run(run())
    assert len(stopped) == 1 and len(processes) == 2
    assert all(p.returncode is not None for p in processes)
    assert commands[0][0] == "Xvfb" and commands[1][0] == "/chrome"
    assert "--headless" not in " ".join(commands[1])
    assert "--use-fake" not in " ".join(commands[1])
    assert envs[1]["PULSE_SINK"] == "private-audio"
    assert "ASSEMBLYAI_API_KEY" not in envs[1]
    assert all(url.startswith("http://127.0.0.1:") for url in endpoints)


def test_native_default_context_closes_owned_browser_once():
    calls = []

    async def close():
        calls.append(True)

    context = native.NativeContext(SimpleNamespace(pages=["page"]), SimpleNamespace(close=close))

    async def run():
        assert context.pages == ["page"]
        await context.close()
        await context.close()

    asyncio.run(run())
    assert calls == [True]


def test_cloud_native_driver_sets_permissions_before_page_navigation(monkeypatch):
    import playwright.async_api

    from app.meet_cloud.browser import CloudJoiner

    grants = []

    async def grant(values, **kwargs):
        grants.append((values, kwargs))

    context = SimpleNamespace(grant_permissions=grant)
    handle = object()

    async def launch(*args):
        return handle, context

    async def start():
        return object()

    monkeypatch.setattr(native, "launch", launch)
    monkeypatch.setattr(
        playwright.async_api, "async_playwright", lambda: SimpleNamespace(start=start)
    )
    joiner = CloudJoiner(
        audio=SimpleNamespace(name="sink"),
        settings=SimpleNamespace(
            meet_cloud_browser_driver="native", meet_cloud_identity_mode="guest"
        ),
        on_turn=None,
        tick=None,
        leave_on_speech=False,
    )
    assert asyncio.run(joiner._launch_context()) == (handle, context)
    assert grants == [(["camera"], {"origin": "https://meet.google.com"})]


@pytest.mark.parametrize("permission", ["denied", "granted", "prompt", "error"])
def test_native_guest_microphone_permission_requires_confirmed_denial(monkeypatch, permission):
    from app.meet_agent.joiner import MeetJoiner
    from app.meet_cloud.browser import CloudJoiner

    async def visible():
        return False

    async def evaluate(_):
        if permission == "error":
            raise RuntimeError("permission query failed")
        return permission

    async def fallback(*args):
        return False

    monkeypatch.setattr(MeetJoiner, "_toggle_media_off", fallback)
    page = SimpleNamespace(
        url="https://meet.google.com/meet/abc-defg-hij",
        locator=lambda _: SimpleNamespace(first=SimpleNamespace(is_visible=visible)),
        is_closed=lambda: False,
        evaluate=evaluate,
    )
    evidence = SimpleNamespace(notes=[])
    joiner = CloudJoiner(
        audio=object(),
        settings=SimpleNamespace(meet_cloud_browser_driver="native", meet_cloud_identity_mode="guest"),
        on_turn=None, tick=None, leave_on_speech=False,
    )
    result = asyncio.run(joiner._toggle_media_off(page, (), "mic_muted", evidence))
    assert result is (permission == "denied")
    assert ("mic_muted_confirmed_off" in evidence.notes) is (permission == "denied")


def test_cleanup_reaps_process_that_exits_during_termination():
    waited = []

    class Process:
        returncode = None

        def terminate(self):
            raise ProcessLookupError()

        async def wait(self):
            waited.append(True)
            return 0

    async def stop():
        pass

    handle = native.NativeHandle(SimpleNamespace(stop=stop))
    handle.processes = [Process()]
    asyncio.run(handle.stop())
    assert waited == [True]
