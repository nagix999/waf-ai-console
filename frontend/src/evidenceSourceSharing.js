// Reuse only a physically identical API-provided excerpt. This never selects,
// combines or reclassifies evidence, and never searches or expands raw input.
function sourceKey(item) {
  if (typeof item?.field !== "string" || !item.field || typeof item?.excerpt !== "string"
      || !Array.isArray(item.source_spans) || !item.source_spans.length) return null;
  const spans = [];
  for (const span of item.source_spans) {
    if (!span || typeof span !== "object" || Array.isArray(span)
        || Object.keys(span).length !== 2 || !Object.hasOwn(span, "start") || !Object.hasOwn(span, "end")
        || !Number.isSafeInteger(span.start) || !Number.isSafeInteger(span.end)
        || span.start < 0 || span.end <= span.start) return null;
    spans.push([span.start, span.end]);
  }
  return JSON.stringify([item.field, item.excerpt, spans]);
}

export function evidenceSourceReferences(items) {
  const owners = new Map();
  return items.map((item, index) => {
    const key = sourceKey(item);
    if (key !== null && owners.has(key)) return { item, index, sourceIndex: owners.get(key) };
    if (key !== null) owners.set(key, index);
    return { item, index, sourceIndex: index };
  });
}

export function sharedSourceText(number, words = ko => ko) {
  return words(`같은 원문은 근거 ${number}에서 확인할 수 있습니다.`, `The source excerpt is shared with evidence ${number}.`);
}
