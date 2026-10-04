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
const telegram_text = [
  `🔥 Hot lead · score ${q.lead_score}`,
  contact,
  `Service: ${q.service_interest} · urgency: ${q.urgency}`,
  q.summary,
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
    usage: response.usage ?? null,
  },
};
