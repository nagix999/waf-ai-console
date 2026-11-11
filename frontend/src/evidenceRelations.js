import { detailEvidence } from "./decisionDetail.js";
import { isResultV3 } from "./assessmentData.js";

const record = value => value !== null && typeof value === "object" && !Array.isArray(value);
const exactKeys = (value, keys) => record(value) && Object.keys(value).length === keys.length && keys.every(key => Object.hasOwn(value, key));
const roles = ["primary", "verifier"];

// A relation references original canonical evidence, not the Editor's duplicate
// presentation. Preserve source classification and role; never infer meaning.
export function evidenceRelationView(result, value) {
  const original = () => ({ enabled: false, invalid: value != null, items: detailEvidence(result), rows: null });
  if (value == null) return original();
  if (!isResultV3(result) || !exactKeys(value, ["version", "groups"]) || value.version !== "evidence-relations-v1"
    || !Array.isArray(value.groups) || value.groups.length > 10) return original();
  if (!value.groups.length) return { ...original(), invalid: false };
  const items = detailEvidence(result, { preserveEvidence: true }), indexed = new Map(items.map(item => [item.evidence_id, item]));
  if (!Array.isArray(result.evidence) || result.evidence.length > 10 || items.length !== result.evidence.length || indexed.size !== items.length) return original();
  const roleOf = item => Array.isArray(item?.origins) ? [...new Set(item.origins.map(origin => origin?.role).filter(role => roles.includes(role)))] : [];
  if (items.some(item => !/^e[1-9][0-9]?$/.test(item.evidence_id) || !roleOf(item).length
    || !["attack_request", "normal_request", "context"].includes(item.supports))) return original();
  const representatives = new Set(), counts = { primary: 0, verifier: 0 }, groups = [];
  for (const group of value.groups) {
    if (!exactKeys(group, ["role", "representative_evidence_id", "related_evidence_ids"]) || !roles.includes(group.role)
      || !Array.isArray(group.related_evidence_ids) || !group.related_evidence_ids.length || group.related_evidence_ids.length > 4) return original();
    const ids = [group.representative_evidence_id, ...group.related_evidence_ids], key = `${group.role}:${group.representative_evidence_id}`;
    if (new Set(ids).size !== ids.length || ids.some(id => typeof id !== "string" || !indexed.has(id) || !roleOf(indexed.get(id)).includes(group.role))
      || representatives.has(key) || ++counts[group.role] > 5) return original();
    const sides = new Set(ids.map(id => indexed.get(id).supports));
    if (sides.has("attack_request") && sides.has("normal_request")) return original();
    representatives.add(key);
    groups.push({ role: group.role, representative: indexed.get(ids[0]), related: ids.slice(1).map(id => indexed.get(id)) });
  }
  const consumed = new Set(), rows = [], identicalGroups = new Map();
  for (const group of groups) {
    consumed.add(`${group.role}:${group.representative.evidence_id}`);
    for (const item of group.related) {
      // A directional source must not disappear inside collapsed context. A
      // representative elsewhere always retains its own explanation as well.
      if (!representatives.has(`${group.role}:${item.evidence_id}`)
        && (item.supports === "context" || item.supports === group.representative.supports)) consumed.add(`${group.role}:${item.evidence_id}`);
    }
    // Only identical canonical references may share a display row. IDs already
    // refer to the same source, interpretation and classification; never merge
    // merely similar prose or different related-source sets. Keep first order.
    const key = JSON.stringify([group.representative.evidence_id, group.related.map(item => item.evidence_id).sort()]);
    const existing = identicalGroups.get(key);
    if (existing) existing.roles = roles.filter(role => existing.roles.includes(role) || role === group.role);
    else {
      const row = { ...group.representative, roles: [group.role], related_sources: group.related, relation: true };
      identicalGroups.set(key, row);
      rows.push(row);
    }
  }
  for (const item of items) {
    const remaining = roleOf(item).filter(role => !consumed.has(`${role}:${item.evidence_id}`));
    if (remaining.length) rows.push({ ...item, roles: remaining, related_sources: [], relation: false });
  }
  rows.sort((left, right) => left.number - right.number || roles.indexOf(left.roles[0]) - roles.indexOf(right.roles[0]));
  return { enabled: true, invalid: false, items, rows };
}
