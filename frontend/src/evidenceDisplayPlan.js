// One immutable display plan for screen and exports. No model calls or new
// evidence text: every explanation and source comes from a canonical ID.
import { analystText } from "./analystText.js";
import { presentationItems } from "./evidencePresentation.js";

const record = value => value !== null && typeof value === "object" && !Array.isArray(value);
const array = value => Array.isArray(value) ? value : [];
const directions = ["attack_request", "normal_request", "context"];
const roles = ["primary", "verifier"];
const reasons = new Set(["rank_top", "decision_issue", "counter_evidence", "inconclusive_directional", "context_auxiliary", "remaining_evidence", "fallback_original"]);
const emptyRelations = () => ({ version: "evidence-relations-v1", groups: [] });
const stableValue = value => Array.isArray(value) ? value.map(stableValue) : record(value)
  ? Object.fromEntries(Object.keys(value).sort().map(key => [key, stableValue(value[key])])) : value;

// Bounded content fingerprint only, never authentication or encryption. The
// same synchronous calculation is usable by the offline report formatter.
export function sha256Text(value) {
  const encodedBytes = [];
  for (const character of value) {
    let point = character.codePointAt(0); if (point >= 0xd800 && point <= 0xdfff) point = 0xfffd;
    if (point < 0x80) encodedBytes.push(point);
    else if (point < 0x800) encodedBytes.push(0xc0 | point >>> 6, 0x80 | point & 63);
    else if (point < 0x10000) encodedBytes.push(0xe0 | point >>> 12, 0x80 | point >>> 6 & 63, 0x80 | point & 63);
    else encodedBytes.push(0xf0 | point >>> 18, 0x80 | point >>> 12 & 63, 0x80 | point >>> 6 & 63, 0x80 | point & 63);
  }
  const bytes = Uint8Array.from(encodedBytes), size = Math.ceil((bytes.length + 9) / 64) * 64;
  const padded = new Uint8Array(size); padded.set(bytes); padded[bytes.length] = 0x80;
  const data = new DataView(padded.buffer); data.setUint32(size - 4, bytes.length * 8, false);
  data.setUint32(size - 8, Math.floor(bytes.length / 0x20000000), false);
  const state = [0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a, 0x510e527f, 0x9b05688c, 0x1f83d9ab, 0x5be0cd19];
  const constants = [0x428a2f98,0x71374491,0xb5c0fbcf,0xe9b5dba5,0x3956c25b,0x59f111f1,0x923f82a4,0xab1c5ed5,0xd807aa98,0x12835b01,0x243185be,0x550c7dc3,0x72be5d74,0x80deb1fe,0x9bdc06a7,0xc19bf174,0xe49b69c1,0xefbe4786,0x0fc19dc6,0x240ca1cc,0x2de92c6f,0x4a7484aa,0x5cb0a9dc,0x76f988da,0x983e5152,0xa831c66d,0xb00327c8,0xbf597fc7,0xc6e00bf3,0xd5a79147,0x06ca6351,0x14292967,0x27b70a85,0x2e1b2138,0x4d2c6dfc,0x53380d13,0x650a7354,0x766a0abb,0x81c2c92e,0x92722c85,0xa2bfe8a1,0xa81a664b,0xc24b8b70,0xc76c51a3,0xd192e819,0xd6990624,0xf40e3585,0x106aa070,0x19a4c116,0x1e376c08,0x2748774c,0x34b0bcb5,0x391c0cb3,0x4ed8aa4a,0x5b9cca4f,0x682e6ff3,0x748f82ee,0x78a5636f,0x84c87814,0x8cc70208,0x90befffa,0xa4506ceb,0xbef9a3f7,0xc67178f2];
  const rotate = (number, bits) => number >>> bits | number << (32 - bits), words = new Uint32Array(64);
  for (let offset = 0; offset < size; offset += 64) {
    for (let index = 0; index < 16; index++) words[index] = data.getUint32(offset + index * 4, false);
    for (let index = 16; index < 64; index++) {
      const a = words[index - 15], b = words[index - 2];
      words[index] = words[index - 16] + (rotate(a, 7) ^ rotate(a, 18) ^ a >>> 3) + words[index - 7] + (rotate(b, 17) ^ rotate(b, 19) ^ b >>> 10);
    }
    let [a,b,c,d,e,f,g,h] = state;
    for (let index = 0; index < 64; index++) {
      const first = (h + (rotate(e,6) ^ rotate(e,11) ^ rotate(e,25)) + (e & f ^ ~e & g) + constants[index] + words[index]) | 0;
      const second = ((rotate(a,2) ^ rotate(a,13) ^ rotate(a,22)) + (a & b ^ a & c ^ b & c)) | 0;
      h=g;g=f;f=e;e=(d+first)|0;d=c;c=b;b=a;a=(first+second)|0;
    }
    [a,b,c,d,e,f,g,h].forEach((word, index) => { state[index] = (state[index] + word) >>> 0; });
  }
  return state.map(word => word.toString(16).padStart(8, "0")).join("");
}

