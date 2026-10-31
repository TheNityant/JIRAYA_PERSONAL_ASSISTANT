from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class SmokeRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)


class SmokeResponse(BaseModel):
    provider: str
    model: str
    content: str


class EventCreate(BaseModel):
    source: str = Field(min_length=1, max_length=100)
    event_type: str = Field(min_length=1, max_length=120)
    entity_id: str | None = Field(default=None, max_length=255)
    correlation_id: str | None = Field(default=None, max_length=255)
    payload: dict[str, Any] = Field(default_factory=dict)
    occurred_at: datetime | None = None


class EventRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    source: str
    event_type: str
    entity_id: str | None
    correlation_id: str | None
    payload: dict[str, Any]
    occurred_at: datetime
    recorded_at: datetime
