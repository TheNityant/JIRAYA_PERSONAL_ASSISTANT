"""A separately started loopback service: no cloud server can read local folders."""

import json
import secrets
import threading
import time
from datetime import UTC, datetime
from inspect import signature
from pathlib import Path
from uuid import uuid4

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from pydantic import BaseModel, Field

from app.integrations.github.validation import validate_branch
from app.local_companion.git import (
    GitProblem,
    checkpoint,
    inspect,
    remote,
    repo_id,
    repository_root,
)


class LoopbackCORS(CORSMiddleware):
    def preflight_response(self, request_headers):
        response = super().preflight_response(request_headers)
        if (
            response.status_code == 200
            and request_headers.get("access-control-request-private-network") == "true"
        ):
            response.headers["Access-Control-Allow-Private-Network"] = "true"
        return response


class WorkspaceHeartbeat(BaseModel):
    session_id: str = Field(min_length=1, max_length=100)
    folders: list[str] = Field(max_length=100)
    unsaved_folders: list[str] = Field(default_factory=list, max_length=100)


class ProjectPolicy(BaseModel):
    enabled: bool
    branch: str = Field(min_length=1, max_length=240)


class RunRequest(BaseModel):
    message: str = Field(default="Jiraya workspace checkpoint", min_length=1, max_length=200)


class Companion:
    def __init__(self, directory: Path):
        self.directory = directory
        directory.mkdir(parents=True, exist_ok=True)
        self.file = directory / "companion.json"
        self.guard = threading.RLock()
        if self.file.exists():
            self.config = json.loads(self.file.read_text())
        else:
            self.config = {"token": secrets.token_urlsafe(32), "projects": {}}
            self.save()
        self.sessions = {}
        self.job = None
        self.running = False

    def save(self):
        temporary = self.file.with_suffix(".tmp")
        temporary.write_text(json.dumps(self.config, indent=2))
        temporary.chmod(0o600)
        temporary.replace(self.file)

    def heartbeat(self, body: WorkspaceHeartbeat):
        roots, unsaved, folders = {}, set(), []
        for folder in body.folders:
            try:
                root = repository_root(folder)
                roots[repo_id(root)] = root
                folders.append({"path": folder, "root": str(root)})
            except (OSError, GitProblem) as exc:
                folders.append({"path": folder, "error": str(exc)})
        for folder in body.unsaved_folders:
            try:
                unsaved.add(repo_id(repository_root(folder)))
            except (OSError, GitProblem):
                continue
        with self.guard:
            self.sessions[body.session_id] = {
                "seen": time.monotonic(),
                "roots": roots,
                "unsaved": unsaved,
                "folders": folders,
            }
        return {
            "registered": True,
            "folder_count": len(folders),
            "project_count": len(roots),
            "issues": [folder for folder in folders if "error" in folder],
        }

    def registrations(self):
        """Expose registration failures only through the authenticated local API."""
        now = time.monotonic()
        with self.guard:
            items = [
                {
                    "age_seconds": round(max(0, now - session["seen"]), 1),
                    "project_count": len(session["roots"]),
                    "folders": session.get("folders", []),
                }
                for session in self.sessions.values()
                if now - session["seen"] < 45
            ]
        return {"items": items, "session_count": len(items)}

    def open_roots(self):
        with self.guard:
            fresh = [s for s in self.sessions.values() if time.monotonic() - s["seen"] < 45]
            roots, unsaved = {}, set()
            for session in fresh:
                roots.update(session["roots"])
                unsaved.update(session["unsaved"])
            return roots, unsaved

    def projects(self):
        roots, unsaved = self.open_roots()
        rows = []
        for project_id, root in sorted(roots.items()):
            with self.guard:
                policy = dict(self.config["projects"].get(project_id, {}))
            try:
                row = inspect(root)
                row.update(
                    {
                        "enabled": policy.get("enabled", False),
                        "target_branch": policy.get("branch", row["branch"]),
                        "unsaved": project_id in unsaved,
                    }
                )
                rows.append(row)
            except (OSError, GitProblem) as exc:
                rows.append(
                    {"id": project_id, "path": str(root), "enabled": False, "error": str(exc)}
                )
        return rows

    def configure(self, project_id: str, policy: ProjectPolicy):
        roots, _ = self.open_roots()
        if project_id not in roots:
            raise HTTPException(
                409, "Open this project in your registered IDE before configuring it."
            )
        validate_branch(policy.branch)
        row = inspect(roots[project_id])
        url, repository = remote(roots[project_id])
        if policy.enabled and row["branch"] != policy.branch:
            raise HTTPException(
                409, "Switch this project's local checkout to the chosen branch before enabling it."
            )
        with self.guard:
            if self.running:
                raise HTTPException(
                    409, "Wait for the current checkpoint to finish before changing policies."
                )
            self.config["projects"][project_id] = {
                "path": str(roots[project_id]),
                "branch": policy.branch,
                "repository": repository,
                "remote": url,
                "enabled": policy.enabled,
            }
            self.save()

    def start(self, message: str):
        with self.guard:
            if self.running:
                raise HTTPException(409, "A workspace checkpoint is already running.")
            self.running = True
            policies = {
                key: dict(value)
                for key, value in self.config["projects"].items()
                if value["enabled"]
            }
            self.job = {
                "id": str(uuid4()),
                "status": "RUNNING",
                "started_at": datetime.now(UTC).isoformat(),
                "results": [],
            }
            job_id = self.job["id"]
        threading.Thread(target=self.run, args=(policies, message), daemon=True).start()
        return {"id": job_id, "status": "RUNNING"}

    def run(self, policies, message):
        try:
            for project_id, policy in policies.items():
                result = {
                    "id": project_id,
                    "repository": policy["repository"],
                    "branch": policy["branch"],
                }
                try:
                    roots, unsaved = self.open_roots()
                    if project_id not in roots:
                        result.update(
                            status="SKIPPED",
                            detail="Project is no longer open in a registered IDE.",
                        )
                    elif project_id in unsaved:
                        result.update(
                            status="SKIPPED",
                            detail="Save this project's unsaved editor files, wait for the next heartbeat, then retry.",
                        )
                    else:
                        result.update(checkpoint(roots[project_id], policy, message))
                except (GitProblem, ValueError, OSError) as exc:
                    result.update(status="NEEDS_ATTENTION", detail=str(exc))
                except Exception:  # noqa: BLE001 -- one failed project must not stop the other projects.
                    result.update(
                        status="NEEDS_ATTENTION",
                        detail="Unexpected local Git failure. Review this repository locally before retrying.",
                    )
                with self.guard:
                    self.job["results"].append(result)
            with self.guard:
                self.job["status"] = "COMPLETE"
                self.job["finished_at"] = datetime.now(UTC).isoformat()
                self.job["message"] = (
                    "No projects enabled. Enable an open project first."
                    if not policies
                    else "Checkpoint finished. Review each project's result."
                )
                # Persist the result, excluding paths, tokens and file contents.
                self.config["last_job"] = self.job
                self.save()
        finally:
            with self.guard:
                self.running = False


