import json
from datetime import UTC, datetime
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.integrations.github.client import GitHubClient
from app.integrations.github.errors import github_error
from app.integrations.github.connection import get_github_client
from app.integrations.github.validation import validate_branch
from app.integrations.github.oauth import (
    build_github_authorization_url,
    exchange_github_code,
    token_expiry,
)
from app.security.tokens import TokenCipher
from app.services.connection_store import ConnectionStore
from app.services.runtime_state_store import RuntimeStateStore
from app.runtime.status import monitor_status

router = APIRouter(tags=["github"])


def _frontend_redirect(*, status: str, detail: str | None = None) -> RedirectResponse:
    settings = get_settings()
    query = {"connection": "github", "status": status}
    if detail:
        query["detail"] = detail[:240]
    return RedirectResponse(f"{settings.frontend_base_url}/?{urlencode(query)}", status_code=302)


def _health_key(user_id: str) -> str:
    return f"oauth-github-health:{user_id}"


def _record_health(db: Session, *, user_id: str, status: str, detail: str | None = None) -> None:
    RuntimeStateStore(db).set(_health_key(user_id), {"status": status, "detail": detail})


@router.get("/connections/github/start")
def start_github_connection(response_mode: str | None = Query(default=None), db: Session = Depends(get_db)):
    settings = get_settings()
    store = ConnectionStore(db)
    store.ensure_user(user_id=settings.dev_user_id, email=settings.dev_user_email)
    try:
        TokenCipher(settings.token_encryption_key)
        state_record = store.create_oauth_state(
            user_id=settings.dev_user_id,
            provider="github",
        )
        authorization_url = build_github_authorization_url(settings, state=state_record.state)
        _record_health(db, user_id=settings.dev_user_id, status="AUTHORIZING")
        if response_mode == "json":
            return {"authorization_url": authorization_url}
        return RedirectResponse(authorization_url, status_code=302)
    except Exception as exc:
        detail = f"{type(exc).__name__}: {str(exc)[:220]}"
        _record_health(db, user_id=settings.dev_user_id, status="ERROR", detail=detail)
        raise HTTPException(status_code=503, detail="GitHub connection is unavailable. Please contact the app owner." if response_mode == "json" else detail) from exc


@router.get("/connections/github/callback")
def github_connection_callback(
    state: str,
    code: str | None = None,
    error: str | None = None,
    error_description: str | None = None,
    db: Session = Depends(get_db),
):
    settings = get_settings()
    store = ConnectionStore(db)
    state_record = store.consume_oauth_state(state=state, provider="github")
    if state_record is None:
        return _frontend_redirect(status="error", detail="invalid_or_expired_state")
    if error:
        detail = error_description or error
        _record_health(db, user_id=state_record.user_id, status="ERROR", detail=detail)
        return _frontend_redirect(status="error", detail=detail)
    if not code:
        _record_health(db, user_id=state_record.user_id, status="ERROR", detail="missing_code")
        return _frontend_redirect(status="error", detail="missing_code")

    try:
        token = exchange_github_code(settings, code=code)
        github = GitHubClient(
            access_token=str(token.get("access_token") or ""),
            api_base_url=settings.github_api_base_url,
        )
        profile = github.get_user()
        login = str(profile.get("login") or "")
        if not login:
            raise RuntimeError("GitHub authorized Jiraya but returned no account login.")
        scopes = str(token.get("scope") or settings.github_oauth_scopes).replace(",", " ").split()
        ConnectionStore(db).upsert(
            user_id=state_record.user_id,
            provider="github",
            provider_account_id=login,
            display_name=str(profile.get("name") or login),
            scopes=scopes,
            credential_ciphertext=TokenCipher(settings.token_encryption_key).encrypt(
                json.dumps(token)
            ),
            token_expires_at=token_expiry(token),
            metadata={
                "github_user_id": profile.get("id"),
                "html_url": profile.get("html_url"),
                "avatar_url": profile.get("avatar_url"),
            },
        )
        _record_health(db, user_id=state_record.user_id, status="CONNECTED")
    except Exception as exc:
        detail = f"{type(exc).__name__}: {str(exc)[:220]}"
        _record_health(db, user_id=state_record.user_id, status="ERROR", detail=detail)
        return _frontend_redirect(status="error", detail=detail)

    return _frontend_redirect(status="connected")


