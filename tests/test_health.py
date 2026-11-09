from fastapi.testclient import TestClient

from app.main import app


def test_health():
    with TestClient(app) as client:
        response = client.get("/health")
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["external_actions_enabled"] is False


def test_root():
    with TestClient(app) as client:
        response = client.get("/")
    assert response.status_code == 200
    payload = response.json()
    assert payload["phase"] == "phase-2e"
    assert payload["version"] == "0.5.0"


def test_public_head_probes_with_authentication_enabled(monkeypatch):
    from app.config import get_settings

    monkeypatch.setenv("AUTH_REQUIRED", "true")
    get_settings.cache_clear()
    try:
        with TestClient(app) as client:
            for path in ("/", "/health"):
                response = client.head(path)
                assert response.status_code == 200
                assert response.content == b""
            assert client.get("/health").json()["status"] == "ok"
            assert client.post("/health", headers={"Origin": "http://localhost:3000"}).status_code == 405
            assert client.get("/health/db").status_code == 401
    finally:
        get_settings.cache_clear()
