import json
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.config import Settings
from app.integrations.github.client import GitHubClient
from app.integrations.github.oauth import refresh_github_token, token_expiry
from app.security.tokens import TokenCipher
from app.services.connection_store import ConnectionStore


def _expired(value: datetime | None) -> bool:
    if value is None:
        return False
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value <= datetime.now(timezone.utc)


def get_github_client(db: Session, settings: Settings) -> GitHubClient:
    store = ConnectionStore(db)
    connection = store.get_active(user_id=settings.dev_user_id, provider="github")
    if connection is None:
        raise RuntimeError("GitHub is not connected. Use Connect GitHub in Jiraya.")

    cipher = TokenCipher(settings.token_encryption_key)
    payload = json.loads(cipher.decrypt(connection.credential_ciphertext))

    if _expired(connection.token_expires_at):
        refresh_token = str(payload.get("refresh_token") or "")
        if not refresh_token:
            raise RuntimeError("GitHub access token expired and cannot be refreshed. Reconnect GitHub.")
        refreshed = refresh_github_token(settings, refresh_token=refresh_token)
        payload.update({key: value for key, value in refreshed.items() if value is not None})
        scopes = str(payload.get("scope") or "").replace(",", " ").split()
        store.update_credentials(
            connection,
            credential_ciphertext=cipher.encrypt(json.dumps(payload)),
            scopes=scopes or list(connection.scopes or []),
            token_expires_at=token_expiry(refreshed),
        )

    return GitHubClient(
        access_token=str(payload.get("access_token") or ""),
        api_base_url=settings.github_api_base_url,
    )
