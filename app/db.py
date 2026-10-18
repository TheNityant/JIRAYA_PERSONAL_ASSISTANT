from collections.abc import Generator

from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker, with_loader_criteria
from sqlalchemy.pool import StaticPool

from app.config import get_settings


class Base(DeclarativeBase):
    pass


settings = get_settings()

engine_kwargs: dict = {"pool_pre_ping": True}
if settings.database_url.startswith("sqlite") and ":memory:" in settings.database_url:
    # Share one in-memory SQLite connection across FastAPI TestClient threads.
    engine_kwargs.update(
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

engine = create_engine(settings.database_url, **engine_kwargs)


class TenantSession(Session):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from app.security.identity import identity

        principal = identity.get()
        self.info["tenant_id"] = principal[0] if principal else None

    def get(self, entity, ident, **kwargs):
        result = super().get(entity, ident, **kwargs)
        tenant = self.info.get("tenant_id")
        if tenant and hasattr(entity, "user_id") and entity.__name__ != "AuthSession":
            return result if result is None or result.user_id == tenant else None
        return result


@event.listens_for(Session, "do_orm_execute")
def _tenant_queries(state):
    tenant = state.session.info.get("tenant_id")
    if tenant and (state.is_select or state.is_update or state.is_delete):
        from app.models import Connection, Notification, OAuthState
        from app.models import Event, Action, Obligation, Automation, AutomationExecution

        for model in (
            Event,
            Action,
            Obligation,
            Automation,
            AutomationExecution,
            Connection,
            Notification,
            OAuthState,
        ):
            state.statement = state.statement.options(
                with_loader_criteria(model, model.user_id == tenant, include_aliases=True)
            )


@event.listens_for(Session, "before_flush")
def _tenant_writes(session, *_):
    tenant = session.info.get("tenant_id")
    if not tenant:
        return
    from app.models import Owned, Connection, Notification, OAuthState

    for obj in session.new | session.dirty | session.deleted:
        if isinstance(obj, (Owned, Connection, Notification, OAuthState)):
            if obj in session.new and getattr(obj, "user_id", None) is None:
                obj.user_id = tenant
            if obj.user_id != tenant:
                raise PermissionError("Object does not belong to this account")


SessionLocal = sessionmaker(
    bind=engine, class_=TenantSession, autoflush=False, expire_on_commit=False
)


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    from app.models import (  # noqa: F401
        Action,
        Automation,
        AutomationExecution,
        Connection,
        Event,
        Notification,
        OAuthState,
        Obligation,
        RuntimeState,
        User,
    )

    from app.models import AuthSession  # noqa: F401

    # Additive upgrade retains the old local workspace under its original identity.
    with engine.begin() as connection:
        if connection.dialect.name == "postgresql":
            # Serialize additive upgrades when multiple API workers start together.
            connection.execute(text("SELECT pg_advisory_xact_lock(1246319425)"))
        inspector = inspect(connection)
        existing = set(inspector.get_table_names())
        legacy = settings.dev_user_id.replace("'", "''")
        for table in ("events", "actions", "obligations", "automations", "automation_executions"):
            if table in existing and "user_id" not in {
                c["name"] for c in inspector.get_columns(table)
            }:
                connection.execute(
                    text(
                        f"ALTER TABLE {table} ADD COLUMN user_id VARCHAR(64) NOT NULL DEFAULT '{legacy}'"
                    )
                )
                connection.execute(
                    text(f"CREATE INDEX IF NOT EXISTS ix_{table}_user_id ON {table} (user_id)")
                )
        if "users" in existing and "password_hash" not in {
            c["name"] for c in inspector.get_columns("users")
        }:
            connection.execute(text("ALTER TABLE users ADD COLUMN password_hash TEXT"))
        Base.metadata.create_all(bind=connection)
