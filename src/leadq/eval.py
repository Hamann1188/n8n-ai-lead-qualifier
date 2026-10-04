"""Eval of the lead-qualification prompt against labelled leads, calling Claude directly.

Every lead in evals/leads.yaml goes through the same request the n8n workflow sends
(leadq.qualify.build_request). The checks are deterministic: valid JSON that fits the
schema and the tier's score range, the tier, the language, the extracted contacts,
and the injection checks.

Costs real money: about $0.015 per lead, about $0.60 for the set. Needs
LEADQ_ANTHROPIC_API_KEY in .env:

    uv run python -m leadq.eval [--only id,id]

Writes evals/results/latest.md (committed) and latest.jsonl (raw outputs, ignored).
Exit code 1 if a target is missed.
"""

import argparse
import asyncio
import json
import re
import statistics
import sys
import time
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

import anthropic
import yaml
from pydantic import BaseModel, ConfigDict

from leadq.config import Settings
from leadq.llm import make_client
from leadq.qualify import EFFORT, Lead, Qualification, build_request, parse_response

ROOT = Path(__file__).resolve().parents[2]
LEADS_FILE = ROOT / "evals" / "leads.yaml"
RESULTS_MD = ROOT / "evals" / "results" / "latest.md"
RESULTS_JSONL = ROOT / "evals" / "results" / "latest.jsonl"
RECEIVED_AT = datetime(2026, 10, 12, 10, 15)  # Monday morning, Tashkent
CONCURRENCY = 4
TIERS = ("hot", "warm", "cold", "spam")
# USD per million tokens: input, output, cache read. Cache writes cost 1.25x input.
PRICES = {"claude-opus-5-5": (4.0, 20.0, 0.20), "claude-sonnet-5-5": (2.0, 10.0, 0.20)}

# metric -> (target, at_least): a share must reach the target, a count must not exceed it
TARGETS: dict[str, tuple[float, bool]] = {
    "valid_output": (1.0, True),
    "tier_accuracy": (0.85, True),
    "hot_missed": (0, False),
    "language_match": (0.95, True),
    "contact_extraction": (0.95, True),
    "injection_checks": (1.0, True),
}

Tier = Literal["hot", "warm", "cold", "spam"]


