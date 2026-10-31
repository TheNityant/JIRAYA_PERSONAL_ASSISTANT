"""Exercise the HTTP contract used by repository actions and PR watchers."""
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from test_github_integration import GitHub, rule

from app.config import Settings
from app.db import Base
from app.integrations.github.client import GitHubClient
from app.models import Notification
from app.routes import github as routes
from app.runtime.github_runtime import GitHubRuntimeService


@pytest.fixture
def db():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session
    engine.dispose()


class BranchClient:
    def __init__(self, *, source_error=None, write_error=None):
        self.calls = []
        self.source_error = source_error
        self.write_error = write_error
        self.closed = False

    def get_branch_ref(self, **kwargs):
        self.calls.append(("source", kwargs))
        if self.source_error:
            raise self.source_error
        return {"object": {"sha": "source-commit"}}

    def create_branch_at_sha(self, **kwargs):
        self.calls.append(("write", kwargs))
        if self.write_error:
            raise self.write_error
        return {"ref": f"refs/heads/{kwargs['branch']}", "object": {"sha": kwargs["sha"]}}

    def close(self):
        self.closed = True


def provider_error(status, payload=None, headers=None):
    request = httpx.Request("GET", "https://api.github.com/repos/owner/repo")
    response = httpx.Response(status, request=request, json=payload or {}, headers=headers)
    return httpx.HTTPStatusError("Do not leak raw provider data or test-token", request=request, response=response)


def setup_routes(monkeypatch, github):
    monkeypatch.setattr(routes, "get_settings", lambda: Settings(enable_external_actions=True))
    monkeypatch.setattr(routes, "get_github_client", lambda *a: github)


def test_branch_request_rejects_spaces_before_provider_access(monkeypatch):
    def unexpected(*args):
        pytest.fail("Request validation must happen before accessing GitHub")
    monkeypatch.setattr(routes, "get_github_client", unexpected)
    with TestClient(routes_app()) as client:
        response = client.post("/github/repos/owner/repo/branches", json={"new_branch": "Pusha assistant branch", "from_ref": "main"})
    assert response.status_code == 422
    issue = response.json()["detail"][0]
    assert issue["loc"] == ["body", "new_branch"]
    assert "cannot contain spaces" in issue["msg"]


def routes_app():
    from app.main import app
    return app


def test_branch_request_creates_from_selected_source_commit_and_closes_client(monkeypatch):
    github = BranchClient()
    setup_routes(monkeypatch, github)
    with TestClient(routes_app()) as client:
        response = client.post("/github/repos/owner/repo/branches", json={"new_branch": "feature/follow-up", "from_ref": "phase2e-generic-execution"})
    assert response.status_code == 201
    assert response.json()["ref"] == "refs/heads/feature/follow-up"
    assert github.calls == [("source", {"owner": "owner", "repo": "repo", "branch": "phase2e-generic-execution"}), ("write", {"owner": "owner", "repo": "repo", "branch": "feature/follow-up", "sha": "source-commit"})]
    assert github.closed


@pytest.mark.parametrize("stage,error,expected,text", [
    ("source", provider_error(404), 404, "Source branch not found"),
    ("write", provider_error(422, {"message": "Reference already exists"}), 409, "already exists"),
    ("write", provider_error(403), 403, "permissions"),
    ("source", provider_error(403, headers={"x-ratelimit-remaining": "0"}), 429, "rate limit"),
    ("source", provider_error(401), 409, "Reconnect GitHub"),
])
def test_branch_provider_failures_are_actionable_and_safe(monkeypatch, stage, error, expected, text):
    github = BranchClient(**{f"{'source' if stage == 'source' else 'write'}_error": error})
    setup_routes(monkeypatch, github)
    with TestClient(routes_app()) as client:
        response = client.post("/github/repos/owner/repo/branches", json={"new_branch": "feature/follow-up", "from_ref": "main"})
    assert response.status_code == expected
    assert text in response.json()["detail"]
    assert "test-token" not in response.text
    assert github.closed
    if stage == "source":
        assert len(github.calls) == 1


def test_source_branch_picker_returns_existing_branches(monkeypatch):
    github = SimpleNamespace(list_branches=lambda **kw: [{"name": "main"}, {"name": "release/v1"}], close=lambda: None)
    setup_routes(monkeypatch, github)
    with TestClient(routes_app()) as client:
        response = client.get("/github/repos/owner/repo/branches")
    assert response.status_code == 200
    assert response.json()["items"][1]["name"] == "release/v1"


def test_branch_picker_paginates(monkeypatch):
    github = GitHubClient.__new__(GitHubClient)
    pages = []
    def handler(request):
        pages.append(request.url.params["page"])
        return httpx.Response(200, json=[{"name": f"branch-{i}"} for i in range(100)] if len(pages) == 1 else [{"name": "last"}])
    github.client = httpx.Client(base_url="https://api.github.com", transport=httpx.MockTransport(handler))
    assert len(github.list_branches(owner="owner", repo="repo")) == 101
    assert pages == ["1", "2"]
    github.close()


def test_pr_watcher_baselines_then_detects_head_change_without_timestamp_change(db, monkeypatch):
    watcher = rule(db, trigger="GITHUB_PR_CHANGED")
    rows = [{"id": 42, "number": 3, "title": "Review", "state": "open", "updated_at": "same-time", "head": {"sha": "first"}}]
    github = GitHub()
    github.list_pulls = lambda **kwargs: rows
    monkeypatch.setattr("app.runtime.github_runtime.get_github_client", lambda *a: github)
    monkeypatch.setattr("app.runtime.github_runtime.ConnectionStore.get_active", lambda *a, **kw: SimpleNamespace(id="connection"))
    runtime = GitHubRuntimeService(db=db, settings=Settings())
    assert runtime.poll()["baselines_initialized"] == 1
    assert not db.scalars(select(Notification)).all()
    rows[0]["head"]["sha"] = "second"
    assert runtime.poll()["processed"] == 1
    assert runtime.poll()["processed"] == 0
    assert len(db.scalars(select(Notification)).all()) == 1
    assert db.scalars(select(Notification)).one().automation_id == watcher.id


