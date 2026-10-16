from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import AutomationExecution


class AutomationExecutionStore:
    def __init__(self, db: Session):
        self.db = db

    def get(self, execution_id: str) -> AutomationExecution | None:
        return self.db.get(AutomationExecution, execution_id)

    def latest_for_event(self, *, automation_id: str, source_event_id: str) -> AutomationExecution | None:
        statement = (
            select(AutomationExecution)
            .where(AutomationExecution.automation_id == automation_id)
            .where(AutomationExecution.source_event_id == source_event_id)
            .order_by(AutomationExecution.created_at.desc())
            .limit(1)
        )
        return self.db.scalars(statement).first()

    def create(
        self,
        *,
        automation_id: str,
        correlation_id: str,
        source_event_id: str,
        source_thread_id: str | None,
        action_type: str,
        approval_policy: str,
        status: str,
        match_result: dict,
        draft_body: str = "",
        output: dict | None = None,
    ) -> AutomationExecution:
        now = datetime.now(timezone.utc)
        execution = AutomationExecution(
            automation_id=automation_id,
            correlation_id=correlation_id,
            source="gmail",
            source_event_id=source_event_id,
            source_thread_id=source_thread_id,
            action_type=action_type,
            approval_policy=approval_policy,
            status=status,
            match_result=match_result,
            draft_body=draft_body,
            output=output or {},
            created_at=now,
            updated_at=now,
        )
        self.db.add(execution)
        self.db.commit()
        self.db.refresh(execution)
        return execution

    def update(
        self,
        execution: AutomationExecution,
        *,
        status: str | None = None,
        draft_body: str | None = None,
        output: dict | None = None,
        action_id: str | None = None,
        executed_at: datetime | None = None,
    ) -> AutomationExecution:
        if status is not None:
            execution.status = status
        if draft_body is not None:
            execution.draft_body = draft_body
        if output is not None:
            execution.output = output
        if action_id is not None:
            execution.action_id = action_id
        if executed_at is not None:
            execution.executed_at = executed_at
        execution.updated_at = datetime.now(timezone.utc)
        self.db.add(execution)
        self.db.commit()
        self.db.refresh(execution)
        return execution

    def list_recent(self, *, automation_id: str | None = None, limit: int = 50) -> list[AutomationExecution]:
        statement = select(AutomationExecution)
        if automation_id:
            statement = statement.where(AutomationExecution.automation_id == automation_id)
        statement = statement.order_by(AutomationExecution.created_at.desc()).limit(limit)
        return list(self.db.scalars(statement).all())
