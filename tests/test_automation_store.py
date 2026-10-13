from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.db import Base
from app.services.automation_store import AutomationStore


def test_automation_store_create_update_and_list():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)

    with Session(engine) as db:
        store = AutomationStore(db)
        automation = store.create(
            name="Interview Watch",
            description="Watch for interview requests",
            enabled=True,
            source="gmail",
            trigger_type="NEW_EMAIL",
            match_config={"semantic_condition": "An employer asks to schedule an interview."},
            context_config={"instructions": "Prefer Friday after 1 PM"},
            action_config={"type": "DRAFT_REPLY"},
            approval_policy="REQUIRED",
        )

        assert automation.name == "Interview Watch"
        assert automation.system_pipeline[-1]["type"] == "OBLIGATION"
        assert store.list(source="gmail", enabled=True)[0].id == automation.id

        updated = store.update(automation, {"enabled": False, "name": "Interview Assistant"})
        assert updated.enabled is False
        assert updated.name == "Interview Assistant"
        assert store.list(enabled=True) == []
