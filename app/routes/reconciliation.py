from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.integrations.gmail.client import GmailClient
from app.providers.nebius import NebiusProvider
from app.services.action_store import ActionStore
from app.workflows.gmail_reconciliation import GmailReconciliationWorkflow

router = APIRouter(tags=["actions", "reconciliation"])


class GmailReconciliationRequest(BaseModel):
    action_id: str | None = None


@router.get("/actions")
def list_actions(db: Session = Depends(get_db)) -> list[dict]:
    actions = ActionStore(db).list_recent()
    return [
        {
            "id": action.id,
            "action_type": action.action_type,
            "source": action.source,
            "source_entity_id": action.source_entity_id,
            "correlation_id": action.correlation_id,
            "external_thread_id": action.external_thread_id,
            "initial_status": action.initial_status,
            "current_status": action.current_status,
            "context": action.context,
            "verification_evidence": action.verification_evidence,
            "last_reconciliation": action.last_reconciliation,
            "executed_at": action.executed_at,
            "verified_at": action.verified_at,
            "last_reconciled_at": action.last_reconciled_at,
        }
        for action in actions
    ]


@router.post("/reconciliation/gmail/run")
def reconcile_gmail(
    request: GmailReconciliationRequest,
    db: Session = Depends(get_db),
) -> dict:
    settings = get_settings()
    try:
        workflow = GmailReconciliationWorkflow(
            db=db,
            provider=NebiusProvider(settings),
            gmail=GmailClient(settings),
        )
        return workflow.run(action_id=request.action_id)
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Reconciliation failed: {type(exc).__name__}",
        ) from exc
