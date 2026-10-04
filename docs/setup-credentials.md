# Setting up credentials

This guide takes a fresh clone to a working "Lead intake" workflow. You need about 30 minutes, a Google account, a Telegram account and an Anthropic API key.

| What | Where it lives | How it gets into n8n |
|---|---|---|
| Webhook secret | `.env`: `LEADQ_WEBHOOK_SECRET` | `deploy` creates the "Lead webhook secret" credential |
| Anthropic API key | `.env`: `LEADQ_ANTHROPIC_API_KEY` | `deploy` creates the "Anthropic API key" credential |
| Telegram bot token and chat id | `.env`: `LEADQ_TELEGRAM_BOT_TOKEN`, `LEADQ_TELEGRAM_CHAT_ID` | `deploy` creates the "Telegram bot" credential and fills in the chat id |
| Google Sheets and Gmail access | n8n's credential store only | You sign in with Google in the n8n UI once; `deploy` finds those credentials |
| Spreadsheet id | `.env`: `LEADQ_GOOGLE_SHEET_ID` | `deploy` fills it in |

n8n encrypts every credential with `N8N_ENCRYPTION_KEY`. `deploy` passes secrets through stdin and never prints them. Until the Google part is done, `deploy` keeps the two Google nodes disabled and the rest of the workflow runs normally.

Commands below are for PowerShell on Windows with Docker inside WSL (`wsl -d Ubuntu -- docker compose`). On Linux or macOS, drop the `wsl -d Ubuntu --` prefix.

## 1. `.env` and n8n

1. Copy `.env.example` to `.env`.
2. Fill in `N8N_ENCRYPTION_KEY`, `POSTGRES_PASSWORD` and `LEADQ_WEBHOOK_SECRET` with random values. Run this once per value:

   ```powershell
   uv run python -c "import secrets; print(secrets.token_hex(32))"
   ```

   Never change `N8N_ENCRYPTION_KEY` afterwards: n8n couldn't decrypt the credentials it already stores.
3. On Windows with WSL, set `LEADQ_COMPOSE_COMMAND="wsl -d Ubuntu -- docker compose"`.
4. Start n8n and open http://localhost:5678. The first visit asks you to create the owner account.

   ```powershell
   wsl -d Ubuntu -- docker compose up -d
   ```

## 2. Anthropic API key

1. Sign in at https://platform.claude.com and add credit under **Billing**. One lead costs about $0.016.
2. Open **API keys**, choose **Create key** and copy it.
3. Put it into `.env` as `LEADQ_ANTHROPIC_API_KEY`.

## 3. Telegram bot and sales group