@router.post("/connections/github/disconnect")
def disconnect_github(db: Session = Depends(get_db)) -> dict:
    settings = get_settings()
    connection = ConnectionStore(db).disconnect(
        user_id=settings.dev_user_id,
        provider="github",
    )
    _record_health(db, user_id=settings.dev_user_id, status="DISCONNECTED")
    return {"disconnected": connection is not None, "provider": "github"}


@router.get("/github/status")
def github_status(db: Session = Depends(get_db)) -> dict:
    settings = get_settings()
    connection = ConnectionStore(db).get_active(
        user_id=settings.dev_user_id,
        provider="github",
    )
    return {
        "connected": connection is not None,
        "account": connection.provider_account_id if connection else None,
        "scopes": list(connection.scopes or []) if connection else [],
        "oauth_configured": bool(settings.github_oauth_client_id and settings.github_oauth_client_secret),
        "health": RuntimeStateStore(db).get(_health_key(settings.dev_user_id)) or {},
        "monitor": monitor_status(
            RuntimeStateStore(db).get(f"github-monitor-health:{settings.dev_user_id}") or {},
            enabled=settings.github_background_monitor_enabled,
            poll_seconds=settings.github_background_poll_seconds,
        ),
        "background_monitor_configured": settings.github_background_monitor_enabled,
        "poll_seconds": settings.github_background_poll_seconds,
    }


@router.get("/github/profile")
def github_profile(db: Session = Depends(get_db)) -> dict:
    try:
        return get_github_client(db, get_settings()).get_user()
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"GitHub unavailable: {type(exc).__name__}: {exc}") from exc


@router.get("/github/repos")
def github_repositories(
    limit: int = Query(default=30, ge=1, le=100),
    db: Session = Depends(get_db),
) -> dict:
    try:
        return {"items": get_github_client(db, get_settings()).list_repositories(limit=limit)}
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"GitHub unavailable: {type(exc).__name__}: {exc}") from exc


@router.get("/github/repos/{owner}/{repo}/workflow-runs")
def github_workflow_runs(
    owner: str,
    repo: str,
    limit: int = Query(default=20, ge=1, le=100),
    status: str | None = Query(default=None),
    db: Session = Depends(get_db),
) -> dict:
    try:
        items = get_github_client(db, get_settings()).list_workflow_runs(
            owner=owner,
            repo=repo,
            limit=limit,
            status=status,
        )
        return {"items": items}
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"GitHub unavailable: {type(exc).__name__}: {exc}") from exc


@router.get("/github/repos/{owner}/{repo}/issues")
def github_issues(
    owner: str,
    repo: str,
    limit: int = Query(default=30, ge=1, le=100),
    state: str = Query(default="open"),
    db: Session = Depends(get_db),
) -> dict:
    try:
        return {
            "items": get_github_client(db, get_settings()).list_issues(
                owner=owner,
                repo=repo,
                limit=limit,
                state=state,
            )
        }
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"GitHub unavailable: {type(exc).__name__}: {exc}") from exc


@router.get("/github/repos/{owner}/{repo}/pulls")
def github_pulls(owner: str, repo: str, db: Session = Depends(get_db)) -> dict:
    client = None
    try:
        client = get_github_client(db, get_settings())
        return {"items": client.list_pulls(owner=owner, repo=repo)}
    except Exception as exc:
        status, detail = github_error(exc)
        raise HTTPException(status_code=status, detail=detail) from exc
    finally:
        if client is not None:
            client.close()


@router.get("/github/repos/{owner}/{repo}/branches")
def github_branches(owner: str, repo: str, db: Session = Depends(get_db)) -> dict:
    client = None
    try:
        client = get_github_client(db, get_settings())
        return {"items": client.list_branches(owner=owner, repo=repo)}
    except Exception as exc:
        status, detail = github_error(exc)
        raise HTTPException(status_code=status, detail=detail) from exc
    finally:
        if client is not None:
            client.close()


