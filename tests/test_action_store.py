from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.db import Base
from app.models import Event
from app.services.action_store import ActionStore


def test_backfills_verified_email_action_from_phase1_events():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    now = datetime.now(timezone.utc)

    with Session(engine) as db:
        db.add_all(
            [
                Event(
                    source="gmail",
                    event_type="EMAIL_OBSERVED",
                    entity_id="gmail:source-1",
                    correlation_id="corr-1",
                    payload={"message_id": "source-1", "thread_id": "thread-1"},
                    occurred_at=now,
                ),
                Event(
                    source="gmail",
                    event_type="EMAIL_REPLY_SENT",
                    entity_id="gmail:source-1",
                    correlation_id="corr-1",
                    payload={"sent_message_id": "sent-1", "thread_id": "thread-1"},
                    occurred_at=now + timedelta(seconds=1),
                ),
                Event(
                    source="verifier",
                    event_type="EMAIL_REPLY_VERIFIED",
                    entity_id="gmail:source-1",
                    correlation_id="corr-1",
                    payload={
                        "sent_message_id": "sent-1",
                        "observed_thread_id": "thread-1",
                        "sent_label_present": True,
                        "recipient_matches": True,
                    },
                    occurred_at=now + timedelta(seconds=2),
                ),
            ]
        )
        db.commit()

        action = ActionStore(db).latest_verified_email_action()

        assert action is not None
        assert action.action_type == "EMAIL_REPLY"
        assert action.initial_status == "VERIFIED_SUCCESS"
        assert action.current_status == "VERIFIED_SUCCESS"
        assert action.external_thread_id == "thread-1"
        assert action.context["source_message_id"] == "source-1"
        assert action.context["sent_message_id"] == "sent-1"
