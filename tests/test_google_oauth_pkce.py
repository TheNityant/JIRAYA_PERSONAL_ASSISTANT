from urllib.parse import parse_qs, urlparse

from app.config import Settings
from app.integrations.google.oauth import build_google_oauth_flow


def test_google_oauth_pkce_verifier_can_be_reused_for_callback():
    settings = Settings(
        google_oauth_client_id="test-client.apps.googleusercontent.com",
        google_oauth_client_secret="test-secret",
        google_oauth_redirect_uri="http://127.0.0.1:8000/connections/google/callback",
    )

    start_flow = build_google_oauth_flow(
        settings,
        state="test-state",
        autogenerate_code_verifier=True,
    )
    authorization_url, _ = start_flow.authorization_url(
        access_type="offline",
        prompt="consent",
    )

    verifier = start_flow.code_verifier
    assert verifier
    assert 43 <= len(verifier) <= 128

    query = parse_qs(urlparse(authorization_url).query)
    assert query["code_challenge_method"] == ["S256"]
    assert query.get("code_challenge")

    callback_flow = build_google_oauth_flow(
        settings,
        state="test-state",
        code_verifier=verifier,
        autogenerate_code_verifier=False,
    )
    assert callback_flow.code_verifier == verifier
