from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Automation


DEFAULT_SYSTEM_PIPELINE = [
    {"type": "TRIGGER", "label": "Trigger"},
    {"type": "MATCH", "label": "Semantic / deterministic match"},
    {"type": "ACTION", "label": "Action"},
    {"type": "APPROVAL", "label": "Approval policy"},
    {"type": "VERIFIER", "label": "Verifier"},
    {"type": "RECONCILIATOR", "label": "Reconciliator"},
    {"type": "OBLIGATION", "label": "Obligation lifecycle"},
]


class AutomationStore:
    def __init__(self, db: Session):
        self.db = db

    def create(
        self,
        *,
        name: str,
        description: str,
        enabled: bool,
        source: str,
        trigger_type: str,
        match_config: dict,
        context_config: dict,
        action_config: dict,
        approval_policy: str,
    ) -> Automation:
        now = datetime.now(timezone.utc)
        automation = Automation(
            name=name,
            description=description,
            enabled=enabled,
            source=source,
            trigger_type=trigger_type,
            match_config=match_config,
            context_config=context_config,
            action_config=action_config,
            approval_policy=approval_policy,
            system_pipeline=DEFAULT_SYSTEM_PIPELINE,
            created_at=now,
            updated_at=now,
        )
        self.db.add(automation)
        self.db.commit()
        self.db.refresh(automation)
        return automation

    def get(self, automation_id: str) -> Automation | None:
        return self.db.get(Automation, automation_id)

    def list(self, *, source: str | None = None, enabled: bool | None = None) -> list[Automation]:
        statement = select(Automation)
        if source:
            statement = statement.where(Automation.source == source)
        if enabled is not None:
            statement = statement.where(Automation.enabled == enabled)
        statement = statement.order_by(Automation.updated_at.desc())
        return list(self.db.scalars(statement).all())

    def update(self, automation: Automation, values: dict) -> Automation:
        for key in (
            "name",
            "description",
            "enabled",
            "source",
            "trigger_type",
            "match_config",
            "context_config",
            "action_config",
            "approval_policy",
        ):
            if key in values:
                setattr(automation, key, values[key])
        automation.updated_at = datetime.now(timezone.utc)
        self.db.add(automation)
        self.db.commit()
        self.db.refresh(automation)
        return automation

    def delete(self, automation: Automation) -> None:
        self.db.delete(automation)
        self.db.commit()
