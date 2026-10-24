"""Use real local Git repositories and bare remotes; never push user repositories."""

import subprocess
import time

import pytest
from fastapi.testclient import TestClient

from app.local_companion import git as workspace_git
from app.local_companion.git import (
    GitProblem,
    changed_files,
    checkpoint,
    github_repository,
    repo_id,
)
from app.local_companion.server import (
    Companion,
    ProjectPolicy,
    WorkspaceHeartbeat,
    create_companion,
)


def run(root, *args):
    return subprocess.check_output(
        ["git", "-C", str(root), *args], stderr=subprocess.STDOUT, text=True
    ).strip()


@pytest.fixture
def project(tmp_path, monkeypatch):
    bare = tmp_path / "remote.git"
    bare.mkdir()
    run(bare, "init", "--bare", "--initial-branch=main")
    root = tmp_path / "project"
    root.mkdir()
    run(root, "init", "--initial-branch=main")
    run(root, "config", "user.name", "Test Developer")
    run(root, "config", "user.email", "test@example.com")
    (root / "app.py").write_text("original\n")
    (root / ".gitignore").write_text(".env\nnode_modules/\n")
    run(root, "add", ".")
    run(root, "commit", "-m", "Initial")
    run(root, "remote", "add", "origin", str(bare))
    run(root, "push", "-u", "origin", "main")
    # URL validation is tested separately. The transport here is a real bare Git remote.
    monkeypatch.setattr(workspace_git, "remote", lambda root: (str(bare), "owner/repo"))
    monkeypatch.setattr("app.local_companion.server.remote", lambda root: (str(bare), "owner/repo"))
    policy = {
        "branch": "main",
        "repository": "owner/repo",
        "remote": str(bare),
        "path": str(root),
        "enabled": True,
    }
    return root, bare, policy


def test_checkpoint_commits_additions_modifications_deletions_and_verifies_remote(project):
    root, bare, policy = project
    (root / "app.py").unlink()
    (root / "new.py").write_text("new saved code\n")
    result = checkpoint(root, policy, "End of day")
    assert result["status"] == "PUSHED"
    assert result["committed"] is True
    assert run(bare, "rev-parse", "refs/heads/main") == run(root, "rev-parse", "HEAD")
    assert run(bare, "log", "-1", "--format=%s") == "End of day"
    assert run(bare, "ls-tree", "--name-only", "HEAD").splitlines() == [".gitignore", "new.py"]
    assert changed_files(root) == []
    assert checkpoint(root, policy, "Again")["status"] == "UP_TO_DATE"


def test_existing_unpushed_commit_is_pushed_without_an_extra_commit(project):
    root, bare, policy = project
    (root / "app.py").write_text("already committed\n")
    run(root, "commit", "-am", "Manual local commit")
    head = run(root, "rev-parse", "HEAD")
    result = checkpoint(root, policy, "Daily checkpoint")
    assert result["status"] == "PUSHED"
    assert result["committed"] is False
    assert run(bare, "rev-parse", "HEAD") == head
    assert run(root, "rev-parse", "HEAD") == head


def test_wrong_branch_never_switches_or_commits(project):
    root, bare, policy = project
    (root / "app.py").write_text("keep me local")
    head = run(root, "rev-parse", "HEAD")
    with pytest.raises(GitProblem, match="configured branch"):
        checkpoint(root, {**policy, "branch": "release"}, "Wrong branch")
    assert run(root, "branch", "--show-current") == "main"
    assert run(root, "rev-parse", "HEAD") == head
    assert run(bare, "rev-parse", "HEAD") == head


def test_remote_ahead_is_not_overwritten_and_local_changes_remain(project, tmp_path):
    root, bare, policy = project
    other = tmp_path / "other"
    run(tmp_path, "clone", str(bare), str(other))
    run(other, "config", "user.name", "Other")
    run(other, "config", "user.email", "other@example.com")
    (other / "other.py").write_text("remote change")
    run(other, "add", ".")
    run(other, "commit", "-m", "Remote advance")
    run(other, "push")
    remote_head = run(bare, "rev-parse", "HEAD")
    local_head = run(root, "rev-parse", "HEAD")
    (root / "app.py").write_text("pending local edits")
    with pytest.raises(GitProblem, match="behind or diverged"):
        checkpoint(root, policy, "Do not overwrite")
    assert run(bare, "rev-parse", "HEAD") == remote_head
    assert run(root, "rev-parse", "HEAD") == local_head
    assert (root / "app.py").read_text() == "pending local edits"


