# n8n AI Lead Qualifier

An n8n workflow with Claude for a (fictional) clinic. Each lead from a form or email is:

- parsed and scored hot, warm, cold or spam;
- logged to Google Sheets;
- reported in Telegram if it is hot;
- answered with a draft reply that a person reviews before sending.

Portfolio demo 3 of 3; shared rules and environment notes are in `../CLAUDE.md`.

The target architecture is in `docs/ARCHITECTURE.md`. Read it before changing the workflow shape, the Claude step or the schema, and record every deviation there as a decision (section 9).

## Stack

n8n (Docker, pinned version) + PostgreSQL 17 for n8n state; Claude Messages API via the HTTP Request node; Google Sheets, Gmail and Telegram nodes. Python 3.13 (uv) tooling for sync, test leads and evals, using the `anthropic` SDK, pytest and ruff.

## Layout

```
workflows/   lead-intake.json · error-alert.json   (exported, no credentials; bind-mounted at /workflows)
prompts/     qualify.md            (source of truth)
schemas/     lead.schema.json      (source of truth)
src/leadq/   config.py · llm.py · sync_workflow.py · send_test_leads.py · eval.py   (Python tooling)
evals/       leads.yaml · results/
docs/        ARCHITECTURE.md · setup-credentials.md
tests/
```

## Commands (PowerShell, from the repo root)

| Task | Command |
|---|---|
| Start n8n | `wsl -d Ubuntu -- docker compose up -d` → http://localhost:5678 |
| Import workflows | `wsl -d Ubuntu -- docker compose exec n8n n8n import:workflow --separate --input=/workflows` |
| Export workflows | `wsl -d Ubuntu -- docker compose exec n8n n8n export:workflow --all --separate --output=/workflows` |
| Sync prompt, schema and code into the workflow | `uv run python -m leadq.n8n sync` (CI: `--check`) |
| Deploy credentials + workflow into n8n | `uv run python -m leadq.n8n deploy` (reads `.env`, prints no secrets, restarts n8n) |
| Send test leads (real Claude calls) | `uv run python -m leadq.send_test_leads [--only id,id \| --all]` |
| Code-node tests locally (Node.js from the n8n container) | `$env:LEADQ_NODE_COMMAND = "wsl -d Ubuntu -- docker compose exec -T n8n node"; uv run pytest` |
| Eval (real API, about $0.65) | `uv run python -m leadq.eval [--only id,id]`: 40 leads; writes `evals/results/latest.md` (committed) and `latest.jsonl` (ignored); exits 1 if a target is missed. Ask the owner before running: it spends their balance |
| n8n database state (counts only) | `docker compose exec -T db sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB"'` |
| Lint / tests | `uv run ruff check .` · `uv run pytest` |

## Repo rules

- `prompts/qualify.md`, `schemas/lead.schema.json` and `workflows/code/*.js` are the only source of truth. Edit them, then run `python -m leadq.n8n sync`. Never hand-edit the Code nodes inside the workflow JSON or in the n8n UI.
- A change to how the lead message or request is built must be made in both `leadq/qualify.py` and `workflows/code/build_request.js`. `tests/test_workflow.py` fails if they differ, but only when Node.js is available (CI, or `LEADQ_NODE_COMMAND`).
- The committed workflow keeps the placeholders `TELEGRAM_CHAT_ID`, `GOOGLE_SHEET_ID`, `GOOGLE_SHEETS_CREDENTIAL` and `GMAIL_CREDENTIAL`; deploy fills them in. Credentials from `.env` are referenced by fixed ids (`leadqWebhookAuth`, `leadqAnthropicKy`, `leadqTelegramBot`).
- The Google credentials can't come from `.env`: the owner creates them in the n8n UI ("Google Sheets OAuth2 API", "Gmail OAuth2 API", Sign in with Google). Deploy finds them by type in n8n's `credentials_entity` table and keeps the Google nodes disabled until they and `LEADQ_GOOGLE_SHEET_ID` exist (ADR-10).
- Commands that go through `wsl -d Ubuntu -- ...` must not contain `$`: the WSL shell expands it before the container sees it (a test enforces this for deploy).
- Every Telegram node sets `parse_mode: HTML`, and the code escapes `&`, `<` and `>` in every value it puts into a message (ADR-11). Without it n8n sends Markdown, and lead text breaks it.
- Keep both workflows published: n8n 2.41 won't run an unpublished error workflow. The error workflow only fires for production (webhook) runs, so test it through the webhook, not the editor.
- Never rewrite repo files with PowerShell 5.1 `Get-Content`/`Set-Content`: it reads UTF-8 without a BOM as cp1251 and corrupts every non-ASCII character. Use the Edit tool or Python.
- Exported workflows must contain no secrets. Check every export with `git diff` before committing.
- The Claude call uses structured outputs (`output_config.format`) and explicit effort `low`; it never sends forced `tool_choice` or disables thinking. Read the `text` block by type, not `content[0]`.
- The tooling creates the Claude client only through `leadq.llm.make_client`, with explicit `api_key` and `base_url` from settings (prefix `LEADQ_`), never from `ANTHROPIC_*` environment variables (see `../CLAUDE.md`, Headroom).
- `.env` was created on 2026-10-04 with random `N8N_ENCRYPTION_KEY`, `POSTGRES_PASSWORD` and `LEADQ_WEBHOOK_SECRET` (never printed). Never change `N8N_ENCRYPTION_KEY`: the stored n8n credentials would become unreadable. The owner adds `LEADQ_ANTHROPIC_API_KEY` themselves.
- n8n is pinned to `n8nio/n8n:2.41.6`, which was `stable` on 2026-10-04, and runs on PostgreSQL 17 with no host port. Only n8n is published, at 127.0.0.1:5678. Nodes can't read environment variables (`N8N_BLOCK_ENV_ACCESS_IN_NODE=true`), so secrets go into n8n credentials.
- n8n node versions change between releases. Build or adjust nodes in the n8n UI and export the result. Hand-written workflow JSON is only a starting point and must be imported and opened in the UI before commit.
- Replies are drafts only; never add a node that sends email to a lead automatically.

