// Only apply a complete ID mapping. Invalid mappings fall back in full.
// Mirror backend evidence_editor.validate_groups; never infer a new grouping.
export function presentationItems(assessment, presentation) {
  const original = Array.isArray(assessment?.evidence) ? assessment.evidence : [];
  if (presentation?.version !== "evidence-editor-v1" || presentation.status !== "completed") return original;
  try {
    const indexed = new Map(original.map(item => [item.evidence_id, item]));
    const groups = presentation.groups;
    if (indexed.size !== original.length || original.length > 10 || !Array.isArray(groups) || groups.length < 1 || groups.length > 10) return original;
    const seen = new Set();
    const sourceKey = item => {
      const spans = [...new Set((item.source_spans || []).map(span => JSON.stringify([span.start, span.end])))].sort();
      return JSON.stringify([item.field, item.excerpt, item.supports, spans]);
    };
    for (const group of groups) {
      const ids = group.member_ids;
      if (Object.keys(group).some(key => !["member_ids", "representative_id"].includes(key))
        || typeof group.representative_id !== "string" || group.representative_id.length > 20
        || !Array.isArray(ids) || !ids.length || ids.length > 10 || !ids.includes(group.representative_id)
        || ids.some(id => typeof id !== "string" || !indexed.has(id) || seen.has(id)) || new Set(ids).size !== ids.length
        || new Set(ids.map(id => sourceKey(indexed.get(id)))).size !== 1) return original;
      ids.forEach(id => seen.add(id));
    }
    if (seen.size !== indexed.size) return original;
    const order = new Map(original.map((item, index) => [item.evidence_id, index]));
    return [...groups].sort((a, b) => Math.min(...a.member_ids.map(id => order.get(id))) - Math.min(...b.member_ids.map(id => order.get(id))))
      .map(group => ({ ...indexed.get(group.representative_id), _evidence_ids: group.member_ids }));
  } catch { return original; }
}
