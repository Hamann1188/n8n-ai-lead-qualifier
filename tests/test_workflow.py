"""The n8n workflow: structure, sync, deploy rendering, and its Code nodes run in Node.js.

The Code-node tests need Node.js: `node` on PATH (CI), or LEADQ_NODE_COMMAND, e.g.
"wsl -d Ubuntu -- docker compose exec -T n8n node" to use the one inside the n8n
container. Without either they are skipped.
"""

import json
import os
import shlex
import shutil
import subprocess
from datetime import datetime

import pytest

from leadq.config import Settings
from leadq.eval import load_leads
from leadq.n8n import (
    CHAT_ID_PLACEHOLDER,
    CODE_NODES,
    CREDENTIAL_IDS,
    END_OF_CONSTANTS,
    GOOGLE_NODES,
    ROOT,
    SHEET_ID_PLACEHOLDER,
    credentials,
    dump_workflow,
    google_credentials,
    import_via_stdin,
    load_workflow,
    node_code,
    parse_credential_rows,
    rendered,
    synced,
)
from leadq.qualify import MODEL, Lead, build_request

WORKFLOW = load_workflow()
NODES = {node["name"]: node for node in WORKFLOW["nodes"]}


# --- structure -------------------------------------------------------------------


def test_committed_workflow_is_in_sync():
    assert dump_workflow(synced(WORKFLOW)) == dump_workflow(WORKFLOW), (
        "run `python -m leadq.n8n sync`"
    )


def test_code_nodes_start_with_generated_constants():
    for name in CODE_NODES:
        code = NODES[name]["parameters"]["jsCode"]
        assert code == node_code(name) and END_OF_CONSTANTS in code


def test_connections_form_the_intended_pipeline():
    def targets(name: str) -> list[list[str]]:
        return [[c["node"] for c in out] for out in WORKFLOW["connections"][name]["main"]]

    assert targets("Lead webhook") == [["Build Claude request"]]
    assert targets("Build Claude request") == [["Qualify with Claude"]]
    assert targets("Qualify with Claude") == [["Parse qualification"]]
    assert targets("Parse qualification") == [
        ["Respond to caller", "Is it hot?", "Sheet row", "Needs a reply draft?"]
    ]
    assert targets("Is it hot?") == [["Alert sales in Telegram"], []]
    assert targets("Sheet row") == [["Log to Google Sheets"]]
    assert targets("Needs a reply draft?") == [["Draft reply in Gmail"], []]
    every_target = {t for name in WORKFLOW["connections"] for out in targets(name) for t in out}
    assert every_target <= set(NODES)


def test_webhook_requires_the_secret_header_and_waits_for_the_answer():
    webhook = NODES["Lead webhook"]
    assert webhook["parameters"]["authentication"] == "headerAuth"
    assert webhook["parameters"]["responseMode"] == "responseNode"
    assert webhook["parameters"]["path"] == "lead-intake"


def test_claude_node_uses_the_credential_and_retries():
    http = NODES["Qualify with Claude"]
    assert http["parameters"]["url"] == "https://api.anthropic.com/v1/messages"
    assert http["parameters"]["genericAuthType"] == "httpHeaderAuth"
    assert http["retryOnFail"] is True and http["maxTries"] == 3
    headers = {p["name"]: p["value"] for p in http["parameters"]["headerParameters"]["parameters"]}
    assert headers == {"anthropic-version": "2023-06-01"}


def test_telegram_message_has_no_n8n_attribution():
    telegram = NODES["Alert sales in Telegram"]["parameters"]
    assert telegram["additionalFields"]["appendAttribution"] is False
    assert telegram["chatId"] == CHAT_ID_PLACEHOLDER