1. In Telegram, message [@BotFather](https://t.me/BotFather), send `/newbot` and follow the prompts. Put the token it gives you into `.env` as `LEADQ_TELEGRAM_BOT_TOKEN`.
2. Create a group for the sales team and add the bot to it.
3. Find the group's chat id:
   1. In the group, send `/start@<your_bot_username>`.
   2. In a browser, open `https://api.telegram.org/bot<token>/getUpdates`, using your bot token.
   3. Find `"chat":{"id":-...` with your group's title. Copy the number, including the minus sign, into `.env` as `LEADQ_TELEGRAM_CHAT_ID`.

   If `result` is empty, send the command again. If another program is polling the same bot, stop it for a moment: Telegram delivers each update to one reader only.

## 4. Google Cloud project

Self-hosted n8n needs your own OAuth client. You only do this once; the same client serves both Sheets and Gmail.

1. Open https://console.cloud.google.com. In the project dropdown choose **New project**, name it (for example "Lead qualifier") and choose **Create**. Make sure the new project is selected at the top.
2. Enable three APIs. Go to **APIs & Services > Library**, search for each one and choose **Enable**:
   - **Google Sheets API**;
   - **Google Drive API**, which the Sheets node also needs;
   - **Gmail API**.
3. Set up the consent screen. Go to **APIs & Services > OAuth consent screen** and choose **Get started**:
   1. **App name**: anything, for example "Lead qualifier". **User support email**: your address. Choose **Next**.
   2. **Audience**: **External**. Choose **Next**. Pick **Internal** instead if the account belongs to a Google Workspace organisation and only its users will sign in; Internal has no test-user list and no 7-day expiry.
   3. **Contact information**: your address. Choose **Next**, accept the policy, then **Continue** and **Create**.
   4. Open **Audience**. Under **Test users** choose **Add users** and add the Google account whose Sheets and Gmail the workflow will use. With an External app in Testing status, any other account gets "access denied".

   You don't need to add scopes under **Data Access** or a domain under **Branding**: n8n asks for its scopes at sign-in, and localhost needs no domain.
4. Create the OAuth client. Go to **APIs & Services > Credentials**, choose **Create credentials > OAuth client ID**:
   1. **Application type**: **Web application**. **Name**: anything, for example "n8n local".
   2. Under **Authorized redirect URIs** choose **Add URI** and paste the redirect URL that n8n shows in its credential dialog (step 5). For this repo's setup it is:

      ```text
      http://localhost:5678/rest/oauth2-credential/callback
      ```

   3. Choose **Create**. Keep the **Client ID** and **Client secret** dialog open, or download the JSON. These are secrets: paste them only into n8n.

## 5. Google credentials in n8n

Create two credentials in the n8n UI. Use the same Client ID and secret in both.

1. In n8n, open **Overview > Credentials** (or **Create > Credential**) and search for **Google Sheets OAuth2 API**.
2. Check that the **OAuth Redirect URL** in the dialog matches the URI you added in Google. If n8n runs on another host or port, use the URL n8n shows and add it in Google too.
3. Paste the **Client ID** and **Client Secret**. Choose **Sign in with Google** and pick the test user account.
   - "Google hasn't verified this app" is expected for your own app in Testing status: choose **Continue**.
   - Allow every permission on the consent page.
4. When the dialog says the account is connected, choose **Save**.
5. Repeat steps 1–4 for **Gmail OAuth2 API**.

The credential names don't matter: `deploy` finds them by type. If there are several of one type, it takes the most recently updated one.

## 6. The Leads spreadsheet

1. Signed in as the same Google account, create a blank spreadsheet (https://sheets.new) and name it, for example, "Leads".
2. Leave the first tab empty. With the first lead, the workflow writes the header row itself, and every later lead fills the columns by name (see `docs/ARCHITECTURE.md`, section 5).
3. Copy the id from the address bar, the part between `/d/` and `/edit`:

   ```text
   https://docs.google.com/spreadsheets/d/<this part>/edit
   ```

   Put it into `.env` as `LEADQ_GOOGLE_SHEET_ID`.

## 7. Deploy and check

```powershell
uv run python -m leadq.n8n deploy
```

The output must not contain any "stays disabled" note. If it does, the note says what is missing: a credential type, or `LEADQ_GOOGLE_SHEET_ID`.

Then send the four default test leads (four Claude calls, about $0.07):

```powershell
uv run python -m leadq.send_test_leads
```

Check the result:

- **Telegram:** the hot lead appears in the sales group.
- **Google Sheets:** a header row and one row per lead. Sending the same lead again updates its row instead of adding one: the match is on `contact_key`.
- **Gmail:** under **Drafts**, a reply to every lead that gave an email address, except spam. Nothing is sent; a person reviews and sends each draft.
- **n8n:** **Executions** shows every run as succeeded.

## Keeping it running

- **7-day sign-in limit.** An External app in Testing status gets Google tokens that expire after 7 days. The Google nodes then fail with an authorisation error. To fix it, open each Google credential in n8n, choose **Sign in with Google** again and save. For a client, either use an **Internal** app in their Google Workspace, or publish the app (**Audience > Publish app**). Publishing an External app with Gmail scopes requires Google's verification.
- **Rotating a secret** from `.env`: change it and run `deploy` again. Credentials keep their ids, so the workflow needs no edits.
- **Moving to a server.** Set `WEBHOOK_URL` in `docker-compose.yml` to the public URL. Then add the new OAuth redirect URL (shown in the n8n credential dialog) to the Google OAuth client and sign in again.

## Troubleshooting

| Symptom | Cause and fix |
|---|---|
| `redirect_uri_mismatch` at Google sign-in | The redirect URI in the Google OAuth client differs from the one n8n shows. Copy n8n's exactly, including `http`, host and port |
| `access_denied` / "has not completed the Google verification process" | The account isn't in **Audience > Test users** |
| `invalid_client` | The Client ID or secret was pasted with a typo or a space. Copy them again |
| A Google node fails with a 403 about an API | That API isn't enabled in the project (step 4.2). Enable it and wait a minute |
| A Google node fails with "Requested entity was not found" | `LEADQ_GOOGLE_SHEET_ID` is wrong, or the spreadsheet belongs to another account. Share it with the signed-in account or fix the id, then deploy |
| Webhook answers 403 | The `X-Webhook-Secret` header is missing or wrong |
| Webhook answers 404 | The workflow isn't published, or n8n wasn't restarted. Run `deploy` again |
| No Telegram alert | The bot isn't in the group, or the chat id is wrong. Repeat step 3 |
