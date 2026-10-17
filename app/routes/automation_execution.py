from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.automation.reliable_executor import ReliableAutomationExecutor
from app.config import get_settings
from app.db import get_db
from app.integrations.gmail.connection import get_gmail_client
from app.providers.nebius import NebiusProvider
from app.services.automation_execution_store import AutomationExecutionStore
from app.services.automation_store import AutomationStore

router = APIRouter(tags=["automation-execution"])


class GmailRunRequest(BaseModel):
    message_id: str | None = None
    force: bool = False


class ApprovalRequest(BaseModel):
    draft_body: str | None = None


def _executor(db: Session, *, require_gmail: bool = True) -> ReliableAutomationExecutor:
    settings = get_settings()
    return ReliableAutomationExecutor(
        settings=settings,
        db=db,
        provider=NebiusProvider(settings),
        gmail=get_gmail_client(db, settings) if require_gmail else None,
    )


@router.post("/automations/{automation_id}/run/gmail")
def run_gmail_automation(
    automation_id: str,
    request: GmailRunRequest,
    db: Session = Depends(get_db),
) -> dict:
    automation = AutomationStore(db).get(automation_id)
    if automation is None:
        raise HTTPException(status_code=404, detail="Automation not found")
    try:
        return _executor(db).run(
            automation=automation,
            message_id=request.message_id,
            force=request.force,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Automation execution failed: {type(exc).__name__}",
        ) from exc


@router.post("/automation-executions/{execution_id}/approve")
def approve_automation_execution(
    execution_id: str,
    request: ApprovalRequest,
    db: Session = Depends(get_db),
) -> dict:
    execution = AutomationExecutionStore(db).get(execution_id)
    if execution is None:
        raise HTTPException(status_code=404, detail="Automation execution not found")
    try:
        return _executor(db, require_gmail=execution.action_type != "CREATE_CALENDAR_EVENT").approve(
            execution=execution, edited_draft=request.draft_body)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Approval failed: {type(exc).__name__}") from exc


@router.get("/automation-executions/{execution_id}")
def get_automation_execution(
    execution_id: str,
    db: Session = Depends(get_db),
) -> dict:
    execution = AutomationExecutionStore(db).get(execution_id)
    if execution is None:
        raise HTTPException(status_code=404, detail="Automation execution not found")
    return ReliableAutomationExecutor.execution_payload(execution)


@router.get("/automation-executions")
def list_automation_executions(
    automation_id: str | None = Query(default=None),
    db: Session = Depends(get_db),
) -> list[dict]:
    items = AutomationExecutionStore(db).list_recent(automation_id=automation_id)
    return [ReliableAutomationExecutor.execution_payload(item) for item in items]
