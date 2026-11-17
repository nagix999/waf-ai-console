export const signatureExplanationLimits = Object.freeze({ entries: 200, explanation: 2000, source: 1000, bytes: 262144 });

export function emptySignatureExplanation() {
  return { waf_vendor: "", signature: "", signature_explanation: "", reference_source: "" };
}

export function signatureExplanationDraft(version) {
  return { name: "", change_reason: "", parent_id: version?.id || null,
    entries: (version?.entries || []).map(entry => ({ ...emptySignatureExplanation(), ...entry })) };
}

export function signatureEntryKey(entry) {
  // This is only a client-side duplicate hint. Unicode casefold and the final
  // vendor/signature match are performed by the server, never inferred here.
  return JSON.stringify([entry.waf_vendor.trim().toLowerCase(), entry.signature.trim()]);
}

export function signatureEntryIssue(entry, entries = [], index = -1) {
  if (!entry.waf_vendor.trim() || !entry.signature.trim() || !entry.signature_explanation.trim()) return "required";
  if (entry.waf_vendor.length > 120 || entry.signature.length > 1000
      || entry.signature_explanation.length > signatureExplanationLimits.explanation
      || entry.reference_source.length > signatureExplanationLimits.source) return "length";
  if (entries.some((other, position) => position !== index && signatureEntryKey(other) === signatureEntryKey(entry))) return "duplicate";
  return "";
}

export function signatureVersionPayload(draft) {
  return { name: draft.name.trim(), change_reason: draft.change_reason.trim(), parent_id: draft.parent_id || null,
    entries: draft.entries.map(entry => ({ waf_vendor: entry.waf_vendor.trim(), signature: entry.signature.trim(),
      signature_explanation: entry.signature_explanation.trim(), reference_source: entry.reference_source.trim() })) };
}

export function signatureDraftIssue(draft) {
  if (!draft?.name.trim() || !draft.change_reason.trim()) return "version_required";
  if (draft.name.length > 120 || draft.change_reason.length > 1000 || draft.entries.length > signatureExplanationLimits.entries) return "length";
  for (let index = 0; index < draft.entries.length; index++) {
    const issue = signatureEntryIssue(draft.entries[index], draft.entries, index);
    if (issue) return issue;
  }
  if (new TextEncoder().encode(JSON.stringify(signatureVersionPayload(draft))).length > signatureExplanationLimits.bytes) return "bytes";
  return "";
}

export function filteredSignatureEntries(entries, vendor = "", query = "") {
  const normalized = query.trim().toLocaleLowerCase();
  return entries.map((entry, index) => ({ ...entry, _index: index, id: String(index) }))
    .filter(entry => (!vendor || entry.waf_vendor === vendor) && (!normalized
      || [entry.waf_vendor, entry.signature, entry.signature_explanation].some(value => value.toLocaleLowerCase().includes(normalized))));
}
