from fastapi.testclient import TestClient

from app.main import app


def test_connection_catalog_exposes_gmail_and_future_connectors():
    with TestClient(app) as client:
        response = client.get("/connections")

    assert response.status_code == 200
    payload = response.json()
    assert payload["mode"] == "single-user-development"

    catalog = {item["id"]: item for item in payload["catalog"]}
    assert catalog["gmail"]["status"] in {"AVAILABLE", "CONNECTED"}
    assert catalog["gmail"]["connect_path"] == "/connections/google/start"
    assert catalog["google_calendar"]["status"] == "COMING_NEXT"
    assert catalog["github"]["status"] == "COMING_NEXT"
