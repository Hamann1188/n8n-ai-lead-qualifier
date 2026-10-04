// "Parse qualification": check Claude's answer and shape the lead for the next steps.
// Mirrors leadq/qualify.py (parse_response, validate). Any problem throws, so the run
// fails and the error workflow alerts the team instead of dropping the lead.

const lead = $('Build Claude request').item.json.lead;
const response = $json;

if (response.stop_reason === 'refusal') {
  throw new Error(`Claude declined to qualify lead ${lead.lead_id}`);
}
if (response.stop_reason === 'max_tokens') {
  throw new Error(`Claude's answer for lead ${lead.lead_id} was cut off (max_tokens)`);
}
// Thinking blocks come first; read the answer by type, not position.
const block = (response.content ?? []).find((b) => b.type === 'text');
if (!block) throw new Error(`No text block in Claude's answer for lead ${lead.lead_id}`);

let q;
try {
  q = JSON.parse(block.text);
} catch (error) {
  throw new Error(`Invalid JSON from Claude for lead ${lead.lead_id}: ${error.message}`);
}
const range = TIER_RANGES[q.tier];
if (!range) throw new Error(`Unknown tier ${JSON.stringify(q.tier)} for lead ${lead.lead_id}`);
if (!Number.isInteger(q.lead_score) || q.lead_score < range[0] || q.lead_score > range[1]) {
  throw new Error(
    `Score ${q.lead_score} is outside the ${q.tier} range ${range[0]}-${range[1]} for lead ${lead.lead_id}`,
  );
}
if (q.tier !== 'spam' && !(q.suggested_reply ?? '').trim()) {
  throw new Error(`Empty suggested reply for ${q.tier} lead ${lead.lead_id}`);
}

const contact = [q.name, q.email, q.phone].filter(Boolean).join(' · ') || 'no contact details';
const email = (q.email ?? '').trim().toLowerCase();
const phoneDigits = (q.phone ?? '').replace(/\D/g, '');
// Repeat leads update their row instead of adding one (ARCHITECTURE ADR-9).
const contact_key = email || (phoneDigits ? `+${phoneDigits}` : lead.lead_id);

const REPLY_SUBJECTS = {
  ru: 'Registan Smile Clinic: ответ на вашу заявку',
  uz: "Registan Smile Clinic: so'rovingizga javob",
  en: 'Registan Smile Clinic: reply to your request',
};
const reply_subject = lead.subject
  ? `Re: ${lead.subject}`
  : REPLY_SUBJECTS[q.language] ?? REPLY_SUBJECTS.en;
const draft = Boolean(email) && q.tier !== 'spam';

const sheet_row = {
  received_at: lead.received_at.slice(0, 16).replace('T', ' '),
  lead_id: lead.lead_id,
  tier: q.tier,
  lead_score: q.lead_score,
  name: q.name ?? '',
  email: q.email ?? '',
  phone: q.phone ?? '',
  company: q.company ?? '',
  language: q.language,
  service_interest: q.service_interest,
  urgency: q.urgency,
  budget_signal: q.budget_signal ?? '',
  summary: q.summary,
  score_reasons: (q.score_reasons ?? []).join('; '),
  suggested_reply: q.suggested_reply,
  reply_draft: draft ? 'Gmail draft' : '',
  status: 'new',
  source: SOURCES[lead.source],
  message: lead.message,
  contact_key,
};
// The Telegram node sends HTML (n8n would default to Markdown, which a `_` or `*` in the
// lead's text breaks), so every value is escaped.
const html = (value) =>
  String(value).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
const telegram_text = [
  `<b>🔥 Hot lead · score ${q.lead_score}</b>`,
  html(contact),
  `Service: ${html(q.service_interest)} · urgency: ${html(q.urgency)}`,
  html(q.summary),
  `Source: ${SOURCES[lead.source]} · ${lead.lead_id}`,
].join('\n');

return {
  json: {
    lead_id: lead.lead_id,
    received_at: lead.received_at,
    source: lead.source,
    message: lead.message,
    ...q,
    telegram_text,
    reply_subject,
    draft,
    sheet_row,
    usage: response.usage ?? null,
  },
};