## Build plan

Each step is one commit; tick it off in Status.

1. **Infrastructure:** compose (n8n pinned + postgres), `.env.example` (`N8N_ENCRYPTION_KEY`, webhook secret), owner account, uv tooling project, CI. *Accept:* n8n opens at localhost:5678 and survives `docker compose down` / `up` with data intact.
2. **Prompt and eval first:** `qualify.md`, schema, `evals/leads.yaml` (40 leads), `tools/eval.py` calling the API directly. *Accept:* the targets in ARCHITECTURE §7 are met before any n8n work.
3. **Main workflow:** form/webhook → normalize → dedupe → Claude → branches; export it and add the sync script. *Accept:* `send_test_leads.py` produces correct rows and alerts.
4. **Credentials guide:** Telegram bot, Google OAuth for Sheets and Gmail, Anthropic key in n8n credentials. *Accept:* someone can follow `docs/setup-credentials.md` from zero.
5. **Error workflow:** Error Trigger → Telegram alert; refusal and max_tokens routed to it. *Accept:* a forced failure (wrong key) produces an alert.
6. **README:** problem, demo video, diagram, setup, eval table, extensions; script for a 60–90 s video.

## Status

- [x] Target architecture and CLAUDE.md (2026-10-01)
- [x] 1 Infrastructure (2026-10-04):
  - compose with n8n 2.41.6 and PostgreSQL 17, the `leadq` uv project, CI, 5 tests;
  - n8n is reachable at localhost:5678. The owner account (created by the owner in the browser) and the schema survived `down`/`up`.
- [x] 2 Prompt and eval (2026-10-04):
  - `prompts/qualify.md`, `schemas/lead.schema.json`, `leadq/qualify.py` (request builder, parsing, validation), `evals/leads.yaml` (40 leads), `leadq/eval.py`;
  - 100% on every metric, exact tier 40/40, $0.016 per lead. I read every output;
  - the prompt was not tuned on the set.
- [x] 3 Main workflow (2026-10-04):
  - "Lead intake": webhook → build request → Claude → parse → respond, plus three branches: hot → Telegram, Google Sheets upsert by `contact_key`, Gmail draft. Also `leadq.n8n` sync/deploy, `send_test_leads`, 60 tests including the Code nodes in Node.js;
  - live check: webhook auth (403 without and with a wrong secret); hot, warm, cold and spam leads qualified through n8n in 6–14 s; the hot-lead alert arrived in the Telegram group. With the Google nodes disabled, the rest still ran (executions 5–6);
  - live check with Google (executions 7–12, all successful; the owner confirmed the sheet and Gmail): the header row was created on the empty sheet; 6 leads made 5 rows, because the repeated lead updated its row; 3 drafts were created, one per lead with an email; the email lead's draft got "Re: <subject>".
- [x] 4 Credentials guide (2026-10-04): `docs/setup-credentials.md`. The owner followed the Google part from it (project, 3 APIs, consent screen, OAuth client, two n8n credentials, sheet id), and deploy then enabled both Google nodes
- [x] 5 Error workflow (2026-10-04):
  - "Error alert" (Error Trigger → Format alert → Telegram); "Lead intake" names it as its error workflow; deploy imports and publishes both; 74 tests;
  - live check: with a wrong Anthropic key the webhook answered 500 and the alert run succeeded (executions 16–17); the real key was restored and checked (execution 18). Then the owner retried the failed run from the UI ("Retry with original workflow"): execution 19 (`retryOf` 16) succeeded, so a failed lead isn't lost. In n8n 2.41 the retry control is an unlabelled ↪ icon at the top right of the execution preview (tooltip "Retry execution"), also available via Ctrl+K → "retry";
  - found on the way: n8n 2.41 only runs a published error workflow, and the Telegram node defaults to Markdown, which broke on the `_` in a hint. Both Telegram nodes now send escaped HTML (ADR-11).
- [x] 6 README and video (2026-10-05). The sheet screenshot keeps the old numeric phone, which the owner chose to leave as is:
  - README on GitHub (6a249f7, checked rendered in Edge), with the owner's 4 screenshots in `docs/images/` (checked: no personal data; three cropped). The sheet screenshot showed phones turned into numbers, which led to ADR-12 (RAW cells). After that fix, a phone-only lead was sent twice (executions 29–30), and the owner checks that it has one row;
  - video script: `../demo-videos/n8n-ai-lead-qualifier.md`, with a local AI voice-over. The video is on YouTube, unlisted: https://youtu.be/qYkbc013J84 (2026-10-05). It is linked under the README intro as a clickable thumbnail.
