from datetime import datetime, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.db import Base
from app.models import Action
from app.services.obligation_store import ObligationStore


def make_action() -> Action:
    now = datetime.now(timezone.utc)
    return Action(
        id="action-obligation-1",
        action_type="EMAIL_REPLY",
        source="gmail",
        source_entity_id="gmail:source-1",
        correlation_id="corr-obligation-1",
        external_thread_id="thread-1",
        initial_status="VERIFIED_SUCCESS",
        current_status="VERIFIED_SUCCESS",
        context={"source_message_id": "source-1", "sent_message_id": "sent-1"},
        verification_evidence={},
        last_reconciliation={},
        executed_at=now,
        verified_at=now,
    )


def test_reconciliation_updates_one_open_obligation():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)

    with Session(engine) as db:
        action = make_action()
        db.add(action)
        db.commit()
        store = ObligationStore(db)

        first = store.upsert_from_reconciliation(
            action=action,
            classification="NEW_OBLIGATION",
            reason="Interview availability requested",
            message_id="incoming-1",
            message_internal_date_ms=1000,
            evidence={"message_id": "incoming-1"},
        )
        second = store.upsert_from_reconciliation(
            action=action,
            classification="SUPERSEDED_LATER",
            reason="Interview time changed",
            message_id="incoming-2",
            message_internal_date_ms=2000,
            evidence={"message_id": "incoming-2"},
        )

        assert second.id == first.id
        assert second.status == "OPEN"
        assert second.obligation_type == "SUPERSEDED_LATER"
        assert second.trigger_message_id == "incoming-2"


def test_obligation_can_be_resolved_by_user():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)

    with Session(engine) as db:
        action = make_action()
        db.add(action)
        db.commit()
        store = ObligationStore(db)
        obligation = store.upsert_from_reconciliation(
            action=action,
            classification="NEW_OBLIGATION",
            reason="Reply with availability",
            message_id="incoming-1",
            message_internal_date_ms=1000,
            evidence={"message_id": "incoming-1"},
        )

        resolved = store.resolve(
            obligation=obligation,
            status="RESOLVED_BY_USER",
            resolved_at=datetime.now(timezone.utc),
            message_id="manual-sent-1",
            reason="User replied manually",
            evidence={"message_id": "manual-sent-1"},
        )

        assert resolved.status == "RESOLVED_BY_USER"
        assert resolved.resolution["message_id"] == "manual-sent-1"
        assert store.open_for_action(action.id) is None


def test_replay_does_not_reopen_resolved_trigger():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)

    with Session(engine) as db:
        action = make_action()
        db.add(action)
        db.commit()
        store = ObligationStore(db)

        obligation = store.upsert_from_reconciliation(
            action=action,
            classification="NEW_OBLIGATION",
            reason="Reply with availability",
            message_id="incoming-1",
            message_internal_date_ms=1000,
            evidence={"message_id": "incoming-1"},
        )
        resolved = store.resolve(
            obligation=obligation,
            status="RESOLVED_BY_USER",
            resolved_at=datetime.now(timezone.utc),
            message_id="manual-sent-1",
            reason="User replied manually",
            evidence={"message_id": "manual-sent-1"},
        )

        replayed = store.upsert_from_reconciliation(
            action=action,
            classification="NEW_OBLIGATION",
            reason="Reply with availability",
            message_id="incoming-1",
            message_internal_date_ms=1000,
            evidence={"message_id": "incoming-1"},
        )

        assert replayed.id == resolved.id
        assert replayed.status == "RESOLVED_BY_USER"
        assert store.open_for_action(action.id) is None
