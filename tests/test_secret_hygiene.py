from pathlib import Path

from app.config import Settings


SENSITIVE_TEMPLATE_KEYS = {
    "NEBIUS_API_KEY",
    "TOKEN_ENCRYPTION_KEY",
    "GOOGLE_OAUTH_CLIENT_ID",
    "GOOGLE_OAUTH_CLIENT_SECRET",
    "GMAIL_PUBSUB_TOPIC_NAME",
    "GMAIL_PUBSUB_WEBHOOK_TOKEN",
}


def _env_values(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip()
    return values


def test_runtime_settings_read_local_dotenv_only():
    assert Settings.model_config.get("env_file") == ".env"


def test_env_example_contains_no_real_sensitive_values():
    values = _env_values(Path(".env.example"))
    leaked = {
        key: values.get(key, "")
        for key in SENSITIVE_TEMPLATE_KEYS
        if values.get(key, "")
    }
    assert leaked == {}, (
        "Sensitive/project-specific values must never be committed in .env.example. "
        "Put the real values in the gitignored local .env file instead."
    )


def test_gitignore_protects_env_variants_but_keeps_example_trackable():
    rules = {
        line.strip()
        for line in Path(".gitignore").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }
    assert ".env" in rules
    assert ".env.*" in rules
    assert "!.env.example" in rules
