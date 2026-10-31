import threading
from typing import Annotated
from uuid import uuid4

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.integrations.github.connection import get_github_client
from app.integrations.github.errors import github_error
from app.integrations.github.validation import repository_parts
from app.providers.hermes import HermesClient, HermesProblem, briefing_configuration
from app.services.developer_briefing import collect_snapshot

router = APIRouter(prefix="/github/briefing", tags=["developer-briefing"])
# Pilot: one API process, one owner and one briefing at a time, not a durable job queue.
briefing_lock = threading.Lock()


class BriefingRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    repositories: list[str] = Field(min_length=1, max_length=3)

    @field_validator("repositories")
    @classmethod
    def valid_repositories(cls, repositories):
        for repository in repositories:
            if len(repository) > 200:
                raise ValueError("Repository name is too long")
            repository_parts(repository)
        if len({repository.lower() for repository in repositories}) != len(repositories):
            raise ValueError("Choose each repository once")
        return repositories


@router.get("/status")
def status():
    problem = briefing_configuration(get_settings())
    return {
        "configured": problem is None,
        "message": problem
        or "Hermes is configured. Its dedicated profile is checked before each briefing.",
        "max_repositories": 3,
    }


@router.post("")
def create_briefing(request: BriefingRequest, db: Annotated[Session, Depends(get_db)]):
    settings = get_settings()
    problem = briefing_configuration(settings)
    if problem:
        raise HTTPException(503, problem)
    if not briefing_lock.acquire(blocking=False):
        raise HTTPException(409, "A briefing is already running. Wait before starting another.")
    hermes = github = None
    try:
        hermes = HermesClient(settings)
        hermes.check_read_only_profile()
        try:
            github = get_github_client(db, settings)  # Identity comes from the signed-in session.
            github.client.timeout = httpx.Timeout(8.0)
        except (httpx.HTTPError, RuntimeError, ValueError) as exc:
            code, detail = github_error(exc)
            raise HTTPException(code, detail) from exc
        snapshot = collect_snapshot(github, request.repositories)
        # No inference charge when every source failed or the selected repos have no activity.
        if not snapshot["sources"]:
            return {
                **snapshot,
                "run_id": None,
                "provider": "none",
                "summary": "No activity was available to summarize. Review the coverage details. No Hermes briefing was requested.",
            }
        run_id = str(uuid4())
        summary = hermes.brief(snapshot, run_id)
        return {**snapshot, "run_id": run_id, "provider": "hermes", "summary": summary}
    except HermesProblem as exc:
        raise HTTPException(502, str(exc)) from exc
    finally:
        try:
            if github:
                github.close()
        finally:
            try:
                if hermes:
                    hermes.close()
            finally:
                briefing_lock.release()
