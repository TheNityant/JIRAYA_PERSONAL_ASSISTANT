import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Connection, OAuthState, User


class ConnectionStore:
    def __init__(self, db: Session):
        self.db = db

    def ensure_user(self, *, user_id: str, email: str, display_name: str = "Local user") -> User:
        user = self.db.get(User, user_id)
        if user is None:
            user = User(id=user_id, email=email, display_name=display_name)
            self.db.add(user)
            self.db.commit()
            self.db.refresh(user)
        return user

    def list_for_user(self, user_id: str) -> list[Connection]:
        statement = (
            select(Connection)
            .where(Connection.user_id == user_id)
            .order_by(Connection.updated_at.desc())
        )
        return list(self.db.scalars(statement).all())

    def get_active(self, *, user_id: str, provider: str) -> Connection | None:
        statement = (
            select(Connection)
            .where(Connection.user_id == user_id)
            .where(Connection.provider == provider)
            .where(Connection.status == "CONNECTED")
            .order_by(Connection.updated_at.desc())
            .limit(1)
        )
        return self.db.scalars(statement).first()

    def get_active_account(self, *, provider: str, provider_account_id: str) -> Connection | None:
        statement = (
            select(Connection)
            .where(Connection.provider == provider)
            .where(Connection.provider_account_id == provider_account_id)
            .where(Connection.status == "CONNECTED")
            .order_by(Connection.updated_at.desc())
            .limit(1)
        )
        return self.db.scalars(statement).first()

    def upsert(
        self,
        *,
        user_id: str,
        provider: str,
        provider_account_id: str,
        display_name: str,
        scopes: list[str],
        credential_ciphertext: str,
        token_expires_at: datetime | None,
        metadata: dict | None = None,
    ) -> Connection:
        statement = (
            select(Connection)
            .where(Connection.user_id == user_id)
            .where(Connection.provider == provider)
            .where(Connection.provider_account_id == provider_account_id)
            .limit(1)
        )
        connection = self.db.scalars(statement).first()
        now = datetime.now(timezone.utc)
        if connection is None:
            connection = Connection(
                user_id=user_id,
                provider=provider,
                provider_account_id=provider_account_id,
                display_name=display_name,
                status="CONNECTED",
                scopes=scopes,
                credential_ciphertext=credential_ciphertext,
                connection_metadata=metadata or {},
                token_expires_at=token_expires_at,
                created_at=now,
                updated_at=now,
            )
        else:
            connection.display_name = display_name
            connection.status = "CONNECTED"
            connection.scopes = scopes
            connection.credential_ciphertext = credential_ciphertext
            connection.connection_metadata = metadata or connection.connection_metadata or {}
            connection.token_expires_at = token_expires_at
            connection.updated_at = now
        self.db.add(connection)
        self.db.commit()
        self.db.refresh(connection)
        return connection

    def update_credentials(
        self,
        connection: Connection,
        *,
        credential_ciphertext: str,
        scopes: list[str],
        token_expires_at: datetime | None,
    ) -> Connection:
        connection.credential_ciphertext = credential_ciphertext
        connection.scopes = scopes
        connection.token_expires_at = token_expires_at
        connection.updated_at = datetime.now(timezone.utc)
        self.db.add(connection)
        self.db.commit()
        self.db.refresh(connection)
        return connection

    def update_metadata(self, connection: Connection, values: dict) -> Connection:
        connection.connection_metadata = {**(connection.connection_metadata or {}), **values}
        connection.updated_at = datetime.now(timezone.utc)
        self.db.add(connection)
        self.db.commit()
        self.db.refresh(connection)
        return connection

    def disconnect(self, *, user_id: str, provider: str) -> Connection | None:
        connection = self.get_active(user_id=user_id, provider=provider)
        if connection is None:
            return None
        connection.status = "DISCONNECTED"
        connection.credential_ciphertext = ""
        connection.updated_at = datetime.now(timezone.utc)
        self.db.add(connection)
        self.db.commit()
        self.db.refresh(connection)
        return connection

    def create_oauth_state(self, *, user_id: str, provider: str, ttl_minutes: int = 10) -> OAuthState:
        state = OAuthState(
            state=secrets.token_urlsafe(48),
            user_id=user_id,
            provider=provider,
            created_at=datetime.now(timezone.utc),
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=ttl_minutes),
        )
        self.db.add(state)
        self.db.commit()
        self.db.refresh(state)
        return state

    def consume_oauth_state(self, *, state: str, provider: str) -> OAuthState | None:
        record = self.db.get(OAuthState, state)
        if record is None or record.provider != provider:
            return None

        expires_at = record.expires_at
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        valid = expires_at > datetime.now(timezone.utc)

        self.db.delete(record)
        self.db.commit()
        return record if valid else None
