from functools import lru_cache
from typing import Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    app_name: str = "Jiraya Personal Assistant"
    app_env: str = "development"
    app_host: str = "127.0.0.1"
    app_port: int = 8000
    frontend_origins: str = "http://localhost:3000,http://127.0.0.1:3000"
    frontend_base_url: str = "http://localhost:3000"

    nebius_api_key: str = Field(default="")
    nebius_base_url: str = "https://api.tokenfactory.nebius.com/v1/"
    nebius_model: str = "nvidia/Nemotron-3_5-Lightning"
    nebius_timeout_seconds: float = 60.0

    database_url: str = "postgresql+psycopg://jiraya:jiraya_dev_password@localhost:5432/jiraya"
    enable_external_actions: bool = False

    dev_user_id: str = "local-dev-user"
    dev_user_email: str = "local@jiraya.dev"

    auth_required: bool = True
    session_days: int = Field(default=7, ge=1, le=30)
    session_cookie_secure: bool = False
    session_cookie_samesite: Literal["lax", "none"] = "lax"
    registration_enabled: bool = True

    @model_validator(mode="after")
    def production_authentication(self):
        if self.app_env.lower() == "production" and not self.auth_required:
            raise ValueError("Authentication must remain enabled in production")
        return self

    token_encryption_key: str = ""

    google_oauth_client_id: str = ""
    google_oauth_client_secret: str = ""
    google_oauth_redirect_uri: str = "http://127.0.0.1:8000/connections/google/callback"
    google_calendar_oauth_redirect_uri: str = (
        "http://127.0.0.1:8000/connections/google/calendar/callback"
    )

    github_oauth_client_id: str = ""
    github_oauth_client_secret: str = ""
    github_oauth_redirect_uri: str = "http://127.0.0.1:8000/connections/github/callback"
    github_oauth_scopes: str = "repo workflow read:user"
    github_background_monitor_enabled: bool = True
    github_background_poll_seconds: int = Field(default=60, ge=15, le=3600)
    github_api_base_url: str = "https://api.github.com"

    gmail_credentials_path: str = "credentials.json"
    gmail_token_path: str = "token.json"
    gmail_classification_threshold: float = Field(default=0.85, ge=0.0, le=1.0)
    gmail_reply_template_path: str = "app/templates/internship_reply.txt"
    gmail_reply_signature: str = "Nityant"

    # Automatic Gmail runtime. Local development can poll Gmail history; production
    # can additionally use users.watch + Pub/Sub to wake this same event processor.
    gmail_background_monitor_enabled: bool = True
    gmail_background_poll_seconds: int = Field(default=15, ge=5, le=3600)
    gmail_pubsub_topic_name: str = ""
    gmail_pubsub_webhook_token: str = ""
    gmail_watch_auto_register: bool = True
    calendar_background_monitor_enabled: bool = True
    calendar_background_poll_seconds: int = Field(default=30, ge=5, le=3600)


@lru_cache
def get_base_settings() -> Settings:
    return Settings()


def get_settings() -> Settings:
    from app.security.identity import identity

    base = get_base_settings()
    current = identity.get()
    return (
        base.model_copy(update={"dev_user_id": current[0], "dev_user_email": current[1]})
        if current
        else base
    )


# Preserve the cache-reset hook used by local tooling/tests.
get_settings.cache_clear = get_base_settings.cache_clear
