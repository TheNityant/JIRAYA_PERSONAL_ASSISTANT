from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.automation.matcher import AutomationMatcher
from app.config import get_settings
from app.db import get_db
from app.integrations.gmail.connection import get_gmail_client
from app.integrations.github.validation import repository_parts, validate_branch
from app.models import Automation
from app.providers.nebius import NebiusProvider
from app.services.automation_store import AutomationStore

router = APIRouter(tags=["automations"])


class AutomationCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    description: str = Field(default="", max_length=1000)
    enabled: bool = True
    source: Literal["gmail", "google_calendar", "github"] = "gmail"
    trigger_type: Literal["NEW_EMAIL", "CALENDAR_CHANGED", "GITHUB_ISSUE_CHANGED", "GITHUB_PR_CHANGED", "GITHUB_WORKFLOW_CHANGED"] = "NEW_EMAIL"
    match_config: dict[str, Any] = Field(default_factory=dict)
    context_config: dict[str, Any] = Field(default_factory=dict)
    action_config: dict[str, Any] = Field(default_factory=dict)
    approval_policy: Literal["REQUIRED", "DRAFT_ONLY", "AUTO"] = "REQUIRED"


class AutomationUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=1000)
    enabled: bool | None = None
    source: Literal["gmail", "google_calendar", "github"] | None = None
    trigger_type: Literal["NEW_EMAIL", "CALENDAR_CHANGED", "GITHUB_ISSUE_CHANGED", "GITHUB_PR_CHANGED", "GITHUB_WORKFLOW_CHANGED"] | None = None
    match_config: dict[str, Any] | None = None
    context_config: dict[str, Any] | None = None
    action_config: dict[str, Any] | None = None
    approval_policy: Literal["REQUIRED", "DRAFT_ONLY", "AUTO"] | None = None


class GmailPreviewRequest(BaseModel):
    message_id: str | None = None


def _payload(automation: Automation) -> dict:
    return {
        "id": automation.id,
        "name": automation.name,
        "description": automation.description,
        "enabled": automation.enabled,
        "source": automation.source,
        "trigger_type": automation.trigger_type,
        "match_config": automation.match_config,
        "context_config": automation.context_config,
        "action_config": automation.action_config,
        "approval_policy": automation.approval_policy,
        "system_pipeline": automation.system_pipeline,
        "created_at": automation.created_at,
        "updated_at": automation.updated_at,
    }


@router.get("/automation-capabilities")
def automation_capabilities() -> dict:
    return {
        "sources": [
            {
                "id": "gmail",
                "label": "Gmail",
                "status": "AVAILABLE",
                "triggers": [{"id": "NEW_EMAIL", "label": "New incoming email"}],
            },
            {"id": "google_calendar", "label": "Google Calendar", "status": "AVAILABLE",
             "triggers": [{"id": "CALENDAR_CHANGED", "label": "Calendar event changed"}]},
            {"id": "github", "label": "GitHub", "status": "AVAILABLE", "triggers": [
                {"id": "GITHUB_ISSUE_CHANGED", "label": "Issue changed"},
                {"id": "GITHUB_PR_CHANGED", "label": "Pull request changed"},
                {"id": "GITHUB_WORKFLOW_CHANGED", "label": "Workflow run changed"},
            ]},
        ],
        "matchers": [
            {"id": "sender_contains", "label": "Sender contains", "kind": "DETERMINISTIC"},
            {"id": "subject_contains", "label": "Subject contains", "kind": "DETERMINISTIC"},
            {"id": "body_contains", "label": "Body contains", "kind": "DETERMINISTIC"},
            {"id": "semantic_condition", "label": "Semantic condition", "kind": "AI"},
        ],
        "actions": [
            {"id": "DRAFT_REPLY", "label": "Draft reply", "status": "CONFIGURABLE"},
            {"id": "NOTIFY", "label": "Notify me", "status": "CONFIGURABLE"},
            {"id": "CREATE_CALENDAR_EVENT", "label": "Create calendar event", "status": "CONFIGURABLE"},
            {"id": "CREATE_GITHUB_ISSUE", "label": "Create GitHub issue", "status": "CONFIGURABLE"},
            {"id": "CREATE_GITHUB_BRANCH", "label": "Create GitHub branch", "status": "CONFIGURABLE"},
        ],
        "approval_policies": ["DRAFT_ONLY", "REQUIRED", "AUTO"],
        "system_nodes": ["VERIFIER", "RECONCILIATOR", "OBLIGATION"],
        "coming_next": [],
    }


