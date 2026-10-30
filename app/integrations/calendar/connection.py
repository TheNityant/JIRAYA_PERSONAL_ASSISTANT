import json

from google.auth.transport.requests import Request
from google.auth.exceptions import RefreshError
from google.oauth2.credentials import Credentials
from sqlalchemy.orm import Session

from app.config import Settings
from app.integrations.calendar.client import CalendarClient
from app.integrations.calendar.errors import CalendarConnectionError
from app.integrations.calendar.oauth import GOOGLE_CALENDAR_SCOPES
from app.security.tokens import TokenCipher
from app.services.connection_store import ConnectionStore
from app.services.runtime_state_store import RuntimeStateStore


def get_calendar_client(db: Session, settings: Settings) -> CalendarClient:
    store = ConnectionStore(db)
    connection = store.get_active(
        user_id=settings.dev_user_id,
        provider="google_calendar",
    )
    if connection is None:
        raise CalendarConnectionError("Google Calendar is not connected. Use Connect Calendar in Jiraya.")

    granted = set(connection.scopes or [])
    missing = [scope for scope in GOOGLE_CALENDAR_SCOPES if scope not in granted]
    if missing:
        raise CalendarConnectionError("Google Calendar connection is missing required scopes. Reconnect Calendar.")

    payload = json.loads(
        TokenCipher(settings.token_encryption_key).decrypt(connection.credential_ciphertext)
    )
    credentials = Credentials.from_authorized_user_info(payload, scopes=connection.scopes)
    if credentials.expired and credentials.refresh_token:
        try:
            credentials.refresh(Request())
        except RefreshError as exc:
            # Transport/server failures may recover; permanent invalid_grant needs consent.
            if not getattr(exc, "retryable", False):
                RuntimeStateStore(db).set(f"oauth-calendar-health:{settings.dev_user_id}", {
                    "status": "RECONNECT_REQUIRED",
                    "detail": "Calendar authorization expired or was revoked. Reconnect Calendar.",
                })
                raise CalendarConnectionError("Reconnect Calendar: authorization expired or was revoked.") from exc
            raise
        store.update_credentials(
            connection,
            credential_ciphertext=TokenCipher(settings.token_encryption_key).encrypt(
                credentials.to_json()
            ),
            scopes=list(credentials.scopes or connection.scopes),
            token_expires_at=credentials.expiry,
        )
    if not credentials.valid:
        raise CalendarConnectionError("Google Calendar credentials are no longer valid. Reconnect Calendar.")
    return CalendarClient(credentials)
