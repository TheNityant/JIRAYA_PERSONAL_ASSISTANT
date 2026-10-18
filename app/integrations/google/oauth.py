from google_auth_oauthlib.flow import Flow

from app.config import Settings


GOOGLE_GMAIL_SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
]


def _client_config(settings: Settings) -> dict:
    if not settings.google_oauth_client_id or not settings.google_oauth_client_secret:
        raise RuntimeError(
            "Google web OAuth is not configured. Set GOOGLE_OAUTH_CLIENT_ID and "
            "GOOGLE_OAUTH_CLIENT_SECRET from a Google Cloud OAuth client of type Web application."
        )
    return {
        "web": {
            "client_id": settings.google_oauth_client_id,
            "client_secret": settings.google_oauth_client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": [settings.google_oauth_redirect_uri],
        }
    }


def build_google_oauth_flow(
    settings: Settings,
    *,
    state: str | None = None,
    scopes: list[str] | None = None,
    code_verifier: str | None = None,
    autogenerate_code_verifier: bool = False,
) -> Flow:
    """Build one leg of Google's web OAuth flow.

    PKCE is generated explicitly on the authorization leg and the same verifier is
    supplied again on the callback leg. Reconstructing a Flow without that verifier
    after sending a code_challenge makes Google reject the token exchange with
    ``invalid_grant: Missing code verifier``.
    """
    flow = Flow.from_client_config(
        _client_config(settings),
        scopes=scopes or GOOGLE_GMAIL_SCOPES,
        state=state,
        code_verifier=code_verifier,
        autogenerate_code_verifier=autogenerate_code_verifier,
    )
    flow.redirect_uri = settings.google_oauth_redirect_uri
    return flow
