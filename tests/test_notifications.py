from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.db import Base
from app.services.notification_store import NotificationStore


def test_notification_dedupe_and_read_state():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)

    with Session(engine) as db:
        store = NotificationStore(db)
        first = store.create_once(
            user_id="local-dev-user",
            category="AUTOMATION_EXECUTED",
            severity="SUCCESS",
            title="Reply sent",
            message="Gmail verified the send.",
            dedupe_key="sent:execution-1",
        )
        duplicate = store.create_once(
            user_id="local-dev-user",
            category="AUTOMATION_EXECUTED",
            severity="SUCCESS",
            title="Duplicate",
            message="Should not create a second row.",
            dedupe_key="sent:execution-1",
        )

        assert duplicate.id == first.id
        assert store.unread_count(user_id="local-dev-user") == 1

        store.mark_read(notification_id=first.id, user_id="local-dev-user")
        assert store.unread_count(user_id="local-dev-user") == 0
