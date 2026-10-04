from pydantic import Field, SecretStr
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

    # Hot-lead alerts. `leadq.n8n deploy` copies the token into n8n's credential store
    # and the chat id into the workflow; neither is committed.
    telegram_bot_token: SecretStr | None = None
    telegram_chat_id: int | None = None

    # The Leads spreadsheet: the id from its URL, docs.google.com/spreadsheets/d/<id>/edit
    google_sheet_id: str | None = None

    # The n8n database, shared with docker-compose.yml (no LEADQ_ prefix).
    postgres_user: str = Field("n8n", validation_alias="POSTGRES_USER")
    postgres_db: str = Field("n8n", validation_alias="POSTGRES_DB")

    # How to reach Docker Compose from this machine, e.g. "wsl -d Ubuntu -- docker compose".
    compose_command: str = "docker compose"
