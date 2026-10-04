// "Build Claude request": normalize the posted lead and build the Messages API body.
// Mirrors leadq/qualify.py (lead_message, build_request); tests/test_workflow_code.py
// checks that both produce the same request.

const LIMITS = { field: 200, message: 5000 };
const body = $json.body ?? {};

function field(value, limit) {
  if (value === undefined || value === null) return null;
  const text = String(value).trim();
  return text ? text.slice(0, limit) : null;
}

// The lead can't close or reopen our tags.
const clean = (text) => text.replaceAll('<lead>', '').replaceAll('</lead>', '').trim();

const now = $now.setZone('Asia/Tashkent').setLocale('en');
const lead = {
  lead_id: `L${$execution.id}`,
  source: body.source === 'email' ? 'email' : 'form',
  received_at: now.toISO(),
  name: field(body.name, LIMITS.field),
  email: field(body.email, LIMITS.field),
  phone: field(body.phone, LIMITS.field),
  company: field(body.company, LIMITS.field),
  subject: field(body.subject, LIMITS.field),
  message: field(body.message, LIMITS.message) ?? '',
};

const lines = [
  '<lead>',
  `source: ${SOURCES[lead.source]}`,
  `received: ${now.toFormat('cccc yyyy-LL-dd HH:mm')} (Asia/Tashkent)`,
];
for (const label of ['name', 'email', 'phone', 'company', 'subject']) {
  if (lead[label]) lines.push(`${label}: ${clean(lead[label])}`);
}
lines.push('message:', clean(lead.message) || '(empty)', '</lead>');

const request = {
  model: MODEL,
  max_tokens: MAX_TOKENS,
  system: SYSTEM_PROMPT,
  messages: [{ role: 'user', content: lines.join('\n') }],
  output_config: { effort: EFFORT, format: { type: 'json_schema', schema: OUTPUT_SCHEMA } },
};

return { json: { lead, request } };
