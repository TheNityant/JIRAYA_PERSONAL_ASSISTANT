from functools import lru_cache

from pydantic import Field
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

    nebius_api_key: str = Field(default="")
    nebius_base_url: str = "https://api.tokenfactory.nebius.com/v1/"
    nebius_model: str = "nvidia/Nemotron-3_5-Lightning"
    nebius_timeout_seconds: float = 60.0

    database_url: str = "postgresql+psycopg://jiraya:jiraya_dev_password@localhost:5432/jiraya"
    enable_external_actions: bool = False


@lru_cache
def get_settings() -> Settings:
    return Settings()
