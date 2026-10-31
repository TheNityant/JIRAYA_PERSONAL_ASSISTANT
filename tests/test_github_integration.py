import json
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.automation.executor import AutomationExecutor
from app.config import Settings
from app.db import Base
from app.integrations.github.client import GitHubClient
from app.integrations.github.validation import repository_parts, validate_branch
from app.main import app
from app.models import AutomationExecution, Notification
from app.runtime.github_runtime import GitHubRuntimeService
from app.services.automation_store import AutomationStore


@pytest.fixture
def db():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session
    engine.dispose()


def rule(db, policy="REQUIRED", action="CREATE_GITHUB_ISSUE", trigger=None):
    return AutomationStore(db).create(
        name="GitHub follow-up",
        description="",
        enabled=True,
        source="github" if trigger else "gmail",
        trigger_type=trigger or "NEW_EMAIL",
        match_config={},
        context_config={},
        action_config={
            "type": "NOTIFY" if trigger else action,
            "repository": "owner/repo",
            "branch": "feature/review",
            "from_ref": "main",
        },
        approval_policy=policy,
    )


class Provider:
    def __init__(self, route="AUTO_ACT", proposal=None):
        self.route = route
        self.proposal = proposal or {
            "title": "Review dependency conflict",
            "body": "Review the reported conflict.",
        }

    def complete(self, message, **kwargs):
        if "action-safety router" in message:
            return json.dumps({"decision": self.route, "confidence": 0.99, "reason": "Routine"})
        return json.dumps(self.proposal)


class GitHub:
    def __init__(self):
        self.issues, self.refs = {}, {"main": "abc123"}
        self.issue_calls = self.branch_calls = self.close_calls = 0
        self.fail_read = False

    def create_issue(self, **kw):
        self.issue_calls += 1
        result = {"number": self.issue_calls, "title": kw["title"], "body": kw["body"]}
        self.issues[self.issue_calls] = result
        return result

    def get_issue(self, **kw):
        if self.fail_read:
            raise httpx.ReadTimeout("timeout")
        return self.issues[kw["number"]]

    def get_branch_ref(self, **kw):
        return {"ref": "refs/heads/" + kw["branch"], "object": {"sha": self.refs[kw["branch"]]}}

    def create_branch_at_sha(self, **kw):
        self.branch_calls += 1
        self.refs[kw["branch"]] = kw["sha"]

    def close(self):
        self.close_calls += 1


def executor(db, monkeypatch, *, enabled=True, client=None, provider=None):
    client = client or GitHub()
    monkeypatch.setattr("app.automation.github_action.get_github_client", lambda *a: client)
    message = SimpleNamespace(
        id="incoming",
        thread_id="thread",
        subject="Dependency conflict",
        body="Please track this dependency problem.",
        sender="friend@example.com",
    )
    gmail = SimpleNamespace(get_message=lambda _: message, get_latest_unread_inbox=lambda: message)
    return AutomationExecutor(
        db=db,
        settings=Settings(enable_external_actions=enabled),
        gmail=gmail,
        provider=provider or Provider(),
    ), client


@pytest.mark.parametrize(
    "policy,decision", [("REQUIRED", "AWAITING_APPROVAL"), ("DRAFT_ONLY", "DRAFT_READY")]
)
def test_issue_proposal_obeys_policy(db, monkeypatch, policy, decision):
    run, github = executor(db, monkeypatch)
    result = run.run(automation=rule(db, policy))
    assert result["decision"] == decision
    assert github.issue_calls == 0
    approved = run.approve(execution=db.get(AutomationExecution, result["execution"]["id"]))
    assert approved["decision"] == ("DRAFT_ONLY" if policy == "DRAFT_ONLY" else "VERIFIED_SUCCESS")
    assert github.issue_calls == (0 if policy == "DRAFT_ONLY" else 1)


def test_auto_issue_verified_and_force_is_deduplicated(db, monkeypatch):
    run, github = executor(db, monkeypatch)
    automation = rule(db, "AUTO")
    assert run.run(automation=automation)["verification_status"] == "VERIFIED_SUCCESS"
    assert run.run(automation=automation)["decision"] == "ALREADY_EXECUTED"
    assert github.issue_calls == 1
    assert github.close_calls == 1


def test_durable_claim_blocks_forced_duplicate_after_lost_readback(db, monkeypatch):
    run, github = executor(db, monkeypatch)
    github.fail_read = True
    automation = rule(db, "AUTO")
    assert run.run(automation=automation)["decision"] == "UNOBSERVED"
    assert run.run(automation=automation, force=True)["decision"] == "UNOBSERVED"
    assert github.issue_calls == 1


