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
from app.providers.bharatcode import BharatCodeClient, BharatCodeProblem
from app.providers.bharatcode import briefing_configuration as bharatcode_configuration
from app.providers.hermes import HermesClient, HermesProblem, briefing_configuration
from app.services.developer_briefing import collect_snapshot

router = APIRouter(prefix="/github/briefing", tags=["developer-briefing"])
# One briefing per API process at a time; Hermes remains an owner-only pilot.
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


def configuration_problem(settings):
    if settings.github_briefing_provider == "bharatcode":
        return bharatcode_configuration(settings)
    return briefing_configuration(settings)


@router.get("/status")
def status():
    settings = get_settings()
    problem = configuration_problem(settings)
    return {
        "configured": problem is None,
        "provider": settings.github_briefing_provider,
        "message": problem
        or (
            "BharatCode is configured for read-only GitHub activity briefings."
            if settings.github_briefing_provider == "bharatcode"
            else "Hermes is configured. Its dedicated profile is checked before each briefing."
        ),
        "max_repositories": 3,
    }


@router.post("")
def create_briefing(request: BriefingRequest, db: Annotated[Session, Depends(get_db)]):
    settings = get_settings()
    problem = configuration_problem(settings)
    if problem:
        raise HTTPException(503, problem)
    if not briefing_lock.acquire(blocking=False):
        raise HTTPException(409, "A briefing is already running. Wait before starting another.")
    provider = github = None
    try:
        if settings.github_briefing_provider == "bharatcode":
            provider = BharatCodeClient(settings)
        else:
            provider = HermesClient(settings)
            provider.check_read_only_profile()
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
                "summary": "No activity was available to summarize. Review the coverage details. No AI briefing was requested.",
            }
        run_id = str(uuid4())
        summary = provider.brief(snapshot, run_id)
        return {
            **snapshot,
            "run_id": run_id,
            "provider": settings.github_briefing_provider,
            "summary": summary,
        }
    except (HermesProblem, BharatCodeProblem) as exc:
        raise HTTPException(502, str(exc)) from exc
    finally:
        try:
            if github:
                github.close()
        finally:
            try:
                if provider:
                    provider.close()
            finally:
                briefing_lock.release()
