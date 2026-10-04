// "Format alert": turn the Error Trigger's data into a short Telegram message (HTML, so
// every value is escaped). n8n keeps the failed execution with the lead in it; the message
// links to it so a person can fix the cause and retry the run from there.

const HINTS = {
  'Qualify with Claude':
    'The Claude API call failed 3 times. 401: wrong Anthropic key (fix LEADQ_ANTHROPIC_API_KEY, run deploy). 400 about credit: top up the balance. 429 or 5xx: temporary, retry later.',
  'Parse qualification':
    "Claude's answer failed the checks (refusal, cut off or invalid). Read the lead in the execution and qualify it by hand.",
  'Alert sales in Telegram':
    'Check that the bot is still in the sales group and that LEADQ_TELEGRAM_CHAT_ID is right.',
  'Log to Google Sheets':
    'The Google sign-in may have expired (every 7 days in Testing mode): open the Google Sheets credential in n8n and sign in with Google again.',
  'Draft reply in Gmail':
    'The Google sign-in may have expired (every 7 days in Testing mode): open the Gmail credential in n8n and sign in with Google again.',
};
const MAX_ERROR = 600;

// A failed trigger node reports under `trigger`, without an execution id or url.
const execution = $json.execution ?? {};
const triggerError = $json.trigger?.error;
const node = execution.lastNodeExecuted ?? triggerError?.node?.name ?? 'unknown step';
const message = String(execution.error?.message ?? triggerError?.message ?? 'no error message');
const error = message.length > MAX_ERROR ? `${message.slice(0, MAX_ERROR)}…` : message;

const html = (value) =>
  String(value).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');

const lines = [
  `<b>⚠️ ${html($json.workflow?.name ?? 'A workflow')} failed</b>`,
  `Step: ${html(node)}`,
  `Error: ${html(error)}`,
];
if (HINTS[node]) lines.push(`What to do: ${html(HINTS[node])}`);
if (execution.url) {
  lines.push(`Execution ${html(execution.id)}: ${html(execution.url)}`);
  lines.push('The lead is saved in that execution: after the fix, open it and choose Retry.');
}
if (execution.retryOf) lines.push(`This run was a retry of execution ${html(execution.retryOf)}.`);

return { json: { text: lines.join('\n') } };
