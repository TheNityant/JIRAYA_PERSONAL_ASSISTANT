import json
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.config import Settings
from app.integrations.github.client import GitHubClient
from app.providers.hermes import HermesClient, HermesProblem
from app.routes import developer_briefing as route
from app.security.identity import account_scope, identity
from app.services.developer_briefing import collect_snapshot


@pytest.fixture
def pilot(monkeypatch):
    for variable in (
        "ALL_PROXY",
        "HTTPS_PROXY",
        "HTTP_PROXY",
        "all_proxy",
        "https_proxy",
        "http_proxy",
    ):
        monkeypatch.delenv(variable, raising=False)
    from app.db import get_db

    settings = Settings(
        _env_file=None,
        hermes_briefing_enabled=True,
        hermes_base_url="https://hermes.example.test/v1",
        hermes_api_key="private-hermes-key",
        hermes_owner_user_id="pilot-user",
        dev_user_id="pilot-user",
    )
    calls = []

    def github_response(request):
        calls.append(request)
        assert request.method == "GET"
        assert request.headers["Authorization"] == "Bearer github-user-token"
        if behavior.get("empty"):
            return httpx.Response(
                200, json={"workflow_runs": []} if "/actions/runs" in request.url.path else []
            )
        if behavior.get("missing_notebook") and "notebook" in request.url.path:
            return httpx.Response(404, text="private-remote-detail")
        common = {
            "id": 10,
            "number": 7,
            "title": "Review dependency update",
            "state": "open",
            "body": "DO-NOT-SEND-ISSUE-BODY",
            "access_token": "DO-NOT-SEND-TOKEN",
            "html_url": "https://evil.test/redirect",
        }
        if "/actions/runs" in request.url.path:
            return httpx.Response(
                200,
                json={
                    "workflow_runs": [
                        {**common, "name": "CI", "conclusion": "failure", "status": "completed"}
                    ]
                },
            )
        return httpx.Response(200, json=[common])

    def github(_db, current):
        assert current.dev_user_id == "pilot-user"
        client = GitHubClient(access_token="github-user-token")
        client.client.close()
        client.client = httpx.Client(
            base_url="https://api.github.com",
            headers={"Authorization": "Bearer github-user-token"},
            transport=httpx.MockTransport(github_response),
        )
        return client

    upstream = []
    behavior = {"tools": [], "finish": "stop", "fail": None}

    def respond(request):
        upstream.append(request)
        assert request.headers["Authorization"] == "Bearer private-hermes-key"
        if behavior["fail"]:
            return httpx.Response(behavior["fail"], text="SECRET-UPSTREAM-BODY")
        if request.url.path == "/v1/toolsets":
            return httpx.Response(200, json=behavior["tools"])
        assert request.url.path == "/v1/chat/completions"
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "finish_reason": behavior["finish"],
                        "message": {
                            "content": "Needs attention: review CI [S3]. Suggested next steps: inspect the linked run."
                        },
                    }
                ]
            },
        )

    monkeypatch.setattr(
        route,
        "HermesClient",
        lambda current: HermesClient(current, transport=httpx.MockTransport(respond)),
    )
    monkeypatch.setattr(route, "get_github_client", github)

    def scoped_settings():
        current = identity.get()
        return settings.model_copy(
            update={"dev_user_id": current[0] if current else settings.dev_user_id}
        )

    monkeypatch.setattr(route, "get_settings", scoped_settings)
    app = FastAPI()
    app.include_router(route.router)
    app.dependency_overrides[get_db] = lambda: None
    with TestClient(app) as client:
        yield SimpleNamespace(
            settings=settings, calls=calls, upstream=upstream, behavior=behavior, client=client
        )


def test_two_repositories_use_read_only_current_account_evidence(pilot):
    result = pilot.client.post(
        "/github/briefing", json={"repositories": ["owner/jiraya", "owner/notebook"]}
    )
    assert result.status_code == 200, result.text
    value = result.json()
    assert value["provider"] == "hermes"
    assert value["repositories"] == ["owner/jiraya", "owner/notebook"]
    assert len(value["sources"]) == 6
    assert len(pilot.calls) == 6
    assert all(source["url"].startswith("https://github.com/owner/") for source in value["sources"])
    request = pilot.upstream[-1]
    payload = json.loads(request.content)
    assert payload["model"] == "hermes-agent"
    assert payload["max_tokens"] == 1000
    assert payload["stream"] is False
    assert request.headers["Idempotency-Key"] == value["run_id"]
    assert "DO-NOT-SEND" not in request.content.decode()
    assert "github-user-token" not in request.content.decode()
    assert "owner/notebook" in request.content.decode()
    assert "private-hermes-key" not in result.text


@pytest.mark.parametrize(
    "repositories",
    [
        [],
        ["owner/a"] * 2,
        ["owner/A", "owner/a"],
        ["../a"],
        ["owner/a", "owner/b", "owner/c", "owner/d"],
    ],
)
def test_invalid_selection_never_calls_external_services(pilot, repositories):
    assert (
        pilot.client.post("/github/briefing", json={"repositories": repositories}).status_code
        == 422
    )
    assert not pilot.calls and not pilot.upstream


def test_request_cannot_choose_identity_or_another_users_profile(pilot):
    assert (
        pilot.client.post(
            "/github/briefing", json={"repositories": ["owner/a"], "user_id": "someone"}
        ).status_code
        == 422
    )
    with account_scope("other-user", "other@example.test"):
        status = pilot.client.get("/github/briefing/status").json()
        assert status["configured"] is False
        assert (
            pilot.client.post("/github/briefing", json={"repositories": ["owner/a"]}).status_code
            == 503
        )
    assert not pilot.upstream and not pilot.calls