def test_no_secrets_or_real_ids_in_the_committed_workflow():
    # Secrets live in n8n credentials; nodes only reference them by id.
    nodes = json.dumps([n for n in WORKFLOW["nodes"] if n["type"] != "n8n-nodes-base.stickyNote"])
    assert "sk-ant" not in json.dumps(WORKFLOW)
    for header in ("x-api-key", "X-Webhook-Secret"):
        assert header not in nodes
    assert "-100" not in json.dumps(NODES["Alert sales in Telegram"])  # no real chat id
    refs = {
        cred["id"]
        for node in WORKFLOW["nodes"]
        if node["name"] not in GOOGLE_NODES
        for cred in node.get("credentials", {}).values()
    }
    assert refs == set(CREDENTIAL_IDS.values())
    # Google credentials and the spreadsheet belong to the owner; deploy fills them in.
    assert NODES["Log to Google Sheets"]["credentials"] == {
        "googleSheetsOAuth2Api": {"id": "GOOGLE_SHEETS_CREDENTIAL", "name": "Google Sheets"}
    }
    assert NODES["Draft reply in Gmail"]["credentials"] == {
        "gmailOAuth2": {"id": "GMAIL_CREDENTIAL", "name": "Gmail"}
    }
    sheet = NODES["Log to Google Sheets"]["parameters"]
    assert sheet["documentId"]["value"] == SHEET_ID_PLACEHOLDER


def test_sheet_upserts_by_contact_and_gmail_only_drafts():
    sheet = NODES["Log to Google Sheets"]["parameters"]
    assert sheet["operation"] == "appendOrUpdate"
    assert sheet["columns"]["mappingMode"] == "autoMapInputData"
    assert sheet["columns"]["matchingColumns"] == ["contact_key"]
    gmail = NODES["Draft reply in Gmail"]["parameters"]
    assert (gmail["resource"], gmail["operation"]) == ("draft", "create")
    assert gmail["options"]["sendTo"] == "={{ $json.email }}"
    # Nothing in the workflow sends email to a lead.
    assert not [n for n in WORKFLOW["nodes"] if "emailSend" in n["type"]]
    assert all(
        n["parameters"].get("operation") != "send"
        for n in WORKFLOW["nodes"]
        if n["type"] == "n8n-nodes-base.gmail"
    )


# --- deploy rendering ----------------------------------------------------------------


def settings(**values) -> Settings:
    return Settings(_env_file=None, **values)


GOOGLE = {
    "googleSheetsOAuth2Api": ("aB3dE5fG7hJ9kL1m", "Google Sheets account"),
    "gmailOAuth2": ("nP2qR4sT6uV8wX0y", "Gmail account"),
}


def by_name(workflow: dict) -> dict:
    return {node["name"]: node for node in workflow["nodes"]}


def test_rendered_workflow_gets_the_chat_id():
    deployed, _ = rendered(WORKFLOW, -5168295738)
    chat = by_name(deployed)["Alert sales in Telegram"]
    assert chat["parameters"]["chatId"] == "-5168295738"
    assert NODES["Alert sales in Telegram"]["parameters"]["chatId"] == CHAT_ID_PLACEHOLDER


def test_rendered_workflow_disables_google_nodes_until_set_up():
    deployed, disabled = rendered(WORKFLOW, -1)
    assert disabled == ["Log to Google Sheets", "Draft reply in Gmail"]
    nodes = by_name(deployed)
    assert all(nodes[name].get("disabled") is True for name in disabled)
    assert not any(node.get("disabled") for name, node in nodes.items() if name not in disabled)

    # The sheet needs the spreadsheet id too; Gmail only needs its credential.
    _, disabled = rendered(WORKFLOW, -1, GOOGLE)
    assert disabled == ["Log to Google Sheets"]
    _, disabled = rendered(WORKFLOW, -1, {"googleSheetsOAuth2Api": GOOGLE["googleSheetsOAuth2Api"]})
    assert disabled == ["Log to Google Sheets", "Draft reply in Gmail"]


