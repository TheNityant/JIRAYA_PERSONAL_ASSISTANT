"""A separately started loopback service: no cloud server can read local folders."""

import json
import asyncio
import secrets
import threading
import time
from datetime import UTC, datetime
from inspect import signature
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.integrations.github.validation import validate_branch
from app.local_companion.git import (
    GitProblem,
    NotARepository,
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


class MeetRetry(BaseModel):
    key: str = Field(pattern=r"^[a-f0-9]{64}$")


class MeetConnect(BaseModel):
    api_base: str = Field(max_length=500)
    device_token: str = Field(min_length=30, max_length=200)


class PairingRequest(BaseModel):
    nonce: str = Field(pattern=r"^[a-f0-9]{64}$")
    origin: str = Field(max_length=300)


class PairingClaim(BaseModel):
    nonce: str = Field(pattern=r"^[a-f0-9]{64}$")


def frontend_origin(value: str) -> str:
    parsed = urlsplit(value)
    if (
        parsed.scheme not in {"http", "https"} or not parsed.hostname
        or parsed.username or parsed.password or parsed.path or parsed.query or parsed.fragment
        or (parsed.scheme != "https" and parsed.hostname not in {"localhost", "127.0.0.1"})
    ):
        raise ValueError("Use an HTTPS frontend origin, or HTTP localhost for development")
    return value


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
        self.pairings = {}

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
                row = {"path": folder, "error": str(exc)}
                if isinstance(exc, NotARepository):
                    row["reason"] = "not_a_repository"
                folders.append(row)
        # VS Code can report both a parent workspace and the nested repositories
        # its Git integration already has open. This is not a filesystem scan.
        for row in folders:
            if row.get("reason") != "not_a_repository":
                continue
            parent = Path(row["path"]).expanduser().resolve()
            nested = [
                str(root)
                for root in roots.values()
                if root != parent and root.is_relative_to(parent)
            ]
            if nested:
                row.pop("error")
                row["roots"] = nested
                row["message"] = (
                    f"{len(nested)} repositories detected by the editor inside this workspace."
                )
        known_roots = {**self.open_roots()[0], **roots}
        for folder in body.unsaved_folders:
            try:
                unsaved.add(repo_id(repository_root(folder)))
            except (OSError, GitProblem):
                # New unsaved documents can live in directories not yet on disk.
                # Keep their enclosing registered repositories blocked as well.
                path = Path(folder).expanduser().resolve()
                unsaved.update(
                    project_id
                    for project_id, root in known_roots.items()
                    if path.is_relative_to(root) or root.is_relative_to(path)
                )
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
    origins = list(dict.fromkeys(origins + state.config.get("origins", [])))
    from app.meet_agent.device import MeetDevice
    attendance = MeetDevice(state)

    @asynccontextmanager
    async def lifespan(_):
        attendance.start()
        try:
            yield
        finally:
            attendance.stop()
            if attendance.thread is not None:
                await asyncio.to_thread(attendance.thread.join, 25)

    app = FastAPI(title="Jiraya Local Workspace Companion", docs_url=None, redoc_url=None, lifespan=lifespan)
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

    app.state.meet = attendance

    @app.get("/meeting/status", dependencies=[Depends(authorize)])
    def meeting_status():
        return attendance.status()

    @app.post("/meeting/connect", dependencies=[Depends(authorize)])
    def meeting_connect(body: MeetConnect, request: Request):
        origin = request.headers.get("origin", "")
        try:
            frontend_origin(origin)
        except ValueError:
            raise HTTPException(422, "Use HTTPS or localhost for device authorization") from None
        if not origin or body.api_base.rstrip("/") != origin + "/api/jiraya":
            raise HTTPException(422, "Connect from your Jiraya website using its same-origin API.")
        try:
            return attendance.configure(body.api_base, body.device_token)
        except Exception:
            raise HTTPException(503, "Could not authorize this computer. Check Calendar and the deployed API.") from None

    @app.post("/meeting/retry", dependencies=[Depends(authorize)])
    def retry_attendance(body: MeetRetry):
        try:
            return attendance.retry(body.key)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.post("/meeting/pause", dependencies=[Depends(authorize)])
    def pause_attendance():
        return attendance.pause()

    @app.get("/health", dependencies=[Depends(authorize)])
    def health():
        return {"service": "jiraya-local-companion", "version": 2, "pairing_handoff": True, "meet_attendance": True}

    @app.post("/pairing/requests", dependencies=[Depends(authorize)])
    def request_pairing(body: PairingRequest):
        try:
            origin = frontend_origin(body.origin)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        # Only a local client already holding the device token can approve an origin.
        # The VS Code extension confirms that origin with the user before this call.
        with state.guard:
            now = time.monotonic()
            state.pairings = {key: row for key, row in state.pairings.items() if row["until"] > now}
            if len(state.pairings) >= 20:
                raise HTTPException(429, "Too many pending pairing requests")
            if origin not in origins:
                origins.append(origin)
            state.config["origins"] = origins
            state.save()
            state.pairings[body.nonce] = {"origin": origin, "until": now + 120}
        return {"ready": True}

    @app.post("/pairing/claim")
    def claim_pairing(body: PairingClaim, request: Request):
        origin = request.headers.get("origin")
        with state.guard:
            row = state.pairings.get(body.nonce)
            if row is None or row["until"] <= time.monotonic():
                state.pairings.pop(body.nonce, None)
                raise HTTPException(404, "Pairing is not ready or has expired")
            if origin != row["origin"]:
                raise HTTPException(403, "Pairing belongs to another frontend origin")
            state.pairings.pop(body.nonce)
            return JSONResponse(
                {"token": state.config["token"], "service": "jiraya-local-companion"},
                headers={"Cache-Control": "no-store"},
            )

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