function canonicalItems(result) {
  const original = array(result?.evidence);
  if (result?.schema_version !== "waf-analysis-v3" || original.length > 10
    || new Set(original.map(item => item?.evidence_id)).size !== original.length
    || original.some(item => !record(item) || !/^e[1-9][0-9]?$/.test(item.evidence_id)
      || !directions.includes(item.supports) || !["field", "excerpt", "interpretation_ko"].every(key => typeof item[key] === "string" && item[key].trim()))) return null;
  return original.map((item, index) => ({ ...item, number: index + 1, related_numbers: [] }));
}

function validRelations(result, value) {
  if (value == null) return emptyRelations();
  const items = canonicalItems(result), byId = new Map((items || []).map(item => [item.evidence_id, item]));
  if (!items || !record(value) || value.version !== "evidence-relations-v1" || Object.keys(value).sort().join() !== "groups,version"
    || !Array.isArray(value.groups) || value.groups.length > 10) return null;
  const seen = new Set(), counts = { primary: 0, verifier: 0 };
  for (const group of value.groups) {
    if (!record(group) || Object.keys(group).sort().join() !== "related_evidence_ids,representative_evidence_id,role"
      || !roles.includes(group.role) || !Array.isArray(group.related_evidence_ids) || !group.related_evidence_ids.length || group.related_evidence_ids.length > 4) return null;
    const ids = [group.representative_evidence_id, ...group.related_evidence_ids], key = `${group.role}:${group.representative_evidence_id}`;
    if (new Set(ids).size !== ids.length || seen.has(key) || ++counts[group.role] > 5
      || ids.some(id => !byId.has(id) || !array(byId.get(id).origins).some(origin => origin?.role === group.role))) return null;
    const sides = new Set(ids.map(id => byId.get(id).supports));
    if (sides.has("attack_request") && sides.has("normal_request")) return null;
    seen.add(key);
  }
  return value;
}

export function evidenceSourceHash(result, relations = null) {
  const value = { evidence: array(result?.evidence), decision_issues: array(result?.decision_issues),
    policy: { decision_evidence_ids: array(result?.policy?.decision_evidence_ids) },
    verdict: result?.verdict ?? null, relations: relations ?? emptyRelations() };
  return sha256Text(JSON.stringify(stableValue(value)));
}

function finishPlan(result, relations, groups, status, reason) {
  const items = canonicalItems(result) || [], byId = new Map(items.map(item => [item.evidence_id, item]));
  const { dependencies } = constraints(result, relations);
  const references = new Set(array(result?.decision_issues).flatMap(issue => array(issue?.evidence_ids)));
  const top = new Set(groups.filter(group => group.supports !== "context").slice(0, 3).map(group => group.group_id));
  const opposite = result?.verdict === "true_positive" ? "normal_request" : result?.verdict === "false_positive" ? "attack_request" : null;
  const output = groups.map(group => {
    const ids = new Set(group.member_ids), related = new Set(group.member_ids.flatMap(id => [...dependencies.get(id)]).filter(id => !ids.has(id)));
    const visibility = [];
    if (status === "fallback") visibility.push("fallback_original");
    else {
      if (top.has(group.group_id)) visibility.push("rank_top");
      if ([...ids].some(id => references.has(id))) visibility.push("decision_issue");
      if (opposite && group.supports === opposite) visibility.push("counter_evidence");
      if (result?.verdict === "inconclusive" && group.supports !== "context") visibility.push("inconclusive_directional");
    }
    const shown = visibility.length > 0;
    if (!shown) visibility.push(group.supports === "context" ? "context_auxiliary" : "remaining_evidence");
    return { ...group, related_ids: items.map(item => item.evidence_id).filter(id => related.has(id)), default_visible: shown, visibility_reasons: visibility };
  });
  return { version: "evidence-display-v1", source_hash: evidenceSourceHash(result, relations), status, reason, groups: output,
    metrics: { original_evidence_count: byId.size, group_count: output.length, core_group_count: output.filter(group => group.default_visible).length, merged_count: byId.size - output.length } };
}

