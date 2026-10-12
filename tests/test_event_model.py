from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.db import Base
from app.models import Event


def test_event_can_be_persisted():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        event = Event(
            source="test",
            event_type="SMOKE_TEST",
            entity_id="project:jiraya",
            correlation_id="test-001",
            payload={"status": "ok"},
        )
        db.add(event)
        db.commit()
        saved = db.scalar(select(Event).where(Event.correlation_id == "test-001"))
    assert saved is not None
    assert saved.payload["status"] == "ok"
