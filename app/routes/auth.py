import base64
import hashlib
import hmac
import re
import secrets
import time
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.models import AuthSession, User
from app.security.identity import identity
from app.security.sessions import COOKIE_NAME, create_session, digest
from app.services.runtime_state_store import RuntimeStateStore

router = APIRouter(prefix="/auth", tags=["account"])


def password_hash(password: str, salt: bytes | None = None) -> str:
    salt = salt or secrets.token_bytes(16)
    derived = hashlib.scrypt(
        password.encode(), salt=salt, n=131072, r=8, p=1, maxmem=256 * 1024 * 1024
    )
    return base64.b64encode(salt).decode() + ":" + base64.b64encode(derived).decode()


def password_matches(password: str, stored: str | None) -> bool:
    if not stored:
        password_hash(password, b"jiraya-dummy-key!")
        return False
    try:
        salt = base64.b64decode(stored.split(":")[0])
        return hmac.compare_digest(password_hash(password, salt), stored)
    except (ValueError, TypeError):
        return False


class Credentials(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=12, max_length=128)

    @field_validator("email")
    @classmethod
    def email_address(cls, value):
        value = value.strip().casefold()
        if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", value):
            raise ValueError("Enter a valid email address")
        return value


class Registration(Credentials):
    display_name: str = Field(min_length=1, max_length=255)


def public_user(user):
    return {"id": user.id, "email": user.email, "display_name": user.display_name}


def rate_limit(db, request):
    address = request.client.host if request.client else "unknown"
    key = "auth-attempts:" + digest(address)
    store = RuntimeStateStore(db)
    value = store.get(key) or {"start": time.time(), "count": 0}
    if time.time() - value["start"] > 900:
        value = {"start": time.time(), "count": 0}
    if value["count"] >= 20:
        raise HTTPException(
            status_code=429, detail="Too many sign-in attempts. Try again in 15 minutes."
        )
    store.set(key, {**value, "count": value["count"] + 1})


@router.get("/me")
def me(db: Session = Depends(get_db)):
    settings = get_settings()
    principal = identity.get()
    user = db.get(User, principal[0]) if principal else None
    return {
        "user": public_user(user) if user else None,
        "auth_required": settings.auth_required,
        "registration_enabled": settings.registration_enabled,
    }


@router.post("/register", status_code=201)
def register(
    data: Registration, request: Request, response: Response, db: Session = Depends(get_db)
):
    settings = get_settings()
    if not settings.registration_enabled:
        raise HTTPException(
            status_code=403, detail="New account registration is currently disabled."
        )
    rate_limit(db, request)
    user = User(
        id=str(uuid.uuid4()),
        email=data.email,
        display_name=data.display_name.strip(),
        password_hash=password_hash(data.password),
    )
    db.add(user)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=409, detail="An account already exists for this email. Sign in instead."
        ) from exc
    create_session(db, user, response, settings)
    return {"user": public_user(user)}


@router.post("/login")
def login(data: Credentials, request: Request, response: Response, db: Session = Depends(get_db)):
    rate_limit(db, request)
    user = db.scalars(select(User).where(User.email == data.email)).first()
    if not password_matches(data.password, user.password_hash if user else None):
        raise HTTPException(status_code=401, detail="Email or password is incorrect.")
    create_session(db, user, response, get_settings())
    return {"user": public_user(user)}


@router.post("/logout")
def logout(request: Request, response: Response, db: Session = Depends(get_db)):
    token = request.cookies.get(COOKIE_NAME)
    if token:
        session = db.get(AuthSession, digest(token))
        if session:
            db.delete(session)
            db.commit()
    response.delete_cookie(COOKIE_NAME, path="/")
    return {"signed_out": True}
