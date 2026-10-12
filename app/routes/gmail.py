from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.integrations.gmail.client import GmailClient
from app.providers.nebius import NebiusProvider
from app.workflows.internship_inquiry import InternshipInquiryWorkflow

router = APIRouter(tags=["gmail", "workflows"])


class InternshipWorkflowRequest(BaseModel):
    message_id: str | None = None
    dry_run: bool = True


@router.get("/gmail/status")
def gmail_status() -> dict:
    settings = get_settings()
    credentials_present = Path(settings.gmail_credentials_path).exists()
    token_present = Path(settings.gmail_token_path).exists()
    response = {
        "credentials_present": credentials_present,
        "token_present": token_present,
        "connected": False,
        "email_address": None,
    }
    if not token_present:
        return response

    try:
        client = GmailClient(settings)
        profile = client.get_profile()
        response["connected"] = True
        response["email_address"] = profile.get("emailAddress")
    except Exception as exc:
        response["error"] = type(exc).__name__
    return response


@router.get("/gmail/messages/latest")
def latest_unread_message() -> dict:
    settings = get_settings()
    try:
        message = GmailClient(settings).get_latest_unread_inbox()
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
        gmail = GmailClient(settings)
        provider = NebiusProvider(settings)
        workflow = InternshipInquiryWorkflow(
            settings=settings,
            db=db,
            provider=provider,
            gmail=gmail,
        )
        return workflow.run(message_id=request.message_id, dry_run=request.dry_run)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Workflow failed: {type(exc).__name__}") from exc