@pytest.mark.parametrize(
    "tools", [[{"name": "terminal", "enabled": True}], {"items": []}, [{"name": "unknown"}]]
)
def test_native_tools_must_be_disabled_before_sending_github_data(pilot, tools):
    pilot.behavior["tools"] = tools
    response = pilot.client.post("/github/briefing", json={"repositories": ["owner/a"]})
    assert response.status_code == 502
    assert "toolset" in response.text
    assert not pilot.calls
    assert len(pilot.upstream) == 1


def test_partial_or_failed_hermes_is_not_a_success_and_never_retries(pilot):
    pilot.behavior["finish"] = "length"
    assert (
        pilot.client.post("/github/briefing", json={"repositories": ["owner/a"]}).status_code == 502
    )
    assert sum(request.method == "POST" for request in pilot.upstream) == 1
    pilot.upstream.clear()
    pilot.behavior["fail"] = 401
    response = pilot.client.post("/github/briefing", json={"repositories": ["owner/a"]})
    assert response.status_code == 502
    assert "SECRET-UPSTREAM-BODY" not in response.text
    assert len(pilot.upstream) == 1
    assert not route.briefing_lock.locked()


def test_configuration_status_does_not_disclose_endpoint_or_key(pilot):
    response = pilot.client.get("/github/briefing/status")
    assert response.json()["configured"] is True
    assert "private-hermes-key" not in response.text and "hermes.example.test" not in response.text
    assert not pilot.upstream


def test_overlap_returns_conflict_without_provider_calls(pilot):
    route.briefing_lock.acquire()
    try:
        assert (
            pilot.client.post("/github/briefing", json={"repositories": ["owner/a"]}).status_code
            == 409
        )
        assert not pilot.calls and not pilot.upstream
    finally:
        route.briefing_lock.release()


@pytest.mark.parametrize(
    "url",
    [
        "http://public.example/v1",
        "https://user:secret@hermes.test/v1",
        "https://hermes.test/v1?token=secret",
        "https://hermes.test/not-v1",
    ],
)
def test_reject_insecure_or_ambiguous_operator_endpoint(url):
    settings = Settings(
        _env_file=None,
        hermes_briefing_enabled=True,
        hermes_base_url=url,
        hermes_api_key="key",
        dev_user_id="pilot",
        hermes_owner_user_id="pilot",
    )
    with pytest.raises(HermesProblem):
        HermesClient(settings)


def test_coverage_reports_source_failures_and_does_not_include_prs_as_issues():
    class Github:
        def list_pulls(self, **kwargs):
            return []

        def list_issues(self, **kwargs):
            return [{"id": 1, "number": 1, "pull_request": {}, "body": "not included"}]

        def list_workflow_runs(self, **kwargs):
            raise RuntimeError("secret provider details")

    snapshot = collect_snapshot(Github(), ["owner/repo"])
    assert snapshot["sources"] == []
    assert snapshot["coverage"][1]["loaded"] == 0
    assert "error" in snapshot["coverage"][2]
    assert "secret provider details" not in json.dumps(snapshot)


def test_unauthenticated_briefing_routes_require_login(monkeypatch):
    from app.main import app
    from app.security import sessions

    monkeypatch.setattr(
        sessions, "get_base_settings", lambda: Settings(_env_file=None, auth_required=True)
    )
    with TestClient(app) as client:
        assert client.get("/github/briefing/status").status_code == 401
        assert (
            client.post("/github/briefing", json={"repositories": ["owner/repo"]}).status_code
            == 401
        )


def test_empty_snapshot_does_not_request_inference(pilot):
    pilot.behavior["empty"] = True
    response = pilot.client.post("/github/briefing", json={"repositories": ["owner/empty"]})
    assert response.status_code == 200
    assert response.json()["provider"] == "none"
    assert response.json()["sources"] == []
    assert len(pilot.upstream) == 1  # Profile check only, no model POST.


def test_partial_repository_failure_is_reported_without_leaking_error_bodies(pilot):
    pilot.behavior["missing_notebook"] = True
    response = pilot.client.post(
        "/github/briefing", json={"repositories": ["owner/jiraya", "owner/notebook"]}
    )
    assert response.status_code == 200
    result = response.json()
    assert len(result["sources"]) == 3
    assert sum("error" in source for source in result["coverage"]) == 3
    assert "private-remote-detail" not in response.text
    assert "not accessible" in pilot.upstream[-1].content.decode()


def test_disabled_configuration_never_contacts_github_or_hermes(pilot):
    pilot.settings.hermes_briefing_enabled = False
    assert pilot.client.get("/github/briefing/status").json()["configured"] is False
    assert (
        pilot.client.post("/github/briefing", json={"repositories": ["owner/a"]}).status_code == 503
    )
    assert not pilot.calls and not pilot.upstream


def test_timeout_and_oversized_response_are_bounded_and_redacted():
    settings = Settings(
        _env_file=None,
        hermes_briefing_enabled=True,
        hermes_base_url="http://127.0.0.1:8642/v1",
        hermes_api_key="key",
        dev_user_id="pilot",
        hermes_owner_user_id="pilot",
    )
    attempts = []

    def timeout(request):
        attempts.append(request)
        raise httpx.ReadTimeout("secret details", request=request)

    client = HermesClient(settings, transport=httpx.MockTransport(timeout))
    try:
        with pytest.raises(HermesProblem, match="timed out") as result:
            client.check_read_only_profile()
        assert "secret details" not in str(result.value)
        assert len(attempts) == 1
    finally:
        client.close()
    client = HermesClient(
        settings,
        transport=httpx.MockTransport(lambda request: httpx.Response(200, content=b"x" * 128001)),
    )
    try:
        with pytest.raises(HermesProblem, match="oversized"):
            client.check_read_only_profile()
    finally:
        client.close()