const encoded = value => JSON.stringify(stableValue(value));
function physicalSource(item) {
  let field = item.field.trim().replace(/^event\./, ""); if (field === "raw_payload") field = "payload";
  const spans = [...new Set(array(item.source_spans).map(span => JSON.stringify([span.start, span.end])))].map(value => JSON.parse(value)).sort((a, b) => a[0] - b[0] || a[1] - b[1]);
  return encoded([field, item.excerpt, spans]);
}

function constraints(result, relations) {
  const items = canonicalItems(result) || [], byId = new Map(items.map(item => [item.evidence_id, item]));
  const dependencies = new Map(items.map(item => [item.evidence_id, new Set()]));
  for (const group of relations.groups) for (const id of group.related_evidence_ids) dependencies.get(group.representative_evidence_id).add(id);
  for (const [id, values] of dependencies) {
    const pending = [...values], reached = new Set();
    while (pending.length) { const other = pending.pop(); if (other === id || reached.has(other)) continue; reached.add(other); pending.push(...dependencies.get(other)); }
    dependencies.set(id, reached);
  }
  const memberships = new Map(items.map(item => [item.evidence_id, array(result?.decision_issues).flatMap((issue, index) => array(issue.evidence_ids).includes(item.evidence_id) ? [index] : [])]));
  const key = item => encoded([item.supports, memberships.get(item.evidence_id), [...new Set([...dependencies.get(item.evidence_id)].map(id => physicalSource(byId.get(id))))].sort()]);
  return { dependencies, memberships, mergeKey: key };
}

function singletonPlan(result, relations, reason) {
  return finishPlan(result, relations, (canonicalItems(result) || []).map(item => ({ group_id: `g${item.number}`,
    representative_id: item.evidence_id, member_ids: [item.evidence_id], supports: item.supports, number: item.number })), "fallback", reason);
}

export function buildEvidenceDisplayPlan(result, presentation = result?.evidence_presentation, relations = null, { status = "skipped", reason = null } = {}) {
  const items = canonicalItems(result), linked = validRelations(result, relations);
  if (!items) return null;
  if (!linked) return singletonPlan(result, emptyRelations(), "relations_invalid");
  if (presentation?.version === "evidence-display-v1") return validateEvidenceDisplayPlan(result, presentation, linked)
    ? presentation : singletonPlan(result, linked, "display_plan_invalid");
  const { mergeKey } = constraints(result, linked);
  let selected = presentationItems({ evidence: result.evidence }, presentation);
  const validLegacy = presentation?.status === "completed" && selected !== result.evidence
    && selected.every(item => new Set((item._evidence_ids || [item.evidence_id]).map(id => mergeKey(items.find(source => source.evidence_id === id)))).size === 1);
  if (!validLegacy) {
    const exact = new Map();
    for (const item of items) {
      const key = encoded([physicalSource(item), item.interpretation_ko, mergeKey(item)]);
      if (!exact.has(key)) exact.set(key, { ...item, _evidence_ids: [] });
      exact.get(key)._evidence_ids.push(item.evidence_id);
    }
    selected = [...exact.values()];
  }
  const byId = new Map(items.map(item => [item.evidence_id, item]));
  const groups = selected.map(item => ({ group_id: `g${byId.get(item.evidence_id)?.number}`,
    representative_id: item.evidence_id, member_ids: items.map(source => source.evidence_id).filter(id => (item._evidence_ids || [item.evidence_id]).includes(id)), supports: item.supports,
    number: byId.get(item.evidence_id)?.number }));
  if (status === "fallback") return singletonPlan(result, linked, reason);
  if (status === "completed" && groups.length === items.length && reason === null) reason = "no_reduction";
  if (status === "skipped" && reason === null) reason = "no_duplicate_candidates";
  return finishPlan(result, linked, groups, status, reason);
}

