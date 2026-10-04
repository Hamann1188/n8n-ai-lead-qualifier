"""The Claude request that qualifies one lead, shared by the eval and the n8n workflow.

prompts/qualify.md and schemas/lead.schema.json are the source of truth; the n8n HTTP
Request node gets the same body (synced in step 3), so the eval measures exactly what
runs in production.
"""

import json
from dataclasses import dataclass, field
from datetime import datetime
from functools import cache
from pathlib import Path

from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[2]
PROMPT_FILE = ROOT / "prompts" / "qualify.md"
SCHEMA_FILE = ROOT / "schemas" / "lead.schema.json"

MODEL = "claude-opus-5-5"  # what the n8n workflow sends; the eval defaults to it too
MAX_TOKENS = 4000  # thinking (always on for Claude Opus 5.5) plus the JSON
EFFORT = "low"  # classification and extraction
# Score ranges per tier, as the prompt defines them.
TIER_RANGES: dict[str, tuple[int, int]] = {
    "hot": (70, 100),
    "warm": (40, 69),
    "cold": (10, 39),
    "spam": (0, 9),
}
SOURCES = {"form": "website form", "email": "email"}


@cache
def system_prompt() -> str:
    return PROMPT_FILE.read_text(encoding="utf-8").strip()


@cache
def output_schema() -> dict:
    return json.loads(SCHEMA_FILE.read_text(encoding="utf-8"))


@dataclass(frozen=True)
class Lead:
    source: str  # form | email
    received_at: datetime  # clinic local time
    message: str
    name: str | None = None
    email: str | None = None
    phone: str | None = None
    company: str | None = None
    subject: str | None = None


def _clean(text: str) -> str:
    # The lead can't close or reopen our tags.
    return text.replace("<lead>", "").replace("</lead>", "").strip()


def lead_message(lead: Lead) -> str:
    lines = [
        "<lead>",
        f"source: {SOURCES.get(lead.source, lead.source)}",
        f"received: {lead.received_at:%A %Y-%m-%d %H:%M} (Asia/Tashkent)",
    ]
    for label, value in (
        ("name", lead.name),
        ("email", lead.email),
        ("phone", lead.phone),
        ("company", lead.company),
        ("subject", lead.subject),
    ):
        if value and value.strip():
            lines.append(f"{label}: {_clean(value)}")
    lines += ["message:", _clean(lead.message) or "(empty)", "</lead>"]
    return "\n".join(lines)


def build_request(lead: Lead, model: str) -> dict:
    """The Messages API body. Structured outputs guarantee valid JSON; effort is explicit
    (Claude Opus 5.5 defaults to medium); no thinking switch-off and no forced
    tool_choice, both of which Claude Opus 5.5 rejects."""
    return {
        "model": model,
        "max_tokens": MAX_TOKENS,
        "system": system_prompt(),
        "messages": [{"role": "user", "content": lead_message(lead)}],
        "output_config": {
            "effort": EFFORT,
            "format": {"type": "json_schema", "schema": output_schema()},
        },
    }


@dataclass
class Qualification:
    data: dict | None
    errors: list[str] = field(default_factory=list)
    stop_reason: str | None = None

    @property
    def ok(self) -> bool:
        return self.data is not None and not self.errors


def parse_response(response) -> Qualification:
    """Check the stop reason first, read the text block by type, validate the JSON."""
    stop = response.stop_reason
    if stop == "refusal":
        return Qualification(None, ["refused"], stop)
    if stop == "max_tokens":
        return Qualification(None, ["cut off at max_tokens"], stop)
    text = next((b.text for b in response.content if b.type == "text"), None)
    if text is None:
        return Qualification(None, ["no text block"], stop)
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        return Qualification(None, [f"invalid JSON: {exc}"], stop)
    return Qualification(data, validate(data), stop)


def validate(data: dict) -> list[str]:
    """Schema errors plus what strict mode can't express: the score range per tier."""
    errors = [e.message for e in Draft202012Validator(output_schema()).iter_errors(data)]
    if errors:
        return errors
    low, high = TIER_RANGES[data["tier"]]
    if not low <= data["lead_score"] <= high:
        errors.append(f"score {data['lead_score']} outside {data['tier']} range {low}-{high}")
    if data["tier"] != "spam" and not data["suggested_reply"].strip():
        errors.append("empty suggested_reply for a non-spam lead")
    return errors
