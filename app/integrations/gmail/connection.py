import json

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from sqlalchemy.orm import Session

from app.config import Settings
from app.integrations.gmail.client import GmailClient
from app.security.tokens import TokenCipher
from app.services.connection_store import ConnectionStore


def get_gmail_client(db: Session, settings: Settings) -> GmailClient:
    """Resolve Gmail credentials for the current development user.

    Before Web OAuth is configured, the old local token.json remains a development
    fallback. Once GOOGLE_OAUTH_CLIENT_ID/SECRET are present, the database connection
    becomes authoritative so Disconnect really disconnects Gmail.
    """
    store = ConnectionStore(db)
    connection = store.get_active(user_id=settings.dev_user_id, provider="google")
    if connection is None:
        web_oauth_configured = bool(
            settings.google_oauth_client_id and settings.google_oauth_client_secret
        )
        if web_oauth_configured or settings.auth_required:
            raise RuntimeError("Gmail is not connected. Use Connect Gmail in the Jiraya frontend.")
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
