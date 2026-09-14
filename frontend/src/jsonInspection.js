// Presentation only: parsing serialized Agent records is explicit and never
// changes stored text, decodes HTTP payloads or executes inspected content.
export const JSON_PAGE_SIZE = 50;
export const JSON_PREVIEW_LENGTH = 320;
export const JSON_TREE_DEPTH = 16;

export const looksLikeJsonRecord = value => typeof value === "string" && /^[\x20\t\r\n]*[\[{]/.test(value);

function decimalValue(literal) {
  const match = /^(-?)(\d+)(?:\.(\d+))?(?:[eE]([+-]?\d+))?$/.exec(literal);
  if (!match || (match[4]?.length || 0) > 6) return null;
  const digits = (match[2] + (match[3] || "")).replace(/^0+/, "");
  if (!digits) return `${match[1]}0`;
  const significant = digits.replace(/0+$/, "");
  return `${match[1]}${significant}e${Number(match[4] || 0) - (match[3]?.length || 0) + digits.length - significant.length}`;
}

// Agent APIs return input/output as serialized JSON strings. Accept one strict
// object/array layer only; preserve raw text as the source of truth. Refuse
// duplicate keys and number rounding instead of silently altering the history.
export function parseJsonRecord(text) {
  if (!looksLikeJsonRecord(text) || text.length > 2097152) return null;
  try {
    const value = JSON.parse(text);
    if (value === null || typeof value !== "object") return null;
    const stack = [];
    let tokens = 0;
    for (const match of text.matchAll(/"(?:\\.|[^"\\])*"|-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?|[{}\[\]]/g)) {
      if (++tokens > 100000) return null;
      const literal = match[0];
      if (literal === "{" || literal === "[") {
        if (stack.length >= 128) return null;
        stack.push(literal === "{" ? new Set() : null);
      } else if (literal === "}" || literal === "]") stack.pop();
      else if (literal[0] === '"') {
        let end = match.index + literal.length;
        while (/[\x20\t\r\n]/.test(text[end] || "")) end += 1;
        if (text[end] === ":") {
          const key = JSON.parse(literal);
          if (stack.at(-1)?.has(key)) return null;
          stack.at(-1)?.add(key);
        }
      } else {
        const number = Number(literal);
        if (!Number.isFinite(number) || (Number.isInteger(number) && !Number.isSafeInteger(number)) || decimalValue(literal) !== decimalValue(String(number))) return null;
      }
    }
    return { value, text: JSON.stringify(value, null, 2) };
  } catch { return null; } // Never expose parser exceptions containing source text.
}

export function jsonType(value) {
  if (value === null) return "null";
  if (Array.isArray(value)) return "array";
  return typeof value;
}

export function jsonStringPreview(value, limit = JSON_PREVIEW_LENGTH) {
  let end = Math.min(value.length, limit);
  // Do not split a UTF-16 surrogate pair at the preview boundary.
  if (end < value.length && /[\uD800-\uDBFF]/.test(value[end - 1]) && /[\uDC00-\uDFFF]/.test(value[end])) end -= 1;
  return { text: JSON.stringify(value.slice(0, end)), remaining: value.length - end };
}

// Bound syntax highlighting; plain pretty JSON and full-text search remain
// available for larger records without creating thousands of DOM elements.
export function jsonTokens(text) {
  if (text.length > 262144) return [{ text, type: "plain", start: 0 }];
  const tokens = [];
  const pattern = /"(?:\\.|[^"\\])*"|-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?|\b(?:true|false|null)\b/g;
  let offset = 0;
  for (const match of text.matchAll(pattern)) {
    if (tokens.length >= 12000) return [{ text, type: "plain", start: 0 }];
    if (match.index > offset) tokens.push({ text: text.slice(offset, match.index), type: "plain", start: offset });
    const literal = match[0];
    const end = match.index + literal.length;
    const type = literal[0] === '"' ? (/^\s*:/.test(text.slice(end, end + 8)) ? "key" : "string")
      : literal === "null" ? "null" : ["true", "false"].includes(literal) ? "boolean" : "number";
    tokens.push({ text: literal, type, start: match.index });
    offset = end;
  }
  if (offset < text.length) tokens.push({ text: text.slice(offset), type: "plain", start: offset });
  return tokens;
}
