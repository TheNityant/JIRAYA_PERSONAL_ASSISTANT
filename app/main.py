from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import get_settings
from app.db import init_db
from app.routes import ai, automations, events, gmail, health, reconciliation


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    yield


settings = get_settings()
app = FastAPI(
    title=settings.app_name,
    version="0.4.0",
    description=(
        "Phase-2C Jiraya API: user-configurable automations on top of verified actions, "
        "temporal reconciliation, and obligation lifecycle tracking."
    ),
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[item.strip() for item in settings.frontend_origins.split(",") if item.strip()],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(health.router)
app.include_router(ai.router)
app.include_router(events.router)
app.include_router(gmail.router)
app.include_router(reconciliation.router)
app.include_router(automations.router)


@app.get("/")
def root() -> dict:
    return {
        "name": settings.app_name,
        "version": "0.4.0",
        "phase": "phase-2c",
        "docs": "/docs",
    }
