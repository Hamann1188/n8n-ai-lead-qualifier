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
workflows/   lead-intake.json · error-alert.json   (exported, no credentials)
prompts/     qualify.md            (source of truth)
schemas/     lead.schema.json      (source of truth)
tools/       sync_workflow.py · send_test_leads.py · eval.py
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
| Sync prompt and schema into the workflow | `uv run python tools/sync_workflow.py` (CI: `--check`) |
| Send test leads | `uv run python tools/send_test_leads.py` |
| Eval (real API, costs money) | `uv run python tools/eval.py` |
| Lint / tests | `uv run ruff check .` · `uv run pytest` |

## Repo rules

- `prompts/qualify.md` and `schemas/lead.schema.json` are the only source of truth. Edit them, then run `sync_workflow.py`; never hand-edit the prompt inside the workflow JSON.
- Exported workflows must contain no secrets. Check every export with `git diff` before committing.
- The Claude call uses structured outputs (`output_config.format`) and explicit effort `low`; it never sends forced `tool_choice` or disables thinking. Read the `text` block by type, not `content[0]`.
- `tools/eval.py` creates the client with explicit `api_key` and `base_url` from settings (prefix `LEADQ_`), never from `ANTHROPIC_*` environment variables (see `../CLAUDE.md`, Headroom).
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
- [ ] 1 Infrastructure
- [ ] 2 Prompt and eval
- [ ] 3 Main workflow
- [ ] 4 Credentials guide
- [ ] 5 Error workflow
- [ ] 6 README and video
