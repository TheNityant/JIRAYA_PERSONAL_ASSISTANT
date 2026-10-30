from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.integrations.gmail.connection import get_gmail_client
from app.providers.nebius import NebiusProvider
from app.services.connection_store import ConnectionStore
from app.workflows.internship_inquiry import InternshipInquiryWorkflow

router = APIRouter(tags=["gmail", "workflows"])


@router.get("/gmail/inbox/recent")
def recent_inbox(
    db: Annotated[Session, Depends(get_db)], limit: int = Query(default=10, ge=1, le=20)
):
    try:
        gmail = get_gmail_client(db, get_settings())
        rows = [
            gmail.get_message(ident)
            for ident in gmail.list_message_ids(query="in:inbox", max_results=limit)
        ]
        return {"items": [
            {"id": row.id, "subject": row.subject, "sender": row.sender}
            for row in sorted(rows, key=lambda row: row.internal_date_ms, reverse=True)
        ]}
    except Exception as exc:
        raise HTTPException(
            status_code=503, detail=f"Could not load inbox messages: {type(exc).__name__}"
        ) from exc


class InternshipWorkflowRequest(BaseModel):
    message_id: str | None = None
    dry_run: bool = True


@router.get("/gmail/status")
def gmail_status(db: Session = Depends(get_db)) -> dict:
    settings = get_settings()
    connection = ConnectionStore(db).get_active(
        user_id=settings.dev_user_id,
        provider="google",
    )
    response = {
        "connected": False,
        "connection_mode": "database" if connection else "legacy-local",
        "email_address": connection.provider_account_id if connection else None,
        "credentials_present": Path(settings.gmail_credentials_path).exists(),
        "token_present": Path(settings.gmail_token_path).exists(),
    }

    try:
        client = get_gmail_client(db, settings)
        profile = client.get_profile()
        response["connected"] = True
        response["email_address"] = profile.get("emailAddress")
    except Exception as exc:
        response["error"] = type(exc).__name__
    return response


@router.get("/gmail/messages/latest")
def latest_unread_message(db: Session = Depends(get_db)) -> dict:
    settings = get_settings()
    try:
        message = get_gmail_client(db, settings).get_latest_unread_inbox()
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Gmail unavailable: {type(exc).__name__}") from exc
    if message is None:
        return {"message": None}
    return {
        "message": {
            "id": message.id,
            "thread_id": message.thread_id,
            "from": message.sender,
            "subject": message.subject,
            "snippet": message.snippet,
            "body_preview": message.body[:1500],
        }
    }


@router.post("/workflows/internship-inquiry/run")
def run_internship_workflow(
    request: InternshipWorkflowRequest,
    db: Session = Depends(get_db),
) -> dict:
    settings = get_settings()
    try:
        workflow = InternshipInquiryWorkflow(
            settings=settings,
            db=db,
            provider=NebiusProvider(settings),
            gmail=get_gmail_client(db, settings),
        )
        return workflow.run(message_id=request.message_id, dry_run=request.dry_run)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Workflow failed: {type(exc).__name__}") from exc
