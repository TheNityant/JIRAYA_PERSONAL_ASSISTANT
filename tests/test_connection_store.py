from datetime import datetime, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.db import Base
from app.services.connection_store import ConnectionStore


def test_connection_can_be_created_and_disconnected():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)

    with Session(engine) as db:
        store = ConnectionStore(db)
        store.ensure_user(user_id="user-1", email="user@example.com")
        connection = store.upsert(
            user_id="user-1",
            provider="google",
            provider_account_id="user@gmail.com",
            display_name="user@gmail.com",
            scopes=["scope-a"],
            credential_ciphertext="encrypted",
            token_expires_at=datetime.now(timezone.utc),
            metadata={"gmail_history_id": "123"},
        )

        assert store.get_active(user_id="user-1", provider="google").id == connection.id
        disconnected = store.disconnect(user_id="user-1", provider="google")
        assert disconnected is not None
        assert disconnected.status == "DISCONNECTED"
        assert disconnected.credential_ciphertext == ""
        assert store.get_active(user_id="user-1", provider="google") is None


def test_oauth_state_is_single_use():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)

    with Session(engine) as db:
        store = ConnectionStore(db)
        state = store.create_oauth_state(user_id="user-1", provider="google")
        first = store.consume_oauth_state(state=state.state, provider="google")
        second = store.consume_oauth_state(state=state.state, provider="google")

        assert first is not None
        assert first.user_id == "user-1"
        assert second is None
