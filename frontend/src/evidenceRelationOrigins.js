// The v2 relationship contract still refers to the original role-local source
// selections. A repeated role/index is ambiguous even when the text is equal.
export function validRelationOrigins(items) {
  const indexes = new Set();
  return items.every(item => Array.isArray(item.origins) && item.origins.length > 0 && item.origins.length <= 10
    && item.origins.every(origin => {
      if (origin === null || typeof origin !== "object" || Array.isArray(origin)
        || Object.keys(origin).sort().join() !== "index,role,source_id"
        || !["primary", "verifier"].includes(origin.role) || !Number.isInteger(origin.index)
        || origin.index < 0 || origin.index > 4 || typeof origin.source_id !== "string"
        || !/^c[1-9][0-9]{0,2}$/.test(origin.source_id)) return false;
      const key = `${origin.role}:${origin.index}`;
      if (indexes.has(key)) return false;
      indexes.add(key);
      return true;
    }));
}
