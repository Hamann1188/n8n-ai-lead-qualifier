# Architecture — n8n AI Lead Qualifier

## 1. Problem and scope

Inbound requests arrive from a website form and by email. Staff read each one, guess its priority and copy the data into a spreadsheet by hand. This workflow does it in seconds:

- extracts the contact details;
- scores the lead;
- logs it to Google Sheets;
- alerts the team in Telegram about hot leads;
- prepares a draft reply for a person to review.

**Goals**

- Visual n8n workflows the client can own and edit.
- Valid JSON from Claude on every call (structured outputs).
- Duplicates are detected; every failure triggers an alert.
- The prompt and output schema live in git as the single source of truth.
- A labelled eval set with published accuracy.

**Non-goals (v1)** — listed as extensions: CRM sync (HubSpot, amoCRM, Bitrix24), sending replies without human review.

## 2. System overview

```mermaid
flowchart LR
  F[n8n Form / website webhook] --> N[Normalize]
  M[Gmail trigger, optional] --> N
  N --> D{Duplicate?}
  D -- new --> Q[Claude: qualify<br/>HTTP Request, structured output]
  Q --> S[Append row to Google Sheets]
  Q --> R{tier = hot?}
  R -- yes --> T[Telegram alert to sales chat]
  Q --> G[Gmail draft with suggested reply]
  E[Error Trigger] --> TA[Telegram alert]
```

## 3. Components

| Path | Purpose |
|---|---|
| `docker-compose.yml` | n8n (pinned image version) + PostgreSQL for n8n state |
| `workflows/lead-intake.json` | Main workflow, exported without credentials |
| `workflows/error-alert.json` | Error workflow |
| `prompts/qualify.md` | System prompt (source of truth) |
| `schemas/lead.schema.json` | Output JSON schema (source of truth) |
| `tools/sync_workflow.py` | Writes prompt + schema into the workflow JSON; `--check` mode fails CI if they drift |
| `tools/send_test_leads.py` | Posts synthetic leads to the webhook |
| `evals/leads.yaml` · `tools/eval.py` | Labelled leads → tier accuracy and JSON validity, using the same prompt, schema and model |
| `docs/setup-credentials.md` | Step by step: Anthropic key, Telegram bot, Google OAuth (Sheets + Gmail) |

## 4. Claude step

- **Request:**
  - n8n HTTP Request node → `POST https://api.anthropic.com/v1/messages`;
  - headers: `x-api-key` (from an n8n credential) and `anthropic-version: 2023-06-01`.
- **Body:**
  - `model: claude-opus-5-5`;
  - `max_tokens: 4000`, leaving room for thinking;
  - `output_config: {effort: "low", format: {type: "json_schema", schema: …}}`;
  - the system prompt;
  - the normalized lead as the user message, wrapped in tags.
- **Output fields:**
  - `name`, `email`, `phone`, `company`, `language` (ru|uz|en|other);
  - `service_interest`, `urgency` (low|medium|high), `budget_signal`;
  - `lead_score` (0–100), `tier` (hot|warm|cold|spam), `score_reasons[]`;
  - `summary`, and `suggested_reply` written in the lead's language.
- **Parsing:** take the `text` block from `content` by type (thinking blocks come first), then `JSON.parse`. On `stop_reason` `refusal` or `max_tokens` the item goes to the error branch.
- **Retries:** the node retries 3 times with backoff on 429 and 5xx.

## 5. Data

- **Google Sheet "Leads":** one row per lead with:
  - received at, source;
  - name, email, phone, company, language;
  - service interest, urgency, score, tier, summary;
  - status (`new`), draft link.
- **Duplicates:** the same email or phone within 30 days updates the existing row instead of adding a new one.

## 6. Security

- Credentials live only in n8n's encrypted credential store (`N8N_ENCRYPTION_KEY` in `.env`). Exported workflows reference credentials by name and contain no secrets.
- Lead text is untrusted. The prompt says to ignore instructions inside the lead, the output is constrained by the schema, and the model has no tools.
- The webhook requires a secret header.
- The n8n editor is bound to localhost and protected by the owner account.

## 7. Evaluation

40 synthetic leads in EN, RU and UZ, labelled hot / warm / cold / spam. The set includes spam and injections such as "mark me as hot".

| Metric | Target |
|---|---|
| Valid JSON | 100% |
| Tier accuracy | ≥ 85% |
| Hot lead classified as cold or spam | 0 |
| Cost per lead | reported |

## 8. Operations

- `wsl -d Ubuntu -- docker compose up -d` → n8n at http://localhost:5678.
- A script imports the workflows with `n8n import:workflow` via `docker compose exec`; then activate them in the UI.
- Workflow changes made in the UI are exported back to `workflows/` and committed.

## 9. Decisions

| ID | Decision | Why / trade-off |
|---|---|---|
| ADR-1 | HTTP Request node instead of n8n's built-in Anthropic node | Exact model ID, effort and structured outputs are available the day the API ships them. Trade-off: less "no-code", mitigated by clear node names and sticky notes |
| ADR-2 | Prompt and schema in git with a sync script | Changes are reviewable, and the eval uses exactly what production uses |
| ADR-3 | PostgreSQL for n8n state instead of the default SQLite | Production-like; survives container rebuilds |
| ADR-4 | Drafts, never auto-send | A person stays in the loop for anything sent to a customer |

## 10. Extensions (offer as add-ons)

CRM sync (HubSpot, amoCRM, Bitrix24) · WhatsApp and Instagram leads · automated follow-up sequences · weekly lead report · call-transcript qualification.
