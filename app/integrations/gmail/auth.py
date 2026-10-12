from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow

from app.config import Settings, get_settings

# Least-privilege split for Phase 1: read mailbox + send mail. We deliberately
# do not request delete/modify/full-mailbox scopes.
GMAIL_SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
]


def load_credentials(settings: Settings, *, interactive: bool = False) -> Credentials:
    token_path = Path(settings.gmail_token_path)
    credentials_path = Path(settings.gmail_credentials_path)
    creds: Credentials | None = None

    if token_path.exists():
        creds = Credentials.from_authorized_user_file(str(token_path), GMAIL_SCOPES)

    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
        token_path.write_text(creds.to_json(), encoding="utf-8")

    if creds and creds.valid:
        return creds

    if not interactive:
        raise RuntimeError(
            "Gmail is not authorized yet. Put credentials.json in the repository root "
            "and run: python -m app.integrations.gmail.auth"
        )

    if not credentials_path.exists():
        raise FileNotFoundError(
            f"Gmail OAuth client file not found at {credentials_path}. "
            "Create a Desktop OAuth client in Google Cloud and download its JSON first."
        )

    flow = InstalledAppFlow.from_client_secrets_file(str(credentials_path), GMAIL_SCOPES)
    creds = flow.run_local_server(port=0)
    token_path.write_text(creds.to_json(), encoding="utf-8")
    return creds


def authorize_interactively() -> None:
    settings = get_settings()
    creds = load_credentials(settings, interactive=True)
    print("Gmail authorization complete.")
    print(f"Token saved to: {settings.gmail_token_path}")
    print(f"Granted scopes: {', '.join(creds.scopes or GMAIL_SCOPES)}")


if __name__ == "__main__":
    authorize_interactively()
