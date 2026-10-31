"""Request/background identity. Provider credentials never come from browser input."""

from contextlib import contextmanager
from contextvars import ContextVar

identity: ContextVar[tuple[str, str] | None] = ContextVar("jiraya_identity", default=None)


def current_user_id() -> str:
    current = identity.get()
    if current:
        return current[0]
    from app.config import get_base_settings

    return get_base_settings().dev_user_id


@contextmanager
def account_scope(user_id: str, email: str):
    token = identity.set((user_id, email))
    try:
        yield
    finally:
        identity.reset(token)
