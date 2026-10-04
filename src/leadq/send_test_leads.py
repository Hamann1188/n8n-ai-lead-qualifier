"""Post labelled leads from evals/leads.yaml to the running n8n workflow and check the tiers.

    uv run python -m leadq.send_test_leads                 # one lead per tier, plus checks
    uv run python -m leadq.send_test_leads --only id,id    # chosen leads
    uv run python -m leadq.send_test_leads --all           # all 40 (about $0.65)

Every lead is a real Claude call through n8n. Hot leads also post to the Telegram group.
First it checks that the webhook rejects a request without the secret header.
"""

import argparse
import json
import sys
import time
import urllib.error
import urllib.request

from leadq.config import Settings
from leadq.eval import EvalLead, load_leads

DEFAULT_SET = ("hot-en-implant", "warm-ru-installments", "cold-uz-samarkand", "spam-en-seo")


def payload(lead: EvalLead) -> dict:
    fields = ("source", "name", "email", "phone", "company", "subject", "message")
    return {f: getattr(lead, f) for f in fields if getattr(lead, f) is not None}


def post(url: str, body: dict, secret: str | None, timeout: float = 120) -> tuple[int, dict]:
    headers = {"Content-Type": "application/json"}
    if secret is not None:
        headers["X-Webhook-Secret"] = secret
    request = urllib.request.Request(
        url, data=json.dumps(body).encode(), headers=headers, method="POST"
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, json.loads(response.read() or b"{}")
    except urllib.error.HTTPError as error:
        text = error.read().decode(errors="replace")
        try:
            return error.code, json.loads(text)
        except json.JSONDecodeError:
            return error.code, {"raw": text[:300]}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--only", help="comma-separated lead ids")
    parser.add_argument("--all", action="store_true", help="send all leads")
    args = parser.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    settings = Settings()
    if settings.webhook_secret is None:
        print("Set LEADQ_WEBHOOK_SECRET in .env (the same value n8n got on deploy).")
        return 2
    leads = load_leads()
    if args.only:
        wanted = args.only.split(",")
        leads = [lead for lead in leads if lead.id in wanted]
    elif not args.all:
        leads = [lead for lead in leads if lead.id in DEFAULT_SET]

    status, _ = post(settings.webhook_url, {"message": "test"}, secret=None)
    secured = status in (401, 403)
    print(f"{'ok ' if secured else 'BAD'} request without the secret is rejected (HTTP {status})")
    status, _ = post(settings.webhook_url, {"message": "test"}, secret="wrong")
    wrong_rejected = status in (401, 403)
    print(f"{'ok ' if wrong_rejected else 'BAD'} wrong secret is rejected (HTTP {status})")

    failures = int(not secured) + int(not wrong_rejected)
    secret = settings.webhook_secret.get_secret_value()
    for lead in leads:
        started = time.perf_counter()
        status, body = post(settings.webhook_url, payload(lead), secret)
        seconds = time.perf_counter() - started
        tier = body.get("tier")
        ok = status == 200 and body.get("ok") is True and tier in (lead.tier, *lead.alt)
        failures += not ok
        detail = f"{tier} {body.get('lead_score')}" if status == 200 else f"HTTP {status} {body}"
        print(f"{'ok ' if ok else 'BAD'} {lead.id:<26} expected {lead.tier:<5} got {detail} "
              f"({seconds:.1f} s, {body.get('lead_id', '-')})")
    print("PASS" if not failures else f"FAIL: {failures} problem(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
