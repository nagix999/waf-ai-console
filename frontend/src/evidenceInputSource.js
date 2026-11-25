import { recordedInputFields } from "./inferenceDetail.js";

const scalarText = value => typeof value === "string" ? value : typeof value === "number" || typeof value === "boolean" ? String(value) : null;
const normalizedField = field => field.replace(/^event\./, "").replace(/\[(0|[1-9][0-9]*)\]/g, ".$1");
const childField = (parent, key) => /^[A-Za-z_0-9]+$/.test(key) ? `${parent}.${key}` : `${parent}[${JSON.stringify(key)}]`;

function ownPath(value, path) {
  for (const part of path.split(".")) {
    if (value === null || typeof value !== "object" || !Object.hasOwn(value, part)) return undefined;
    value = value[part];
  }
  return value;
}

export function inputFieldRows(detail, event) {
  const rows = Object.entries(recordedInputFields(detail)).map(([field, value]) => ({ id: field, field, value, text: scalarText(value) }));
  const pending = Object.entries(event?.extra_fields || {}).reverse().map(([key, value]) => ({ field: childField("extra_fields", key), value }));
  // Iterative traversal keeps deeply nested, untrusted inputs out of the call stack.
  while (pending.length) {
    const item = pending.pop();
    if (item.value !== null && typeof item.value === "object" && Object.keys(item.value).length) {
      const children = Object.entries(item.value);
      for (let index = children.length - 1; index >= 0; index--) {
        const [key, value] = children[index];
        pending.push({ field: childField(item.field, key), value });
      }
    } else rows.push({ id: item.field, ...item, text: scalarText(item.value) });
  }
  return rows;
}

function uniqueMatch(text, excerpt, start = 0, end = text.length) {
  const position = text.indexOf(excerpt, start);
  if (position < start || position + excerpt.length > end) return null;
  const next = text.indexOf(excerpt, position + 1);
  return next !== -1 && next + excerpt.length <= end ? null : { start: position, end: position + excerpt.length };
}