def test_failed_push_preserves_checkpoint_for_retry(project):
    root, bare, policy = project
    hook = bare / "hooks" / "pre-receive"
    hook.write_text("#!/bin/sh\nexit 1\n")
    hook.chmod(0o755)
    previous = run(bare, "rev-parse", "HEAD")
    (root / "app.py").write_text("preserve checkpoint")
    with pytest.raises(GitProblem):
        checkpoint(root, policy, "Preserve this commit")
    local = run(root, "rev-parse", "HEAD")
    assert local != previous
    assert run(bare, "rev-parse", "HEAD") == previous
    hook.unlink()
    assert checkpoint(root, policy, "Retry")["committed"] is False
    assert run(bare, "rev-parse", "HEAD") == local


def test_ignored_files_are_not_committed_and_sensitive_tracked_files_block(project):
    root, bare, policy = project
    (root / ".env").write_text("SECRET=ignored")
    (root / "app.py").write_text("saved change")
    assert checkpoint(root, policy, "Saved")["status"] == "PUSHED"
    assert ".env" not in run(bare, "ls-tree", "--name-only", "HEAD")
    (root / "credentials.json").write_text("private")
    with pytest.raises(GitProblem, match="credential"):
        checkpoint(root, policy, "Must not include credentials")
    assert "credentials.json" not in run(bare, "ls-tree", "--name-only", "HEAD")


def test_conflict_state_is_not_committed(project):
    root, _, policy = project
    (root / ".git" / "MERGE_HEAD").write_text(run(root, "rev-parse", "HEAD"))
    with pytest.raises(GitProblem, match="active Git"):
        checkpoint(root, policy, "Not during merge")


@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://github.com/owner/repo.git", "owner/repo"),
        ("git@github.com:owner/repo.git", "owner/repo"),
        ("https://github.com/owner/repo", "owner/repo"),
    ],
)
def test_supported_github_remotes(url, expected):
    assert github_repository(url) == expected


@pytest.mark.parametrize(
    "url",
    [
        "https://token@github.com/owner/repo.git",
        "https://evil.test/owner/repo",
        "file:///tmp/repo",
        "-u",
        "https://github.com/owner/repo?token=secret",
    ],
)
def test_invalid_remotes_are_not_used(url):
    with pytest.raises(GitProblem):
        github_repository(url)


def test_device_api_requires_pairing_and_trusted_origin(tmp_path):
    app = create_companion(tmp_path / "state", ["http://localhost:3000"])
    token = app.state.companion.config["token"]
    with TestClient(app) as client:
        assert client.get("/projects").status_code == 401
        assert client.post("/checkpoints", json={}).status_code == 401
        assert (
            client.get(
                "/projects",
                headers={"Authorization": f"Bearer {token}", "Origin": "https://evil.test"},
            ).status_code
            == 403
        )
        assert (
            client.get("/health", headers={"Authorization": f"Bearer {token}"}).status_code == 200
        )
        response = client.options(
            "/projects",
            headers={
                "Origin": "http://localhost:3000",
                "Access-Control-Request-Method": "GET",
                "Access-Control-Request-Headers": "authorization",
                "Access-Control-Request-Private-Network": "true",
            },
        )
        assert response.status_code == 200
        assert response.headers["access-control-allow-private-network"] == "true"
        assert (
            "token" not in client.get("/health", headers={"Authorization": f"Bearer {token}"}).text
        )


def wait(state):
    for _ in range(200):
        if not state.running:
            return state.job
        time.sleep(0.01)
    pytest.fail("Checkpoint did not complete")


