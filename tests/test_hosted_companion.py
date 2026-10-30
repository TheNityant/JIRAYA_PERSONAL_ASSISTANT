import secrets
import time

from fastapi.testclient import TestClient

from app.local_companion.server import create_companion

ORIGIN = "https://jiraya-personal-assistant.vercel.app"


def test_editor_approves_an_exact_origin_and_browser_claim_is_single_use(tmp_path):
    app = create_companion(tmp_path, ["http://localhost:3000"])
    token = app.state.companion.config["token"]
    nonce = secrets.token_hex(32)
    with TestClient(app) as client:
        assert (
            client.post("/pairing/requests", json={"nonce": nonce, "origin": ORIGIN}).status_code
            == 401
        )
        assert (
            client.post(
                "/pairing/requests",
                headers={"Authorization": f"Bearer {token}"},
                json={"nonce": nonce, "origin": ORIGIN},
            ).status_code
            == 200
        )
        assert (
            client.post(
                "/pairing/claim", headers={"Origin": "https://other.example"}, json={"nonce": nonce}
            ).status_code
            == 403
        )
        assert client.post("/pairing/claim", json={"nonce": nonce}).status_code == 403
        preflight = client.options(
            "/pairing/claim",
            headers={
                "Origin": ORIGIN,
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "content-type",
            },
        )
        assert preflight.status_code == 200
        assert preflight.headers["access-control-allow-origin"] == ORIGIN
        claimed = client.post("/pairing/claim", headers={"Origin": ORIGIN}, json={"nonce": nonce})
        assert claimed.json()["token"] == token
        assert claimed.headers["access-control-allow-origin"] == ORIGIN
        assert (
            client.post(
                "/pairing/claim", headers={"Origin": ORIGIN}, json={"nonce": nonce}
            ).status_code
            == 404
        )


def test_expired_pairing_is_rejected_and_device_settings_survive_restart(tmp_path):
    app = create_companion(tmp_path, [ORIGIN])
    state = app.state.companion
    nonce = secrets.token_hex(32)
    state.config["projects"]["repo"] = {"enabled": True, "branch": "feature/test"}
    state.save()
    with TestClient(app) as client:
        client.post(
            "/pairing/requests",
            headers={"Authorization": f"Bearer {state.config['token']}"},
            json={"nonce": nonce, "origin": "https://custom.example"},
        )
        state.pairings[nonce]["until"] = time.monotonic() - 1
        assert (
            client.post(
                "/pairing/claim",
                headers={"Origin": "https://custom.example"},
                json={"nonce": nonce},
            ).status_code
            == 404
        )
    restarted = create_companion(tmp_path, [ORIGIN]).state.companion
    assert restarted.config["token"] == state.config["token"]
    assert restarted.config["projects"]["repo"]["enabled"] is True
    assert "https://custom.example" in restarted.config["origins"]
    assert not restarted.pairings


def test_pairing_cannot_approve_insecure_or_path_based_origins(tmp_path):
    app = create_companion(tmp_path, [ORIGIN])
    headers = {"Authorization": f"Bearer {app.state.companion.config['token']}"}
    with TestClient(app) as client:
        for origin in (
            "http://untrusted.example",
            "https://custom.example/path",
            "https://user:password@custom.example",
            "https://custom.example?query=1",
        ):
            assert (
                client.post(
                    "/pairing/requests",
                    headers=headers,
                    json={"nonce": secrets.token_hex(32), "origin": origin},
                ).status_code
                == 422
            )