// Historical records may lack positions. Only a conservative, literal HTTP
// boundary is usable as a fallback; escaped captures and ambiguous fields stay
// unhighlighted. This does not replace the parser recorded with the analysis.
function literalHttpRange(raw, field) {
  const line = /^([A-Z]+)[ \t]+(\S+)[ \t]+HTTP\/\d(?:\.\d)?(?:\r\n|\n)/.exec(raw);
  if (!line) return null;
  const lineEnd = line[0].length - (line[0].endsWith("\r\n") ? 2 : 1);
  const methodEnd = line[1].length, uriStart = line[0].indexOf(line[2], methodEnd), uriEnd = uriStart + line[2].length;
  if (field === "payload.request_line") return { start: 0, end: lineEnd };
  if (field === "payload.method") return { start: 0, end: methodEnd };
  if (/[#\x00-\x20\x7f]/.test(line[2]) || line[2].includes("\\n") || line[2].includes("\\r")) return null;
  if (["payload.uri", "payload.request_target"].includes(field)) return { start: uriStart, end: uriEnd };
  if (field === "payload.protocol") return { start: lineEnd - /HTTP\/\d(?:\.\d)?$/.exec(line[0].slice(0, lineEnd))[0].length, end: lineEnd };
  const queryStart = line[2].indexOf("?");
  if (field === "payload.path") {
    const pathEnd = queryStart < 0 ? uriEnd : uriStart + queryStart;
    const prefix = /^https?:\/\/[^/]*/.exec(raw.slice(uriStart, pathEnd));
    if (!line[2].startsWith("/") && !prefix) return null;
    return { start: uriStart + (prefix?.[0].length || 0), end: pathEnd };
  }
  if (field === "payload.query") return queryStart < 0 ? null : { start: uriStart + queryStart + 1, end: uriEnd };
  if (field.startsWith("payload.query.") && queryStart >= 0) {
    const key = field.slice(14), found = []; let start = uriStart + queryStart + 1;
    for (const pair of line[2].slice(queryStart + 1).split("&")) {
      if (pair.split("=", 1)[0] === key) found.push({ start, end: start + pair.length });
      start += pair.length + 1;
    }
    return found.length === 1 ? found[0] : null;
  }
  const separator = /\r\n\r\n|\n\n/.exec(raw);
  if (!separator || separator.index < lineEnd) return null;
  if (field === "payload.body") return { start: separator.index + separator[0].length, end: raw.length };
  if (field === "payload.headers") return { start: line[0].length, end: separator.index };
  if (field.startsWith("payload.headers.")) {
    const header = field.slice(16).toLowerCase(), matches = [];
    let start = line[0].length;
    for (const text of raw.slice(start, separator.index).split(/\r\n|\n/)) {
      const colon = text.indexOf(":");
      if (colon > 0 && text.slice(0, colon).toLowerCase() === header) {
        matches.push({ start, end: start + text.length });
      }
      start += text.length + (raw.slice(start + text.length, start + text.length + 2) === "\r\n" ? 2 : 1);
    }
    return matches.length === 1 ? matches[0] : null;
  }
  return null;
}

function recordedRanges(raw, excerpt, spans) {
  // Server offsets count Unicode code points. JS slices count UTF-16 units.
  const offsets = [0];
  for (const character of raw) offsets.push(offsets[offsets.length - 1] + character.length);
  const ranges = [];
  for (const span of spans) {
    if (!span || typeof span !== "object" || Array.isArray(span) || Object.keys(span).length !== 2
        || !Number.isSafeInteger(span.start) || !Number.isSafeInteger(span.end)
        || span.start < 0 || span.end <= span.start || span.end >= offsets.length) return null;
    const range = { start: offsets[span.start], end: offsets[span.end] };
    if (raw.slice(range.start, range.end) !== excerpt) return null;
    if (!ranges.some(previous => previous.start === range.start && previous.end === range.end)) ranges.push(range);
  }
  ranges.sort((a, b) => a.start - b.start || a.end - b.end);
  if (ranges.some((range, index) => index > 0 && range.start < ranges[index - 1].end)) return null;
  return ranges;
}

// This resolver only connects an exact source. It never decodes, normalizes,
// searches another field, or substitutes the first of repeated occurrences.
export function resolveEvidenceInput(event, detail, target) {
  if (!target || typeof target.field !== "string" || typeof target.excerpt !== "string" || !target.excerpt) return null;
  const field = normalizedField(target.field), excerpt = target.excerpt;
  const payload = field === "payload" || field === "raw_payload" || field.startsWith("payload.");
  const base = { field, section: payload ? "payload" : "fields", found: false, ranges: [], reason: "unverified" };
  if (payload) {
    const raw = event?.payload;
    if (typeof raw !== "string") return { ...base, reason: "unavailable" };
    if (!["payload", "raw_payload"].includes(field) && !/^payload\.(?:request_line|method|uri|request_target|path|protocol|body|query(?:\..+)?|headers(?:\..+)?)$/.test(field)) return base;
    if (Object.hasOwn(target, "source_spans") && !Array.isArray(target.source_spans)) return { ...base, reason: "invalid_positions" };
    if (target.source_spans?.length) {
      const ranges = recordedRanges(raw, excerpt, target.source_spans);
      const boundary = literalHttpRange(raw, field);
      if (ranges && boundary && ranges.some(range => range.start < boundary.start || range.end > boundary.end)) return { ...base, reason: "invalid_positions" };
      return ranges ? { ...base, found: true, ranges, reason: "recorded_positions" } : { ...base, reason: "invalid_positions" };
    }
    const boundary = ["payload", "raw_payload"].includes(field) ? { start: 0, end: raw.length } : literalHttpRange(raw, field);
    const match = boundary && uniqueMatch(raw, excerpt, boundary.start, boundary.end);
    return match ? { ...base, found: true, ranges: [match], reason: "unique_field_excerpt" } : base;
  }
  const basics = recordedInputFields(detail);
  let value, rowField = field;
  if (Object.hasOwn(basics, field)) value = basics[field];
  else {
    const path = field.startsWith("extra_fields.") ? field.slice(13) : field;
    value = ownPath(event?.extra_fields, path); rowField = `extra_fields.${path}`;
  }
  const text = scalarText(value), range = text !== null && uniqueMatch(text, excerpt);
  return { ...base, rowField, value: text, ...(range ? { found: true, ranges: [range], reason: "unique_field_excerpt" } : {}) };
}

export function splitHighlightedText(text, ranges = []) {
  const parts = []; let start = 0;
  for (const range of ranges) {
    if (range.start > start) parts.push({ text: text.slice(start, range.start), highlighted: false });
    parts.push({ text: text.slice(range.start, range.end), highlighted: true }); start = range.end;
  }
  if (start < text.length || !parts.length) parts.push({ text: text.slice(start), highlighted: false });
  return parts;
}