def test_rendered_workflow_gets_the_google_credentials_and_sheet():
    deployed, disabled = rendered(WORKFLOW, -1, GOOGLE, "1AbCdEfGhIjKlMnOpQrStUvWxYz")
    assert disabled == []
    nodes = by_name(deployed)
    sheet = nodes["Log to Google Sheets"]
    assert "disabled" not in sheet
    assert sheet["parameters"]["documentId"]["value"] == "1AbCdEfGhIjKlMnOpQrStUvWxYz"
    assert sheet["credentials"] == {
        "googleSheetsOAuth2Api": {"id": "aB3dE5fG7hJ9kL1m", "name": "Google Sheets account"}
    }
    assert nodes["Draft reply in Gmail"]["credentials"] == {
        "gmailOAuth2": {"id": "nP2qR4sT6uV8wX0y", "name": "Gmail account"}
    }
    # The committed workflow is untouched.
    assert NODES["Log to Google Sheets"]["parameters"]["documentId"]["value"] == "GOOGLE_SHEET_ID"


def test_credential_rows_keep_the_newest_of_each_google_type():
    output = (
        "gmailOAuth2,old1,Gmail old\n"
        "googleSheetsOAuth2Api,s1,Sheets, with a comma\n"
        "httpHeaderAuth,leadqWebhookAuth,Lead webhook secret\n"
        "gmailOAuth2,new2,Gmail account\n"
        "\n"
    )
    assert parse_credential_rows(output) == {
        "gmailOAuth2": ("new2", "Gmail account"),
        "googleSheetsOAuth2Api": ("s1", "Sheets, with a comma"),
    }


def test_container_scripts_have_no_dollar_signs(monkeypatch):
    # Through `wsl --` the WSL shell expands `$` before the container sees it.
    calls = []
    monkeypatch.setattr(
        "leadq.n8n.compose", lambda _s, *args, stdin=None: calls.append((args, stdin)) or ""
    )
    import_via_stdin(settings(), "workflow", [{"name": "x"}])
    google_credentials(settings())
    (import_args, _), (psql_args, sql) = calls
    assert import_args[-1].startswith("cat > /tmp/leadq-workflow.json && n8n import:workflow")
    assert import_args[-1].endswith("|| { rm -f /tmp/leadq-workflow.json; exit 1; }")
    assert psql_args[:4] == ("exec", "-T", "db", "psql")
    for text in (*import_args, *psql_args):
        assert "$" not in text
    assert "credentials_entity" in sql


def test_credentials_from_settings():
    creds = credentials(settings(webhook_secret="w", anthropic_api_key="k"))
    assert [(c["id"], c["type"]) for c in creds] == [
        ("leadqWebhookAuth", "httpHeaderAuth"),
        ("leadqAnthropicKy", "httpHeaderAuth"),
    ]
    assert creds[0]["data"] == {"name": "X-Webhook-Secret", "value": "w"}
    assert creds[1]["data"] == {"name": "x-api-key", "value": "k"}

    with_telegram = credentials(
        settings(webhook_secret="w", anthropic_api_key="k", telegram_bot_token="t")
    )
    assert with_telegram[-1] == {
        "id": "leadqTelegramBot",
        "name": "Telegram bot",
        "type": "telegramApi",
        "data": {"accessToken": "t"},
    }