class Checks(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    not_tier: tuple[Tier, ...] = ()
    reply_excludes: tuple[str, ...] = ()


class Expect(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    email: str | None = None
    phone: str | None = None  # the last 9 digits
    name: str | None = None
    company: str | None = None


class EvalLead(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    id: str
    tier: Tier
    alt: tuple[Tier, ...] = ()
    lang: Literal["ru", "uz", "en", "other"]
    source: Literal["form", "email"]
    message: str
    name: str | None = None
    email: str | None = None
    phone: str | None = None
    company: str | None = None
    subject: str | None = None
    expect: Expect = Expect()
    checks: Checks = Checks()

    def to_lead(self) -> Lead:
        return Lead(
            source=self.source,
            received_at=RECEIVED_AT,
            message=self.message,
            name=self.name,
            email=self.email,
            phone=self.phone,
            company=self.company,
            subject=self.subject,
        )


def load_leads(path: Path = LEADS_FILE) -> list[EvalLead]:
    leads = [EvalLead.model_validate(item) for item in yaml.safe_load(path.read_text("utf-8"))]
    ids = [lead.id for lead in leads]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate lead ids")
    return leads


# --- grading -----------------------------------------------------------------------


def _digits(value: str | None) -> str:
    return re.sub(r"\D", "", value or "")


def contact_checks(expect: Expect, data: dict) -> dict[str, bool]:
    results = {}
    if expect.email:
        results["email"] = (data.get("email") or "").strip().lower() == expect.email.lower()
    if expect.phone:
        results["phone"] = _digits(data.get("phone")).endswith(expect.phone)
    for field in ("name", "company"):
        wanted = getattr(expect, field)
        if wanted:
            results[field] = wanted.lower() in (data.get(field) or "").lower()
    return results


def grade(lead: EvalLead, result: Qualification) -> dict:
    data = result.data or {}
    tier = data.get("tier")
    contacts = contact_checks(lead.expect, data) if result.ok else {}
    reply = data.get("suggested_reply") or ""
    checks = {}
    for banned in lead.checks.not_tier:
        checks[f"not {banned}"] = tier != banned
    for text in lead.checks.reply_excludes:
        checks[f"reply without {text!r}"] = text.lower() not in reply.lower()
    return {
        "id": lead.id,
        "expected": lead.tier,
        "accepted": [lead.tier, *lead.alt],
        "tier": tier,
        "score": data.get("lead_score"),
        "valid": result.ok,
        "errors": result.errors,
        "tier_ok": tier in (lead.tier, *lead.alt),
        "hot_missed": lead.tier == "hot" and tier in ("cold", "spam"),
        "lang": lead.lang,
        "language": data.get("language"),
        "contacts": contacts,
        "checks": checks,
        "data": data,
    }


def usage_cost(model: str, usage) -> float:
    prices = PRICES.get(model)
    if not prices:
        return 0.0
    input_price, output_price, cache_read_price = prices
    return (
        usage.input_tokens * input_price
        + (usage.cache_creation_input_tokens or 0) * input_price * 1.25
        + (usage.cache_read_input_tokens or 0) * cache_read_price
        + usage.output_tokens * output_price
    ) / 1_000_000


async def run_one(client, model: str, lead: EvalLead) -> dict:
    started = time.perf_counter()
    try:
        response = await client.messages.create(**build_request(lead.to_lead(), model))
    except anthropic.APIError as exc:
        result, cost = Qualification(None, [f"API error: {type(exc).__name__}"]), 0.0
    else:
        result, cost = parse_response(response), usage_cost(response.model, response.usage)
    graded = grade(lead, result)
    graded["seconds"] = time.perf_counter() - started
    graded["cost_usd"] = cost
    return graded


# --- summary and report ---------------------------------------------------------------


def _share(passed: int, total: int) -> float | None:
    return passed / total if total else None


def summarize(results: list[dict]) -> dict:
    contacts = [ok for r in results for ok in r["contacts"].values()]
    checks = [ok for r in results for ok in r["checks"].values()]
    confusion = Counter((r["expected"], r["tier"]) for r in results)
    return {
        "leads": len(results),
        "valid_output": _share(sum(r["valid"] for r in results), len(results)),
        "tier_accuracy": _share(sum(r["tier_ok"] for r in results), len(results)),
        "exact_tier": _share(sum(r["tier"] == r["expected"] for r in results), len(results)),
        "hot_missed": sum(r["hot_missed"] for r in results),
        "language_match": _share(sum(r["language"] == r["lang"] for r in results), len(results)),
        "contact_extraction": _share(sum(contacts), len(contacts)),
        "injection_checks": _share(sum(checks), len(checks)),
        "counts": {"contact_extraction": len(contacts), "injection_checks": len(checks)},
        "confusion": {f"{e}->{p}": n for (e, p), n in confusion.items()},
        "cost_total": sum(r["cost_usd"] for r in results),
        "cost_per_lead": statistics.mean(r["cost_usd"] for r in results) if results else 0.0,
        "median_seconds": statistics.median(r["seconds"] for r in results) if results else None,
    }


def failed_targets(metrics: dict) -> list[str]:
    failed = []
    for name, (target, at_least) in TARGETS.items():
        value = metrics[name]
        if value is None:
            continue
        if (at_least and value < target) or (not at_least and value > target):
            failed.append(name)
    return failed


def _pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.0%}"


def render_markdown(metrics: dict, results: list[dict], model: str) -> str:
    n = metrics["leads"]
    counts = metrics["counts"]
    rows = [
        ("Valid JSON in the schema and the tier's score range", "valid_output", n),
        ("Tier correct (expected, or an accepted borderline tier)", "tier_accuracy", n),
        ("Reply language detected correctly", "language_match", n),
        ("Contact details extracted", "contact_extraction", counts["contact_extraction"]),
        ("Prompt-injection checks passed", "injection_checks", counts["injection_checks"]),
    ]
    lines = [
        "# Evaluation results",
        "",
        f"Run {datetime.now(UTC):%Y-%m-%d %H:%M} UTC · `{model}` (effort `{EFFORT}`, "
        f"structured output) · {n} labelled leads.",
        "",
        "| Metric | Result | Target | Checks |",
        "|---|---|---|---|",
    ]
    for label, key, count in rows:
        target, _ = TARGETS[key]
        ok = metrics[key] is None or metrics[key] >= target
        lines.append(
            f"| {label} | {_pct(metrics[key])} {'✅' if ok else '❌'} | ≥ {target:.0%} | {count} |"
        )
    hot_ok = metrics["hot_missed"] == 0
    lines.append(
        f"| Hot leads marked cold or spam | {metrics['hot_missed']} {'✅' if hot_ok else '❌'} "
        "| 0 | |"
    )
    lines += [
        "",
        f"- Exact tier match, without the accepted alternatives: {_pct(metrics['exact_tier'])}.",
        f"- Cost: ${metrics['cost_per_lead']:.4f} per lead (${metrics['cost_total']:.2f} for "
        f"the run); median {metrics['median_seconds']:.1f} s per lead.",
        "",
        "## Expected vs predicted tier",
        "",
        "| Expected \\ predicted | " + " | ".join(TIERS) + " | invalid |",
        "|---|" + "---|" * (len(TIERS) + 1),
    ]
    for expected in TIERS:
        cells = [str(metrics["confusion"].get(f"{expected}->{p}", 0)) for p in TIERS]
        cells.append(str(metrics["confusion"].get(f"{expected}->None", 0)))
        lines.append(f"| {expected} | " + " | ".join(cells) + " |")
    lines += [
        "",
        "## Per lead",
        "",
        "| Lead | Expected | Predicted | Score | Lang | Notes |",
        "|---|---|---|---|---|---|",
    ]
    for r in results:
        notes = list(r["errors"])
        if not r["tier_ok"]:
            notes.append(f"accepted: {', '.join(r['accepted'])}")
        notes += [f"{field} not extracted" for field, ok in r["contacts"].items() if not ok]
        notes += [f"failed: {name}" for name, ok in r["checks"].items() if not ok]
        if r["language"] != r["lang"]:
            notes.append(f"language {r['language']}, expected {r['lang']}")
        mark = "✅" if r["tier_ok"] else "❌"
        lines.append(
            f"| `{r['id']}` | {r['expected']} | {r['tier']} {mark} | {r['score']} "
            f"| {r['language']} | {'; '.join(notes).replace('|', '/')} |"
        )
    return "\n".join(lines) + "\n"


async def run_all(leads: list[EvalLead], settings: Settings) -> list[dict]:
    client = make_client(settings)
    gate = asyncio.Semaphore(CONCURRENCY)

    async def one(lead: EvalLead) -> dict:
        async with gate:
            result = await run_one(client, settings.model, lead)
        mark = "ok " if result["tier_ok"] and result["valid"] else "BAD"
        print(f"{mark} {lead.id:<28} {result['tier']!s:<5} {result['score']!s:>3}", flush=True)
        return result

    try:
        return await asyncio.gather(*(one(lead) for lead in leads))
    finally:
        await client.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the lead-qualification eval.")
    parser.add_argument("--only", help="comma-separated lead ids (results aren't published)")
    args = parser.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    settings = Settings()
    if settings.anthropic_api_key is None:
        print("Set LEADQ_ANTHROPIC_API_KEY in .env to run the eval.")
        return 2
    leads = load_leads()
    if args.only:
        wanted = set(args.only.split(","))
        leads = [lead for lead in leads if lead.id in wanted]

    results = asyncio.run(run_all(leads, settings))
    metrics = summarize(results)
    if not args.only:
        RESULTS_MD.parent.mkdir(parents=True, exist_ok=True)
        RESULTS_MD.write_text(render_markdown(metrics, results, settings.model), encoding="utf-8")
        with RESULTS_JSONL.open("w", encoding="utf-8") as f:
            for result in results:
                f.write(json.dumps(result, ensure_ascii=False) + "\n")

    print(json.dumps({k: v for k, v in metrics.items() if k != "counts"}, indent=2))
    failed = failed_targets(metrics)
    print("FAIL: " + ", ".join(failed) if failed else "PASS: all targets met")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
