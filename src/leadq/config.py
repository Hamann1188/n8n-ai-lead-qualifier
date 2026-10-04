from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Settings of the Python tooling, read from LEADQ_* environment variables and `.env`.

    n8n has its own configuration in docker-compose.yml and keeps its credentials
    (Anthropic key, Telegram, Google) in its encrypted credential store.
    """

    model_config = SettingsConfigDict(
        env_prefix="LEADQ_", env_file=".env", extra="ignore", env_ignore_empty=True
    )

    anthropic_api_key: SecretStr | None = None
    # Always passed to the client explicitly, so a globally exported
    # ANTHROPIC_BASE_URL (e.g. a local proxy) is never picked up.
    anthropic_base_url: str = "https://api.anthropic.com"
    model: str = "claude-opus-5-5"

    n8n_url: str = "http://localhost:5678"
    webhook_url: str = "http://localhost:5678/webhook/lead-intake"
    # Shared secret the website sends in the X-Webhook-Secret header.
    webhook_secret: SecretStr | None = None