def test_only_enabled_open_saved_projects_run_and_ide_windows_deduplicate(project, tmp_path):
    root, bare, _ = project
    state = Companion(tmp_path / "state")
    heartbeat = WorkspaceHeartbeat(session_id="window1", folders=[str(root)])
    state.heartbeat(heartbeat)
    state.heartbeat(WorkspaceHeartbeat(session_id="window2", folders=[str(root)]))
    assert len(state.projects()) == 1
    (root / "app.py").write_text("saved")
    state.start("Disabled repo")
    assert wait(state)["results"] == []
    project_id = repo_id(root)
    state.configure(project_id, ProjectPolicy(enabled=True, branch="main"))
    state.heartbeat(
        WorkspaceHeartbeat(session_id="window2", folders=[str(root)], unsaved_folders=[str(root)])
    )
    state.start("Unsaved window")
    assert wait(state)["results"][0]["status"] == "SKIPPED"
    state.sessions["window2"]["seen"] = -1000
    state.start("Enabled and open")
    assert wait(state)["results"][0]["status"] == "PUSHED"
    assert run(bare, "log", "-1", "--format=%s") == "Enabled and open"
    state.sessions.clear()
    state.start("Closed project")
    assert wait(state)["results"][0]["status"] == "SKIPPED"
    assert Companion(tmp_path / "state").config["last_job"]["results"][0]["status"] == "SKIPPED"


def test_reject_overlapping_checkpoint_jobs(project, tmp_path, monkeypatch):
    import threading

    from app.local_companion import server

    root, _, _ = project
    state = Companion(tmp_path / "state")
    state.heartbeat(WorkspaceHeartbeat(session_id="window", folders=[str(root)]))
    state.configure(repo_id(root), ProjectPolicy(enabled=True, branch="main"))
    gate = threading.Event()

    def blocked(*args):
        gate.wait(3)
        return {"status": "UP_TO_DATE"}

    monkeypatch.setattr(server, "checkpoint", blocked)
    state.start("First")
    try:
        from fastapi import HTTPException

        with pytest.raises(HTTPException, match="already running"):
            state.start("Second")
    finally:
        gate.set()
    wait(state)


def test_checkpoint_pushes_only_the_configured_branch(project):
    root, bare, policy = project
    main_tip = run(bare, "rev-parse", "HEAD")
    run(root, "switch", "-c", "feature/daily-work")
    (root / "app.py").write_text("branch-specific saved work")
    result = checkpoint(root, {**policy, "branch": "feature/daily-work"}, "Feature checkpoint")
    assert result["status"] == "PUSHED"
    assert run(bare, "rev-parse", "refs/heads/feature/daily-work") == result["commit"]
    assert run(bare, "rev-parse", "refs/heads/main") == main_tip


def test_multiple_projects_continue_after_one_needs_attention(project, tmp_path, monkeypatch):
    root, bare, _ = project
    second_bare = tmp_path / "second.git"
    second_bare.mkdir()
    run(second_bare, "init", "--bare", "--initial-branch=main")
    second = tmp_path / "second"
    run(tmp_path, "clone", str(bare), str(second))
    run(second, "config", "user.name", "Test Developer")
    run(second, "config", "user.email", "test@example.com")
    run(second, "remote", "set-url", "origin", str(second_bare))
    run(second, "push", "-u", "origin", "main")

    def local_remote(path):
        return (str(bare), "owner/one") if path == root else (str(second_bare), "owner/two")

    monkeypatch.setattr(workspace_git, "remote", local_remote)
    monkeypatch.setattr("app.local_companion.server.remote", local_remote)
    state = Companion(tmp_path / "state")
    state.heartbeat(WorkspaceHeartbeat(session_id="projects", folders=[str(root), str(second)]))
    for path in [root, second]:
        state.configure(repo_id(path), ProjectPolicy(enabled=True, branch="main"))
    (root / "credentials.json").write_text("needs manual review")
    (second / "app.py").write_text("push this project")
    before = run(bare, "rev-parse", "HEAD")
    state.start("Multiple projects checkpoint")
    results = {row["repository"]: row for row in wait(state)["results"]}
    assert results["owner/one"]["status"] == "NEEDS_ATTENTION"
    assert results["owner/two"]["status"] == "PUSHED"
    assert run(bare, "rev-parse", "HEAD") == before
    assert run(second_bare, "rev-parse", "HEAD") == results["owner/two"]["commit"]


def test_changed_origin_cannot_be_pushed_with_old_policy(project, monkeypatch):
    root, bare, policy = project
    monkeypatch.setattr(
        workspace_git,
        "remote",
        lambda root: ("https://github.com/other/target.git", "other/target"),
    )
    before = run(bare, "rev-parse", "HEAD")
    with pytest.raises(GitProblem, match="origin remote changed"):
        checkpoint(root, policy, "No redirect")
    assert run(bare, "rev-parse", "HEAD") == before