def test_branch_verifies_target_sha(db, monkeypatch):
    run, github = executor(db, monkeypatch)
    result = run.run(automation=rule(db, "AUTO", action="CREATE_GITHUB_BRANCH"))
    assert result["verification_status"] == "VERIFIED_SUCCESS"
    assert github.refs["feature/review"] == "abc123"
    assert github.branch_calls == 1


def test_disabled_external_actions_do_not_write(db, monkeypatch):
    run, github = executor(db, monkeypatch, enabled=False)
    assert run.run(automation=rule(db, "AUTO"))["decision"] == "BLOCKED_BY_POLICY"
    assert github.issue_calls == 0


def test_auto_router_falls_back_to_notification(db, monkeypatch):
    run, github = executor(db, monkeypatch, provider=Provider(route="NOTIFY_ONLY"))
    assert run.run(automation=rule(db, "AUTO"))["decision"] == "FALLBACK_NOTIFICATION"
    assert github.issue_calls == 0
    assert db.scalars(select(Notification)).one().category == "ACTION_REQUIRED"


def test_model_cannot_override_user_repository(db, monkeypatch):
    run, github = executor(
        db, monkeypatch, provider=Provider(proposal={"title": "Task", "repository": "other/repo"})
    )
    assert run.run(automation=rule(db, "AUTO"))["decision"] == "FALLBACK_NOTIFICATION"
    assert github.issue_calls == 0


@pytest.mark.parametrize(
    "trigger", ["GITHUB_ISSUE_CHANGED", "GITHUB_PR_CHANGED", "GITHUB_WORKFLOW_CHANGED"]
)
def test_monitor_baselines_detects_change_and_deduplicates(db, monkeypatch, trigger):
    automation = rule(db, trigger=trigger)
    client = GitHub()
    current = [
        {
            "id": 1,
            "title": "Review",
            "name": "Build",
            "state": "open",
            "status": "queued",
            "updated_at": "v1",
        }
    ]
    client.list_issues = client.list_pulls = client.list_workflow_runs = lambda **kw: current
    monkeypatch.setattr("app.runtime.github_runtime.get_github_client", lambda *a: client)
    monkeypatch.setattr(
        "app.runtime.github_runtime.ConnectionStore.get_active",
        lambda *a, **kw: SimpleNamespace(id="connection"),
    )
    runtime = GitHubRuntimeService(db=db, settings=Settings())
    runtime.poll()
    assert not db.scalars(select(Notification)).all()
    current[0] = {**current[0], "updated_at": "v2", "conclusion": "failure"}
    assert runtime.poll()["processed"] == 1
    runtime.poll()
    assert len(db.scalars(select(Notification)).all()) == 1
    assert db.scalars(select(AutomationExecution)).one().source == "github"
    assert db.scalars(select(AutomationExecution)).one().automation_id == automation.id


def test_monitor_repository_error_does_not_stop_other_repository(db, monkeypatch):
    good = rule(db, trigger="GITHUB_ISSUE_CHANGED")
    bad = rule(db, trigger="GITHUB_ISSUE_CHANGED")
    bad.action_config = {"type": "NOTIFY", "repository": "owner/broken"}
    db.commit()
    client = GitHub()

    def issues(**kw):
        if kw["repo"] == "broken":
            raise RuntimeError("Cannot read repository")
        return [{"id": 1, "title": "Task"}]

    client.list_issues = issues
    monkeypatch.setattr("app.runtime.github_runtime.get_github_client", lambda *a: client)
    monkeypatch.setattr(
        "app.runtime.github_runtime.ConnectionStore.get_active",
        lambda *a, **kw: SimpleNamespace(id="connection"),
    )
    result = GitHubRuntimeService(db=db, settings=Settings()).poll()
    assert result["decision"] == "PARTIAL_ERROR"
    assert len(result["errors"]) == 1
    assert result["errors"][0]["automation_id"] == bad.id
    assert good.id != bad.id


@pytest.mark.parametrize("value", ["../repo", "owner/../repo", "owner/repo?x=1", "owner/.."])
def test_repository_validation_rejects_path_injection(value):
    with pytest.raises(ValueError):
        repository_parts(value)


