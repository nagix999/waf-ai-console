// A recorded ID mapping only changes presentation, never the stored checks.
// Keep validation aligned with agent/result_editor.py; do not guess grouping.
export function presentFollowUpChecks(checks, presentation) {
  if (presentation?.version !== "follow-up-editor-v1" || presentation.status !== "completed") return checks;
  try {
    if (!Array.isArray(checks) || checks.length > 5) return checks;
    const fields = ["source_ko", "check_ko", "why_ko"];
    const limits = { source_ko: 240, check_ko: 800, why_ko: 800 };
    const items = checks.map((check, index) => {
      if (!check || typeof check !== "object" || Object.keys(check).some(key => !fields.includes(key))
        || fields.some(key => typeof check[key] !== "string" || !check[key].length || [...check[key]].length > limits[key])) throw new Error("invalid_checks");
      return { check_id: `c${index + 1}`, ...check };
    });
    if (!Array.isArray(presentation.items) || presentation.items.length !== items.length
      || presentation.items.some((item, index) => !item || Object.keys(item).length !== 4
        || ["check_id", ...fields].some(key => item[key] !== items[index][key]))) return checks;
    const groups = presentation.groups;
    if (!Array.isArray(groups) || groups.length > 5) return checks;
    const indexed = new Map(items.map(item => [item.check_id, item]));
    const seen = new Set();
    for (const group of groups) {
      if (!group || Object.keys(group).length !== 2 || !Object.hasOwn(group, "member_ids") || !Object.hasOwn(group, "representative_id")) return checks;
      const ids = group.member_ids;
      if (!Array.isArray(ids) || !ids.length || ids.length > 10 || typeof group.representative_id !== "string"
        || !ids.includes(group.representative_id) || new Set(ids).size !== ids.length
        || ids.some(id => typeof id !== "string" || !indexed.has(id) || seen.has(id))
        || new Set(ids.map(id => indexed.get(id).source_ko)).size !== 1) return checks;
      ids.forEach(id => seen.add(id));
    }
    if (seen.size !== indexed.size) return checks;
    const order = new Map(items.map((item, index) => [item.check_id, index]));
    return [...groups].sort((a, b) => Math.min(...a.member_ids.map(id => order.get(id))) - Math.min(...b.member_ids.map(id => order.get(id))))
      .map(group => Object.fromEntries(fields.map(key => [key, indexed.get(group.representative_id)[key]])));
  } catch { return checks; }
}