def test_push_is_pinned_to_checkpoint_not_a_concurrent_new_commit(project, monkeypatch):
    root, bare, policy = project
    (root / "app.py").write_text("checkpoint snapshot")
    native_git = workspace_git.git

    def concurrent_git(path, *args, **kwargs):
        if args[0] == "push":
            (root / "later.py").write_text("saved and committed after the checkpoint")
            run(root, "add", "later.py")
            run(root, "commit", "-m", "Concurrent later commit")
        return native_git(path, *args, **kwargs)

    monkeypatch.setattr(workspace_git, "git", concurrent_git)
    result = checkpoint(root, policy, "Pinned snapshot")
    assert result["status"] == "PUSHED"
    assert run(bare, "rev-parse", "HEAD") == result["commit"]
    assert run(root, "rev-parse", "HEAD") != result["commit"]
    assert result["remaining_commits"] == 1


def test_registration_diagnostics_include_rejected_folders_and_expire(project, tmp_path):
    root, _, _ = project
    non_git = tmp_path / "parent"
    non_git.mkdir()
    child = root / "src"
    child.mkdir()
    missing = tmp_path / "missing"
    app = create_companion(tmp_path / "state", ["http://localhost:3000"])
    state = app.state.companion
    headers = {"Authorization": f"Bearer {state.config['token']}"}
    with TestClient(app) as client:
        assert client.get("/workspaces").status_code == 401
        assert (
            client.get(
                "/workspaces", headers={**headers, "Origin": "https://evil.test"}
            ).status_code
            == 403
        )
        response = client.post(
            "/workspaces/heartbeat",
            headers=headers,
            json={
                "session_id": "window",
                "folders": [str(root), str(child), str(non_git), str(missing)],
            },
        )
        summary = response.json()
        assert summary["registered"] is True
        assert summary["folder_count"] == 4
        assert summary["project_count"] == 1  # Nested workspace folders deduplicate.
        assert len(summary["issues"]) == 2
        assert "repository folder" in summary["issues"][0]["error"]
        registration = client.get("/workspaces", headers=headers).json()
        assert registration["session_count"] == 1
        assert registration["items"][0]["folders"][0]["root"] == str(root)
        listing = client.get("/projects", headers=headers).json()
        assert listing["registration"]["session_count"] == 1
        assert len(listing["items"]) == 1
        assert state.config["token"] not in client.get("/workspaces", headers=headers).text
        state.sessions["window"]["seen"] = time.monotonic() - 46
        assert client.get("/workspaces", headers=headers).json() == {
            "session_count": 0,
            "items": [],
        }
        assert client.get("/projects", headers=headers).json()["items"] == []


def test_api_flow_register_enable_checkpoint_verify_push_and_unregister(project, tmp_path):
    root, bare, _ = project
    app = create_companion(tmp_path / "state", ["http://localhost:3000"])
    state = app.state.companion
    headers = {
        "Authorization": f"Bearer {state.config['token']}",
        "Origin": "http://localhost:3000",
    }
    with TestClient(app) as client:
        response = client.post(
            "/workspaces/heartbeat",
            headers=headers,
            json={"session_id": "ide-window", "folders": [str(root)]},
        )
        assert response.json()["project_count"] == 1
        listing = client.get("/projects", headers=headers).json()
        project_id = listing["items"][0]["id"]
        assert listing["items"][0]["enabled"] is False
        assert (
            client.put(
                f"/projects/{project_id}", headers=headers, json={"enabled": True, "branch": "main"}
            ).status_code
            == 200
        )
        (root / "app.py").write_text("registered through the API\n")
        result = client.post("/checkpoints", headers=headers, json={"message": "API checkpoint"})
        assert result.status_code == 202
        wait(state)
        job = client.get("/checkpoints/latest", headers=headers).json()
        assert job["id"] == result.json()["id"]
        assert job["status"] == "COMPLETE"
        assert job["results"][0]["status"] == "PUSHED"
        assert run(bare, "rev-parse", "refs/heads/main") == job["results"][0]["commit"]
        assert client.delete("/workspaces/ide-window", headers=headers).status_code == 200
        assert client.get("/workspaces", headers=headers).json()["session_count"] == 0
        assert client.get("/projects", headers=headers).json()["items"] == []