def _validate_logic(values: dict) -> None:
    source = values["source"]
    trigger = values["trigger_type"]
    action = (values.get("action_config") or {}).get("type", "DRAFT_REPLY")
    scheduling = (values.get("action_config") or {}).get("calendar_scheduling") or {}
    if not isinstance(scheduling, dict):
        raise HTTPException(422, "Calendar scheduling must be an object")
    if scheduling.get("enabled"):
        from app.automation.scheduling_reply import SchedulingPolicy
        try:
            SchedulingPolicy.model_validate(scheduling)
        except ValueError as exc:
            raise HTTPException(422, "Use valid scheduling timezone, working hours, weekdays, duration and calendars") from exc
        if source != "gmail" or action != "DRAFT_REPLY":
            raise HTTPException(422, "Calendar-backed scheduling is available only for Gmail replies")
    allowed = {("gmail", "NEW_EMAIL"), ("google_calendar", "CALENDAR_CHANGED"),
               ("github", "GITHUB_ISSUE_CHANGED"), ("github", "GITHUB_PR_CHANGED"),
               ("github", "GITHUB_WORKFLOW_CHANGED")}
    if (source, trigger) not in allowed:
        raise HTTPException(status_code=422, detail="Trigger does not belong to the selected source")
    if action not in {"DRAFT_REPLY", "NOTIFY", "CREATE_CALENDAR_EVENT", "CREATE_GITHUB_ISSUE", "CREATE_GITHUB_BRANCH"}:
        raise HTTPException(status_code=422, detail="Unsupported action")
    if source == "github" and action != "NOTIFY":
        raise HTTPException(status_code=422, detail="GitHub change triggers support Notify me")
    if source == "github" or action in {"CREATE_GITHUB_ISSUE", "CREATE_GITHUB_BRANCH"}:
        try:
            repository_parts(str((values.get("action_config") or {}).get("repository") or ""))
            if action == "CREATE_GITHUB_BRANCH":
                validate_branch(str(values["action_config"].get("branch") or ""))
                validate_branch(str(values["action_config"].get("from_ref") or "main"))
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    if source == "google_calendar" and action != "NOTIFY":
        raise HTTPException(status_code=422, detail="Calendar change triggers support Notify me")


@router.get("/automations")
def list_automations(
    source: str | None = Query(default=None),
    enabled: bool | None = Query(default=None),
    db: Session = Depends(get_db),
) -> list[dict]:
    return [_payload(item) for item in AutomationStore(db).list(source=source, enabled=enabled)]


@router.post("/automations", status_code=201)
def create_automation(request: AutomationCreate, db: Session = Depends(get_db)) -> dict:
    _validate_logic(request.model_dump())
    automation = AutomationStore(db).create(**request.model_dump())
    return _payload(automation)


@router.put("/automations/{automation_id}")
def update_automation(
    automation_id: str,
    request: AutomationUpdate,
    db: Session = Depends(get_db),
) -> dict:
    store = AutomationStore(db)
    automation = store.get(automation_id)
    if automation is None:
        raise HTTPException(status_code=404, detail="Automation not found")
    changes = request.model_dump(exclude_unset=True)
    _validate_logic({**_payload(automation), **changes})
    return _payload(store.update(automation, changes))


@router.delete("/automations/{automation_id}", status_code=204)
def delete_automation(automation_id: str, db: Session = Depends(get_db)) -> None:
    store = AutomationStore(db)
    automation = store.get(automation_id)
    if automation is None:
        raise HTTPException(status_code=404, detail="Automation not found")
    store.delete(automation)


@router.post("/automations/presets/internship-assistant", status_code=201)
def create_internship_preset(db: Session = Depends(get_db)) -> dict:
    automation = AutomationStore(db).create(
        name="Internship Assistant",
        description="Watch Gmail for internship/recruiter follow-ups and prepare an appropriate reply.",
        enabled=True,
        source="gmail",
        trigger_type="NEW_EMAIL",
        match_config={
            "semantic_condition": (
                "The email is materially about an internship application, recruiter outreach, "
                "interview invitation, application update, or internship-related follow-up."
            ),
            "semantic_threshold": 0.78,
            "sender_contains": "",
            "subject_contains": "",
            "body_contains": "",
        },
        context_config={
            "instructions": "Use my stated availability and preferences when relevant. Do not invent facts."
        },
        action_config={
            "type": "DRAFT_REPLY",
            "instructions": "Draft a concise professional response tailored to the actual email.",
        },
        approval_policy="REQUIRED",
    )
    return _payload(automation)


@router.post("/automations/{automation_id}/preview/gmail")
def preview_gmail_match(
    automation_id: str,
    request: GmailPreviewRequest,
    db: Session = Depends(get_db),
) -> dict:
    settings = get_settings()
    store = AutomationStore(db)
    automation = store.get(automation_id)
    if automation is None:
        raise HTTPException(status_code=404, detail="Automation not found")
    if automation.source != "gmail":
        raise HTTPException(status_code=400, detail="Automation source is not Gmail")

    try:
        gmail = get_gmail_client(db, settings)
        message = (
            gmail.get_message(request.message_id)
            if request.message_id
            else gmail.get_latest_unread_inbox()
        )
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Gmail unavailable: {type(exc).__name__}") from exc

    if message is None:
        return {"automation": _payload(automation), "message": None, "match": None}

    result = AutomationMatcher(NebiusProvider(settings)).match_gmail(
        automation=automation,
        message=message,
    )
    return {
        "automation": _payload(automation),
        "message": {
            "id": message.id,
            "thread_id": message.thread_id,
            "from": message.sender,
            "subject": message.subject,
            "body_preview": message.body[:1500],
        },
        "match": {
            "matches": result.matches,
            "confidence": result.confidence,
            "reason": result.reason,
            "deterministic_checks": result.deterministic_checks,
        },
    }
