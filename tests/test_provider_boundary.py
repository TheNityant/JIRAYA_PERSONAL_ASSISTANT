from app.config import Settings
from app.providers.nebius import NebiusProvider


def test_nebius_provider_requires_api_key():
    settings = Settings(_env_file=None, nebius_api_key="", database_url="sqlite+pysqlite:///:memory:")
    try:
        NebiusProvider(settings)
    except ValueError as exc:
        assert "NEBIUS_API_KEY" in str(exc)
    else:
        raise AssertionError("Provider should reject a missing API key.")