@pytest.mark.parametrize(
    "value", ["../main", "feature..x", "-oops", "feature//bad", "branch.lock", "bad?name"]
)
def test_invalid_branches(value):
    with pytest.raises(ValueError):
        validate_branch(value)


def test_github_api_dispatch_acceptance_and_encoded_branch_paths(monkeypatch):
    for name in (
        "ALL_PROXY",
        "all_proxy",
        "HTTPS_PROXY",
        "https_proxy",
        "HTTP_PROXY",
        "http_proxy",
    ):
        monkeypatch.delenv(name, raising=False)
    seen = []

    def handler(request):
        seen.append(request)
        return (
            httpx.Response(204)
            if request.method == "POST"
            else httpx.Response(200, json={"object": {"sha": "123"}})
        )

    github = GitHubClient(access_token="test-token")
    github.client.close()
    github.client = httpx.Client(
        base_url="https://api.github.com", transport=httpx.MockTransport(handler)
    )
    github.get_branch_ref(owner="owner", repo="repo", branch="feature/review")
    github.dispatch_workflow(owner="owner", repo="repo", workflow_id="build.yml", ref="main")
    assert b"feature%2Freview" in seen[0].url.raw_path
    assert seen[1].method == "POST"
    github.close()


def test_github_automation_validation():
    with TestClient(app) as client:
        config = {
            "name": "Failed builds",
            "source": "github",
            "trigger_type": "GITHUB_WORKFLOW_CHANGED",
            "action_config": {"type": "NOTIFY", "repository": "owner/repo"},
        }
        assert client.post("/automations", json=config).status_code == 201
        assert (
            client.post("/automations", json={**config, "trigger_type": "NEW_EMAIL"}).status_code
            == 422
        )
        assert (
            client.post(
                "/automations", json={**config, "action_config": {"type": "NOTIFY"}}
            ).status_code
            == 422
        )
        assert (
            client.post(
                "/automations",
                json={
                    **config,
                    "action_config": {"type": "CREATE_GITHUB_ISSUE", "repository": "owner/repo"},
                },
            ).status_code
            == 422
        )
        assert (
            client.post(
                "/github/repos/owner/repo/workflow-dispatch", json={"workflow_id": "build.yml"}
            ).status_code
            == 403
        )


def test_missing_connection_produces_actionable_notification(db, monkeypatch):
    run, _ = executor(db, monkeypatch)

    def disconnected(*args):
        raise RuntimeError("GitHub is not connected")

    monkeypatch.setattr("app.automation.github_action.get_github_client", disconnected)
    result = run.run(automation=rule(db, "AUTO"))
    assert result["decision"] == "FALLBACK_NOTIFICATION"
    assert "Reconnect" in result["execution"]["output"]["reason"]


def test_github_oauth_callback_persists_connection_and_disconnect_is_independent(monkeypatch):
    from urllib.parse import parse_qs, urlparse
    from cryptography.fernet import Fernet
    from app.routes import github as routes

    settings = Settings(
        github_oauth_client_id="test-client",
        github_oauth_client_secret="test-secret",
        token_encryption_key=Fernet.generate_key().decode(),
    )
    monkeypatch.setattr(routes, "get_settings", lambda: settings)
    monkeypatch.setattr(
        routes,
        "exchange_github_code",
        lambda *a, **kw: {"access_token": "test-token", "scope": "repo,read:user"},
    )
    monkeypatch.setattr(
        routes,
        "GitHubClient",
        lambda **kw: SimpleNamespace(get_user=lambda: {"login": "connected-user", "id": 1}),
    )
    with TestClient(app) as client:
        start = client.get("/connections/github/start", follow_redirects=False)
        assert start.status_code == 302
        state = parse_qs(urlparse(start.headers["location"]).query)["state"][0]
        callback = client.get(
            "/connections/github/callback",
            params={"state": state, "code": "test-code"},
            follow_redirects=False,
        )
        assert callback.status_code == 302
        assert "status=connected" in callback.headers["location"]
        catalog = {item["id"]: item for item in client.get("/connections").json()["catalog"]}
        assert catalog["github"]["status"] == "CONNECTED"
        assert catalog["github"]["account"] == "connected-user"
        assert "test-token" not in client.get("/connections").text
        client.post("/connections/github/disconnect")
        assert client.get("/github/status").json()["connected"] is False
        assert (
            client.get(
                "/connections/github/callback",
                params={"state": state, "code": "test-code"},
                follow_redirects=False,
            )
            .headers["location"]
            .endswith("detail=invalid_or_expired_state")
        )
