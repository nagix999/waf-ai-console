export const BASELINE_PARSER_ID = "generic-http-v3";
export const PARSER_ENGINES = ["generic-http-v3", "http-capture-v1"];
export const defaultParserOptions = () => ({ line_endings: "actual", request_line_whitespace: "strict", skip_leading_blank_lines: false });

export function parserDraft(version) {
  const options = { ...defaultParserOptions(), ...(version?.options || {}) };
  if (options.profiles) options.profiles = options.profiles.map(profile => ({ ...profile }));
  return { name: "", description: "", engine_version: "http-capture-v1", options, parent_id: version?.id || BASELINE_PARSER_ID };
}
const formatOptionsValid = options => options != null && typeof options === "object" && !Array.isArray(options)
    && ["actual", "escaped_crlf", "escaped_lf"].includes(options.line_endings)
    && ["strict", "flexible"].includes(options.request_line_whitespace)
    && typeof options.skip_leading_blank_lines === "boolean";
export function safeParserOptions(options) {
  if (!formatOptionsValid(options) || !Object.keys(options).every(key => ["line_endings", "request_line_whitespace", "skip_leading_blank_lines", "profiles"].includes(key))) return false;
  if (options.profiles === undefined) return true;
  if (!Array.isArray(options.profiles) || options.profiles.length > 20) return false;
  const seen = new Set();
  for (const profile of options.profiles) {
    if (!formatOptionsValid(profile) || Object.keys(profile).some(key => !["name", "waf_vendor", "line_endings", "request_line_whitespace", "skip_leading_blank_lines"].includes(key)) || typeof profile.name !== "string" || !profile.name.trim() || profile.name.length > 120 || typeof profile.waf_vendor !== "string" || !profile.waf_vendor.trim() || profile.waf_vendor.length > 120) return false;
    const key = `${profile.waf_vendor.trim().toLowerCase()}\u0000${profile.line_endings}`;
    if (seen.has(key)) return false; seen.add(key);
  }
  return true;
}
export function parserDraftPayload(draft) {
  if (!draft?.name?.trim() || draft.name.length > 120 || draft.description?.length > 2000 || !PARSER_ENGINES.includes(draft.engine_version) || !safeParserOptions(draft.options)) throw new Error("invalid_parser_version");
  const options = { ...draft.options };
  if (options.profiles?.length) options.profiles = options.profiles.map(profile => ({ ...profile, name: profile.name.trim(), waf_vendor: profile.waf_vendor.trim() }));
  else delete options.profiles;
  return { name: draft.name.trim(), description: draft.description?.trim() || "", engine_version: draft.engine_version,
    options: draft.engine_version === BASELINE_PARSER_ID ? defaultParserOptions() : options, parent_id: draft.parent_id || BASELINE_PARSER_ID };
}
export function parserSourcePayload({ source, testRunId, analysisIds, maxCases }, kind = "comparison") {
  const ceiling = kind === "suggestion" ? 20 : 100;
  const max_cases = Number(maxCases);
  if (!Number.isInteger(max_cases) || max_cases < 1 || max_cases > ceiling) throw new Error("invalid_parser_case_limit");
  if (source === "test") {
    if (!testRunId) throw new Error("parser_test_required");
    return { test_run_id: testRunId, max_cases };
  }
  const analysis_ids = [...new Set(String(analysisIds || "").split(/[\s,]+/).filter(Boolean))];
  if (!analysis_ids.length || analysis_ids.length > max_cases || analysis_ids.some(id => !/^[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}$/i.test(id))) throw new Error("invalid_parser_analysis_ids");
  return { analysis_ids, max_cases };
}
export function parserSuggestionDraft(result, baseline) {
  const suggestion = result?.suggestion;
  if (suggestion?.engine_version !== "http-capture-v1" || !safeParserOptions(suggestion.options) || suggestion.options.profiles?.length) return null;
  const draft = parserDraft(baseline);
  return { ...draft, engine_version: suggestion.engine_version, options: { ...suggestion.options, ...(draft.options.profiles?.length ? { profiles: draft.options.profiles } : {}) },
    // Free-form model prose may quote operational inputs. Keep it in the
    // audited encrypted job result, not plaintext version metadata.
    description: "" };
}
export function parserVersionLabel(version, missing = "—") {
  return version?.name || version?.engine_version || missing;
}
export const parserJobFinished = job => ["completed", "failed", "canceled"].includes(job?.status);
export function parserCatalogVersions(catalog) {
  const items = [...(catalog?.items || [])];
  if (catalog?.active_version && !items.some(version => version.id === catalog.active_version.id)) items.push(catalog.active_version);
  return items;
}