export function validateEvidenceDisplayPlan(result, plan, relations = null) {
  try {
    const items = canonicalItems(result), linked = validRelations(result, relations);
    if (!items || !linked || !record(plan) || Object.keys(plan).sort().join() !== "groups,metrics,reason,source_hash,status,version"
      || plan.version !== "evidence-display-v1" || !["pending", "completed", "skipped", "fallback"].includes(plan.status)
      || plan.source_hash !== evidenceSourceHash(result, linked) || !Array.isArray(plan.groups) || plan.groups.length > 10) return false;
    const byId = new Map(items.map(item => [item.evidence_id, item])), seen = new Set(), groupIds = new Set();
    const { mergeKey } = constraints(result, linked);
    for (const group of plan.groups) {
      if (!record(group) || Object.keys(group).sort().join() !== "default_visible,group_id,member_ids,number,related_ids,representative_id,supports,visibility_reasons"
        || !byId.has(group.representative_id) || group.supports !== byId.get(group.representative_id).supports
        || group.number !== byId.get(group.representative_id).number || group.group_id !== `g${group.number}` || groupIds.has(group.group_id)
        || !Array.isArray(group.member_ids) || !group.member_ids.length || !group.member_ids.includes(group.representative_id)
        || new Set(group.member_ids).size !== group.member_ids.length || group.member_ids.some(id => !byId.has(id) || seen.has(id) || byId.get(id).supports !== group.supports)
        || !Array.isArray(group.related_ids) || new Set(group.related_ids).size !== group.related_ids.length
        || group.related_ids.some(id => !byId.has(id) || group.member_ids.includes(id))
        || typeof group.default_visible !== "boolean" || !Array.isArray(group.visibility_reasons)
        || !group.visibility_reasons.length || group.visibility_reasons.some(reason => !reasons.has(reason))) return false;
      if (new Set(group.member_ids.map(id => mergeKey(byId.get(id)))).size !== 1) return false;
      if (encoded(group.member_ids) !== encoded(items.map(item => item.evidence_id).filter(id => group.member_ids.includes(id)))) return false;
      if (plan.status === "fallback" && group.member_ids.length !== 1) return false;
      group.member_ids.forEach(id => seen.add(id)); groupIds.add(group.group_id);
    }
    if (seen.size !== items.length) return false;
    const expected = finishPlan(result, linked, plan.groups, plan.status, plan.reason);
    return encoded(plan.metrics) === encoded(expected.metrics)
      && plan.groups.every((group, index) => JSON.stringify(group.related_ids) === JSON.stringify(expected.groups[index].related_ids)
        && group.default_visible === expected.groups[index].default_visible
        && JSON.stringify(group.visibility_reasons) === JSON.stringify(expected.groups[index].visibility_reasons));
  } catch { return false; }
}

export function evidenceDisplayView(result, view = null, relations = view?.relations ?? null) {
  const sourceItems = canonicalItems(result)?.map(item => ({ ...item, interpretation_ko: analystText(item.interpretation_ko) }));
  if (!sourceItems) return null;
  const hasView = view?.version === "evidence-display-view-v1";
  const presentation = hasView && view.plan ? view.plan : result.evidence_presentation;
  // An unavailable job must never reuse a previously reduced legacy mapping.
  // Keep every original visible even if a damaged response omits its plan.
  const plan = hasView && view.status === "fallback" && !view.plan
    ? buildEvidenceDisplayPlan(result, null, relations, { status: "fallback", reason: "display_plan_invalid" })
    : buildEvidenceDisplayPlan(result, presentation, relations);
  const byId = new Map(sourceItems.map(item => [item.evidence_id, item]));
  const groups = plan.groups.map(group => ({ ...byId.get(group.representative_id), ...group,
    evidence_id: group.representative_id, _evidence_ids: group.member_ids,
    roles: roles.filter(role => group.member_ids.some(id => array(byId.get(id).origins).some(origin => origin?.role === role))),
    related_sources: group.related_ids.map(id => byId.get(id)), member_sources: group.member_ids.map(id => byId.get(id)),
    relation: group.related_ids.length > 0, related_numbers: [] }));
  const numberById = Object.fromEntries(sourceItems.map(item => [item.evidence_id, item.number]));
  const groupById = Object.fromEntries(groups.flatMap(group => group.member_ids.map(id => [id, group])));
  return { plan, items: sourceItems, groups, core: groups.filter(group => group.default_visible), additional: groups.filter(group => !group.default_visible),
    numberById, groupById, status: view?.status ?? plan.status, invalid: plan.reason === "display_plan_invalid" || plan.reason === "relations_invalid" };
}
