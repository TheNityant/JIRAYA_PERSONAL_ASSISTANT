from fastapi.testclient import TestClient

from app.main import app


def test_automation_capabilities_expose_generic_gmail_matchers():
    with TestClient(app) as client:
        response = client.get("/automation-capabilities")
    assert response.status_code == 200
    payload = response.json()
    matcher_ids = {item["id"] for item in payload["matchers"]}
    assert "semantic_condition" in matcher_ids
    assert "subject_contains" in matcher_ids
    assert payload["system_nodes"] == ["VERIFIER", "RECONCILIATOR", "OBLIGATION"]


def test_create_user_defined_automation_via_api():
    request = {
        "name": "Recruiter interview watch",
        "description": "User-authored automation, not a hardcoded workflow",
        "enabled": True,
        "source": "gmail",
        "trigger_type": "NEW_EMAIL",
        "match_config": {
            "semantic_condition": "An employer asks me to schedule an interview.",
            "semantic_threshold": 0.8,
        },
        "context_config": {"instructions": "Prefer Friday afternoons."},
        "action_config": {"type": "DRAFT_REPLY", "instructions": "Draft a concise reply."},
        "approval_policy": "REQUIRED",
    }
    with TestClient(app) as client:
        created = client.post("/automations", json=request)
        assert created.status_code == 201
        automation_id = created.json()["id"]
        listed = client.get("/automations")

    assert listed.status_code == 200
    match = next(item for item in listed.json() if item["id"] == automation_id)
    assert match["name"] == "Recruiter interview watch"
    assert match["match_config"]["semantic_condition"].startswith("An employer")
