from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

import httpx

from app.config import Settings


def build_github_authorization_url(settings: Settings, *, state: str) -> str:
    if not settings.github_oauth_client_id or not settings.github_oauth_client_secret:
        raise RuntimeError(
            "GitHub OAuth is not configured. Set GITHUB_OAUTH_CLIENT_ID and "
            "GITHUB_OAUTH_CLIENT_SECRET in the local .env file."
        )
    query = urlencode(
        {
            "client_id": settings.github_oauth_client_id,
            "redirect_uri": settings.github_oauth_redirect_uri,
            "scope": settings.github_oauth_scopes,
            "state": state,
            "allow_signup": "true",
        }
    )
    return f"https://github.com/login/oauth/authorize?{query}"


def exchange_github_code(settings: Settings, *, code: str) -> dict:
    response = httpx.post(
        "https://github.com/login/oauth/access_token",
        headers={"Accept": "application/json"},
        data={
            "client_id": settings.github_oauth_client_id,
            "client_secret": settings.github_oauth_client_secret,
            "code": code,
            "redirect_uri": settings.github_oauth_redirect_uri,
        },
        timeout=30.0,
    )
    response.raise_for_status()
    payload = response.json()
    if payload.get("error"):
        raise RuntimeError(
            f"GitHub OAuth token exchange failed: {payload.get('error_description') or payload['error']}"
        )
    if not payload.get("access_token"):
        raise RuntimeError("GitHub OAuth returned no access token.")
    return payload


def refresh_github_token(settings: Settings, *, refresh_token: str) -> dict:
    response = httpx.post(
        "https://github.com/login/oauth/access_token",
        headers={"Accept": "application/json"},
        data={
            "client_id": settings.github_oauth_client_id,
            "client_secret": settings.github_oauth_client_secret,
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
        },
        timeout=30.0,
    )
    response.raise_for_status()
    payload = response.json()
    if payload.get("error"):
        raise RuntimeError(
            f"GitHub OAuth refresh failed: {payload.get('error_description') or payload['error']}"
        )
    if not payload.get("access_token"):
        raise RuntimeError("GitHub OAuth refresh returned no access token.")
    return payload


def token_expiry(payload: dict) -> datetime | None:
    raw = payload.get("expires_in")
    if raw in (None, ""):
        return None
    try:
        seconds = int(raw)
    except (TypeError, ValueError):
        return None
    return datetime.now(timezone.utc) + timedelta(seconds=max(seconds, 0))