def create_companion(directory: Path, origins: list[str]) -> FastAPI:
    state = Companion(directory)
    app = FastAPI(title="Jiraya Local Workspace Companion", docs_url=None, redoc_url=None)
    app.state.companion = state
    app.add_middleware(
        TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost", "testserver"]
    )
    app.add_middleware(
        LoopbackCORS,
        allow_origins=origins,
        allow_methods=["GET", "POST", "PUT", "DELETE"],
        allow_headers=["Authorization", "Content-Type"],
        **(
            {"allow_private_network": True}
            if "allow_private_network" in signature(CORSMiddleware).parameters
            else {}
        ),
    )

    def authorize(request: Request, authorization: str = Header(default="")):
        origin = request.headers.get("origin")
        if origin is not None and origin not in origins:
            raise HTTPException(403, "This frontend origin is not paired with the local companion.")
        if not secrets.compare_digest(authorization, f"Bearer {state.config['token']}"):
            raise HTTPException(401, "Pair this device using the companion token.")

    @app.get("/health", dependencies=[Depends(authorize)])
    def health():
        return {"service": "jiraya-local-companion", "version": 1}

    @app.post("/workspaces/heartbeat", dependencies=[Depends(authorize)])
    def heartbeat(body: WorkspaceHeartbeat):
        return state.heartbeat(body)

    @app.get("/workspaces", dependencies=[Depends(authorize)])
    def registrations():
        return state.registrations()

    @app.delete("/workspaces/{session_id}", dependencies=[Depends(authorize)])
    def closed(session_id: str):
        with state.guard:
            state.sessions.pop(session_id, None)
        return {"removed": True}

    @app.get("/projects", dependencies=[Depends(authorize)])
    def projects():
        return {"items": state.projects(), "registration": state.registrations()}

    @app.put("/projects/{project_id}", dependencies=[Depends(authorize)])
    def configure(project_id: str, body: ProjectPolicy):
        try:
            state.configure(project_id, body)
        except (ValueError, GitProblem) as exc:
            raise HTTPException(422, str(exc)) from exc
        return {"saved": True}

    @app.post("/checkpoints", status_code=202, dependencies=[Depends(authorize)])
    def start(body: RunRequest):
        return state.start(body.message)

    @app.get("/checkpoints/latest", dependencies=[Depends(authorize)])
    def latest():
        with state.guard:
            return json.loads(json.dumps(state.job or state.config.get("last_job")))

    return app
