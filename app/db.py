from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import get_settings


class Base(DeclarativeBase):
    pass


settings = get_settings()

engine_kwargs: dict = {"pool_pre_ping": True}
if settings.database_url.startswith("sqlite") and ":memory:" in settings.database_url:
    # An in-memory SQLite database normally belongs to one connection. FastAPI's
    # TestClient can create tables during lifespan on one connection and service the
    # request on another, which makes the second connection look like an empty database.
    # StaticPool keeps one shared connection for the whole test process, and
    # check_same_thread=False allows TestClient worker threads to use it safely.
    engine_kwargs.update(
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

engine = create_engine(settings.database_url, **engine_kwargs)
SessionLocal = sessionmaker(bind=engine, class_=Session, autoflush=False, expire_on_commit=False)


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    from app.models import Action, Automation, Event, Obligation  # noqa: F401

    Base.metadata.create_all(bind=engine)
