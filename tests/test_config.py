from pathlib import Path

import yaml

from leadq.config import Settings
from leadq.llm import make_client

ROOT = Path(__file__).resolve().parents[1]


def test_defaults_without_env_file(monkeypatch):
    for name in ("LEADQ_ANTHROPIC_API_KEY", "LEADQ_WEBHOOK_SECRET"):
        monkeypatch.delenv(name, raising=False)
    settings = Settings(_env_file=None)
    assert settings.anthropic_api_key is None and settings.webhook_secret is None
    assert settings.model == "claude-opus-5-5"
    assert settings.webhook_url == "http://localhost:5678/webhook/lead-intake"
    assert make_client(settings) is None


def test_global_anthropic_base_url_is_ignored(monkeypatch):
    # Claude Code exports ANTHROPIC_BASE_URL for its local proxy; the tooling must not use it.
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "http://127.0.0.1:8787")
    monkeypatch.setenv("LEADQ_ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.delenv("LEADQ_ANTHROPIC_BASE_URL", raising=False)
    client = make_client(Settings(_env_file=None))
    assert str(client.base_url).rstrip("/") == "https://api.anthropic.com"


def test_secrets_are_masked(monkeypatch):
    monkeypatch.setenv("LEADQ_WEBHOOK_SECRET", "s3cret-value")
    settings = Settings(_env_file=None)
    assert "s3cret-value" not in repr(settings)
    assert settings.webhook_secret.get_secret_value() == "s3cret-value"


def test_compose_pins_n8n_and_keeps_ports_local():
    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    n8n = compose["services"]["n8n"]
    image, _, tag = n8n["image"].partition(":")
    assert image == "n8nio/n8n" and tag[0].isdigit(), "pin an exact n8n version"
    assert n8n["ports"] == ["127.0.0.1:5678:5678"]
    assert "ports" not in compose["services"]["db"]
    assert n8n["environment"]["DB_TYPE"] == "postgresdb"
    assert "N8N_ENCRYPTION_KEY" in n8n["environment"]


def test_env_example_lists_every_secret_without_values():
    lines = (ROOT / ".env.example").read_text(encoding="utf-8").splitlines()
    values = dict(line.split("=", 1) for line in lines if "=" in line and not line.startswith("#"))
    for secret in (
        "N8N_ENCRYPTION_KEY",
        "POSTGRES_PASSWORD",
        "LEADQ_WEBHOOK_SECRET",
        "LEADQ_ANTHROPIC_API_KEY",
    ):
        assert values[secret] == "", f"{secret} must be empty in the template"