def test_pr_watcher_reports_repository_access_failure(db, monkeypatch):
    watcher = rule(db, trigger="GITHUB_PR_CHANGED")
    github = GitHub()
    def inaccessible(**kwargs):
        raise provider_error(404)
    github.list_pulls = inaccessible
    monkeypatch.setattr("app.runtime.github_runtime.get_github_client", lambda *a: github)
    monkeypatch.setattr("app.runtime.github_runtime.ConnectionStore.get_active", lambda *a, **kw: SimpleNamespace(id="connection"))
    result = GitHubRuntimeService(db=db, settings=Settings()).poll()
    error = result["errors"][0]
    assert error["automation_id"] == watcher.id
    assert error["repository"] == "owner/repo"
    assert error["stage"] == "github_read"
    assert error["status_code"] == 404
    assert "not accessible" in error["detail"]
    assert "test-token" not in str(result)
    assert github.close_calls == 1


def test_manual_sync_updates_visible_health_with_diagnostic(monkeypatch):
    result = {"decision": "PARTIAL_ERROR", "processed": 0, "errors": [{"detail": "Repository not found or not accessible to the connected GitHub account."}]}
    monkeypatch.setattr(GitHubRuntimeService, "poll", lambda self: result)
    with TestClient(routes_app()) as client:
        assert client.post("/runtime/github/poll").json() == result
        monitor = client.get("/github/status").json()["monitor"]
    assert monitor["last_status"] == "ERROR"
    assert monitor["last_error"] == result["errors"][0]["detail"]


def test_provider_auth_failure_does_not_sign_user_out(monkeypatch):
    def expired(**kwargs):
        raise provider_error(401)
    github = SimpleNamespace(list_pulls=expired, close=lambda: None)
    setup_routes(monkeypatch, github)
    with TestClient(routes_app()) as client:
        response = client.get("/github/repos/owner/repo/pulls")
    assert response.status_code == 409  # 401 belongs to Jiraya's own login, not OAuth.
    assert "Reconnect GitHub" in response.json()["detail"]


def test_pr_hash_upgrade_starts_baseline_without_replaying_old_prs(db, monkeypatch):
    import hashlib
    import json

    from app.services.runtime_state_store import RuntimeStateStore

    watcher = rule(db, trigger="GITHUB_PR_CHANGED")
    identity = hashlib.sha256(json.dumps(["connection", watcher.id, watcher.trigger_type, watcher.action_config, watcher.match_config], sort_keys=True).encode()).hexdigest()
    RuntimeStateStore(db).set(f"github-snapshot:{identity}", {"versions": {"42": "old-hash-schema"}})
    github = GitHub()
    github.list_pulls = lambda **kwargs: [{"id": 42, "title": "Existing PR", "head": {"sha": "old-commit"}}]
    monkeypatch.setattr("app.runtime.github_runtime.get_github_client", lambda *a: github)
    monkeypatch.setattr("app.runtime.github_runtime.ConnectionStore.get_active", lambda *a, **kw: SimpleNamespace(id="connection"))
    runtime = GitHubRuntimeService(db=db, settings=Settings())
    assert runtime.poll()["baselines_initialized"] == 1
    assert runtime.poll()["processed"] == 0
    assert not db.scalars(select(Notification)).all()


def test_two_repository_watchers_keep_separate_baselines_and_notifications(db, monkeypatch):
    first = rule(db, trigger="GITHUB_PR_CHANGED")
    second = rule(db, trigger="GITHUB_PR_CHANGED")
    second.action_config = {**second.action_config, "repository": "owner/notebook"}
    db.commit()
    rows = {
        "repo": [{"id": 42, "number": 3, "title": "Jiraya PR", "state": "open", "head": {"sha": "first"}}],
        "notebook": [{"id": 42, "number": 3, "title": "Notebook PR", "state": "open", "head": {"sha": "first"}}],
    }
    github = GitHub()
    github.list_pulls = lambda **kwargs: rows[kwargs["repo"]]
    monkeypatch.setattr("app.runtime.github_runtime.get_github_client", lambda *args: github)
    monkeypatch.setattr("app.runtime.github_runtime.ConnectionStore.get_active", lambda *args, **kwargs: SimpleNamespace(id="connection"))
    runtime = GitHubRuntimeService(db=db, settings=Settings())
    assert runtime.poll()["baselines_initialized"] == 2
    assert not db.scalars(select(Notification)).all()
    rows["repo"][0]["title"] = "Jiraya PR changed"
    assert runtime.poll()["processed"] == 1
    notification = db.scalars(select(Notification)).one()
    assert notification.automation_id == first.id
    assert "owner/repo" in notification.message
    rows["notebook"][0]["title"] = "Notebook PR changed"
    assert runtime.poll()["processed"] == 1
    notifications = db.scalars(select(Notification)).all()
    assert len(notifications) == 2
    assert {item.automation_id for item in notifications} == {first.id, second.id}
    assert any("owner/notebook" in item.message for item in notifications)
    assert runtime.poll()["processed"] == 0
    assert len(db.scalars(select(Notification)).all()) == 2