@router.post("/runtime/github/poll")
def poll_github(db: Session = Depends(get_db)) -> dict:
    from app.runtime.github_runtime import GitHubRuntimeService
    try:
        settings = get_settings()
        result = GitHubRuntimeService(db=db, settings=settings).poll()
        errors = result.get("errors") or []
        # Keep manual diagnostics visible without claiming a background worker ran.
        RuntimeStateStore(db).set(f"github-monitor-health:{settings.dev_user_id}", {
            "poll_source": "manual",
            "last_poll_at": datetime.now(UTC).isoformat(),
            "last_status": "ERROR" if errors else "OK", "last_decision": result["decision"],
            "last_error": "; ".join(error["detail"] for error in errors)[:1000] if errors else None,
        })
        return result
    except Exception as exc:
        status, detail = github_error(exc)
        raise HTTPException(status_code=status, detail=detail) from exc


class GitHubIssueRequest(BaseModel):
    title: str = Field(min_length=1, max_length=256)
    body: str = ""


class GitHubBranchRequest(BaseModel):
    new_branch: str = Field(min_length=1, max_length=240)
    from_ref: str = "main"

    @field_validator("new_branch", "from_ref")
    @classmethod
    def valid_ref(cls, value: str) -> str:
        return validate_branch(value)


class GitHubWorkflowDispatchRequest(BaseModel):
    workflow_id: str = Field(min_length=1, max_length=255)
    ref: str = Field(default="main", min_length=1, max_length=255)
    inputs: dict[str, str] = Field(default_factory=dict)


def _require_external_actions() -> None:
    if not get_settings().enable_external_actions:
        raise HTTPException(status_code=403, detail="ENABLE_EXTERNAL_ACTIONS is false")


@router.post("/github/repos/{owner}/{repo}/issues", status_code=201)
def create_github_issue(
    owner: str,
    repo: str,
    request: GitHubIssueRequest,
    db: Session = Depends(get_db),
) -> dict:
    _require_external_actions()
    try:
        return get_github_client(db, get_settings()).create_issue(
            owner=owner,
            repo=repo,
            title=request.title,
            body=request.body,
        )
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"GitHub action failed: {type(exc).__name__}: {exc}") from exc


@router.post("/github/repos/{owner}/{repo}/branches", status_code=201)
def create_github_branch(
    owner: str,
    repo: str,
    request: GitHubBranchRequest,
    db: Session = Depends(get_db),
) -> dict:
    _require_external_actions()
    client = None
    operation = "source_branch"
    try:
        client = get_github_client(db, get_settings())
        source = client.get_branch_ref(owner=owner, repo=repo, branch=request.from_ref)
        sha = str((source.get("object") or {}).get("sha") or "")
        if not sha:
            raise HTTPException(status_code=502, detail="GitHub returned no commit for the source branch. Refresh branches and try again.")
        operation = "create_branch"
        return client.create_branch_at_sha(owner=owner, repo=repo, branch=request.new_branch, sha=sha)
    except HTTPException:
        raise
    except Exception as exc:
        status, detail = github_error(exc, operation=operation)
        raise HTTPException(status_code=status, detail=detail) from exc
    finally:
        if client is not None:
            client.close()


@router.post("/github/repos/{owner}/{repo}/workflow-dispatch", status_code=202)
def dispatch_github_workflow(
    owner: str,
    repo: str,
    request: GitHubWorkflowDispatchRequest,
    db: Session = Depends(get_db),
) -> dict:
    _require_external_actions()
    try:
        get_github_client(db, get_settings()).dispatch_workflow(
            owner=owner,
            repo=repo,
            workflow_id=request.workflow_id,
            ref=request.ref,
            inputs=request.inputs,
        )
        return {"dispatched": True, "workflow_id": request.workflow_id, "ref": request.ref}
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"GitHub action failed: {type(exc).__name__}: {exc}") from exc