def test_credentials_need_the_webhook_secret_and_api_key(monkeypatch):
    for name in ("LEADQ_WEBHOOK_SECRET", "LEADQ_ANTHROPIC_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(SystemExit, match="LEADQ_WEBHOOK_SECRET, LEADQ_ANTHROPIC_API_KEY"):
        credentials(settings())


# --- Code nodes in Node.js -------------------------------------------------------------

HARNESS = """
const $json = __INPUT__;
const __NODES = __NODES_JSON__;
const $execution = { id: "42" };
const __time = {
  setZone(zone) { if (zone !== "Asia/Tashkent") throw new Error("zone " + zone); return __time; },
  setLocale() { return __time; },
  toFormat(f) { return f === "cccc yyyy-LL-dd HH:mm" ? "Monday 2026-10-12 10:15" : "BAD " + f; },
  toISO() { return "2026-10-12T10:15:00.000+05:00"; },
};
const $now = __time;
const $ = (name) => ({ item: { json: __NODES[name] } });
let __out;
try {
  __out = { ok: true, result: (() => {
__CODE__
  })() };
} catch (error) {
  __out = { ok: false, error: error.message };
}
process.stdout.write(JSON.stringify(__out));
"""


def node_command() -> list[str] | None:
    if command := os.environ.get("LEADQ_NODE_COMMAND"):
        return shlex.split(command)
    return ["node"] if shutil.which("node") else None


needs_node = pytest.mark.skipif(node_command() is None, reason="Node.js not available")


def run_code(node_name: str, input_json: dict, nodes: dict | None = None) -> dict:
    script = (
        HARNESS.replace("__INPUT__", json.dumps(input_json, ensure_ascii=False))
        .replace("__NODES_JSON__", json.dumps(nodes or {}, ensure_ascii=False))
        .replace("__CODE__", NODES[node_name]["parameters"]["jsCode"])
    )
    result = subprocess.run(
        node_command(),
        input=script,
        capture_output=True,
        text=True,
        encoding="utf-8",
        cwd=ROOT,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def body_of(lead) -> dict:
    fields = ("source", "name", "email", "phone", "company", "subject", "message")
    return {f: getattr(lead, f) for f in fields if getattr(lead, f) is not None}


@needs_node
@pytest.mark.parametrize(
    "lead_id", ["hot-en-implant", "hot-en-corporate", "spam-empty", "inject-ru-discount"]
)
def test_js_request_matches_the_evaluated_python_request(lead_id):
    item = next(lead for lead in load_leads() if lead.id == lead_id)
    out = run_code("Build Claude request", {"body": body_of(item)})
    assert out["ok"], out
    python = build_request(item.to_lead(), MODEL)
    assert out["result"]["json"]["request"] == python


@needs_node
def test_js_normalizes_the_posted_fields():
    body = {
        "name": "  Aziz  ",
        "email": "",
        "phone": 998901234567,
        "source": "fax",
        "message": "x" * 6000,
        "extra": "ignored",
    }
    lead = run_code("Build Claude request", {"body": body})["result"]["json"]["lead"]
    assert lead["lead_id"] == "L42" and lead["source"] == "form"
    assert lead["name"] == "Aziz" and lead["email"] is None and lead["phone"] == "998901234567"
    assert len(lead["message"]) == 5000 and "extra" not in lead
    expected = Lead(
        source="form",
        received_at=datetime(2026, 10, 12, 10, 15),
        message="",
    )
    empty = run_code("Build Claude request", {"body": {}})["result"]["json"]["request"]
    assert empty == build_request(expected, MODEL)


def qualification(**overrides) -> dict:
    data = {
        "name": "Sarah Collins",
        "email": "sarah@example.com",
        "phone": "+998908112233",
        "company": None,
        "language": "en",
        "service_interest": "dental implant",
        "urgency": "high",
        "budget_signal": None,
        "tier": "hot",
        "lead_score": 92,
        "score_reasons": ["wants it this week"],
        "summary": "Wants an implant consultation this week.",
        "suggested_reply": "Hello Sarah ... Registan Smile Clinic",
    }
    return data | overrides


LEAD = {
    "lead_id": "L7",
    "source": "form",
    "received_at": "2026-10-12T10:15:00.000+05:00",
    "message": "Hi",
}


def claude_response(data=None, stop_reason="end_turn", text=None) -> dict:
    content = [{"type": "thinking", "thinking": "", "signature": "s"}]
    if data is not None or text is not None:
        content.append({"type": "text", "text": text if text is not None else json.dumps(data)})
    return {"content": content, "stop_reason": stop_reason, "usage": {"input_tokens": 1}}


def parse(response: dict, lead: dict = LEAD) -> dict:
    return run_code("Parse qualification", response, {"Build Claude request": {"lead": lead}})


SHEET_COLUMNS = [
    "received_at",
    "lead_id",
    "tier",
    "lead_score",
    "name",
    "email",
    "phone",
    "company",
    "language",
    "service_interest",
    "urgency",
    "budget_signal",
    "summary",
    "score_reasons",
    "suggested_reply",
    "reply_draft",
    "status",
    "source",
    "message",
    "contact_key",
]


@needs_node
def test_js_parses_a_hot_lead_and_writes_the_alert():
    out = parse(claude_response(qualification()))
    assert out["ok"], out
    item = out["result"]["json"]
    assert (item["lead_id"], item["tier"], item["lead_score"]) == ("L7", "hot", 92)
    assert item["telegram_text"].splitlines() == [
        "🔥 Hot lead · score 92",
        "Sarah Collins · sarah@example.com · +998908112233",
        "Service: dental implant · urgency: high",
        "Wants an implant consultation this week.",
        "Source: website form · L7",
    ]


@needs_node
def test_js_builds_the_sheet_row_and_the_draft():
    item = parse(claude_response(qualification()))["result"]["json"]
    assert item["draft"] is True
    assert item["reply_subject"] == "Registan Smile Clinic: reply to your request"
    row = item["sheet_row"]
    assert list(row) == SHEET_COLUMNS
    assert row == {
        "received_at": "2026-10-12 10:15",
        "lead_id": "L7",
        "tier": "hot",
        "lead_score": 92,
        "name": "Sarah Collins",
        "email": "sarah@example.com",
        "phone": "+998908112233",
        "company": "",
        "language": "en",
        "service_interest": "dental implant",
        "urgency": "high",
        "budget_signal": "",
        "summary": "Wants an implant consultation this week.",
        "score_reasons": "wants it this week",
        "suggested_reply": "Hello Sarah ... Registan Smile Clinic",
        "reply_draft": "Gmail draft",
        "status": "new",
        "source": "website form",
        "message": "Hi",
        "contact_key": "sarah@example.com",
    }
    # The Sheet row node passes exactly these columns on.
    assert run_code("Sheet row", item) == {"ok": True, "result": {"json": row}}


@needs_node
@pytest.mark.parametrize(
    ("overrides", "contact_key", "draft"),
    [
        ({"email": "  Sarah@Example.COM "}, "sarah@example.com", True),
        ({"email": None, "phone": "+998 (90) 811-22-33"}, "+998908112233", False),
        ({"email": None, "phone": None}, "L7", False),
        ({"tier": "spam", "lead_score": 2, "suggested_reply": ""}, "sarah@example.com", False),
    ],
)
def test_js_contact_key_and_draft(overrides, contact_key, draft):
    item = parse(claude_response(qualification(**overrides)))["result"]["json"]
    assert (item["sheet_row"]["contact_key"], item["draft"]) == (contact_key, draft)
    assert item["sheet_row"]["reply_draft"] == ("Gmail draft" if draft else "")


@needs_node
def test_js_reply_subject_follows_the_lead():
    by_email = parse(claude_response(qualification()), LEAD | {"subject": "Implant price"})
    assert by_email["result"]["json"]["reply_subject"] == "Re: Implant price"
    russian = parse(claude_response(qualification(language="ru")))
    assert russian["result"]["json"]["reply_subject"] == (
        "Registan Smile Clinic: ответ на вашу заявку"
    )


@needs_node
def test_js_accepts_spam_without_a_reply():
    out = parse(claude_response(qualification(tier="spam", lead_score=1, suggested_reply="")))
    assert out["ok"] and out["result"]["json"]["tier"] == "spam"


@needs_node
@pytest.mark.parametrize(
    ("response", "message"),
    [
        (claude_response(stop_reason="refusal"), "declined"),
        (claude_response(qualification(), stop_reason="max_tokens"), "cut off"),
        (claude_response(), "No text block"),
        (claude_response(text="{not json"), "Invalid JSON"),
        (claude_response(qualification(tier="warm", lead_score=92)), "outside the warm range"),
        (claude_response(qualification(tier="vip")), "Unknown tier"),
        (claude_response(qualification(suggested_reply=" ")), "Empty suggested reply"),
    ],
)
def test_js_rejects_bad_answers(response, message):
    out = parse(response)
    assert not out["ok"] and message in out["error"] and "L7" in out["error"]
