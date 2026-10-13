import json

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from sqlalchemy.orm import Session

from app.config import Settings
from app.integrations.gmail.client import GmailClient
from app.security.tokens import TokenCipher
from app.services.connection_store import ConnectionStore


def get_gmail_client(db: Session, settings: Settings) -> GmailClient:
    """Prefer a database-backed Google connection, then fall back to local token.json.

    The fallback keeps the existing developer workflow working while Phase 2D migrates
    Gmail to the same connection system real users will use through the frontend.
    """
    store = ConnectionStore(db)
    connection = store.get_active(user_id=settings.dev_user_id, provider="google")
    if connection is None:
        return GmailClient(settings)

    cipher = TokenCipher(settings.token_encryption_key)
    payload = json.loads(cipher.decrypt(connection.credential_ciphertext))
    credentials = Credentials.from_authorized_user_info(payload, scopes=connection.scopes)

    if credentials.expired and credentials.refresh_token:
        credentials.refresh(Request())
        store.update_credentials(
            connection,
            credential_ciphertext=cipher.encrypt(credentials.to_json()),
            scopes=list(credentials.scopes or connection.scopes),
            token_expires_at=credentials.expiry,
        )

    if not credentials.valid:
        raise RuntimeError("Connected Google credentials are no longer valid. Reconnect Gmail.")

    return GmailClient(settings, credentials=credentials)
