from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.models import RuntimeState


class RuntimeStateStore:
    def __init__(self, db: Session):
        self.db = db

    def get(self, key: str) -> dict | None:
        row = self.db.get(RuntimeState, key)
        return dict(row.value or {}) if row else None

    def set(self, key: str, value: dict) -> RuntimeState:
        row = self.db.get(RuntimeState, key)
        now = datetime.now(timezone.utc)
        if row is None:
            row = RuntimeState(key=key, value=value, updated_at=now)
        else:
            row.value = value
            row.updated_at = now
        self.db.add(row)
        self.db.commit()
        self.db.refresh(row)
        return row

    def delete(self, key: str) -> bool:
        row = self.db.get(RuntimeState, key)
        if row is None:
            return False
        self.db.delete(row)
        self.db.commit()
        return True
