import assert from "node:assert/strict";
import test from "node:test";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";
import { runInNewContext } from "node:vm";
import { buildSync } from "esbuild";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { BASELINE_PARSER_ID, defaultParserOptions, parserCatalogVersions, parserDraft, parserDraftPayload, parserSourcePayload, parserSuggestionDraft, safeParserOptions, parserJobFinished } from "./parserManagement.js";
import { candidateIssue, defaultCandidate } from "./candidateConfiguration.js";
import { api } from "./api.js";
import { readAppHash, writeAppHash } from "./appRoutes.js";
import { consoleDestination, consoleLocation } from "./consoleNavigation.js";
import { configurationChanges } from "./testConfigurationIdentity.js";

const id = "11111111-1111-4111-8111-111111111111";
function load(file, name = "default") {
  const source = buildSync({ entryPoints: [fileURLToPath(new URL(file, import.meta.url))], bundle: true, write: false, platform: "node", format: "cjs", packages: "external", jsx: "automatic", loader: { ".css": "empty" }, logLevel: "silent" }).outputFiles[0].text;
  const module = { exports: {} }; runInNewContext(source, { module, exports: module.exports, require: createRequire(import.meta.url), console, URL, setTimeout, clearTimeout, AbortController }); return module.exports[name];
}

test("safe parser options reject arbitrary code, regex, unknown keys and coerced types", () => {
  assert.equal(safeParserOptions(defaultParserOptions()), true);
  for (const invalid of [null, [], { ...defaultParserOptions(), code: "exec(payload)" }, { ...defaultParserOptions(), regex: "(a+)+" }, { ...defaultParserOptions(), line_endings: "auto" }, { ...defaultParserOptions(), skip_leading_blank_lines: "false" }]) assert.equal(safeParserOptions(invalid), false);
});
test("immutable parser drafts copy options but not names, hashes or source logs", () => {
  const source = { id: "saved", name: "old", options: { ...defaultParserOptions(), line_endings: "escaped_crlf" }, content_hash: "hash", raw_payload: "PRIVATE" };
  const draft = parserDraft(source); assert.equal(draft.parent_id, "saved"); assert.equal(draft.name, "");
  draft.name = "  company capture  "; const payload = parserDraftPayload(draft);
  assert.equal(payload.name, "company capture"); assert.equal(payload.engine_version, "http-capture-v1");
  assert.doesNotMatch(JSON.stringify(payload), /PRIVATE|hash/);
  payload.options.line_endings = "actual"; assert.equal(source.options.line_endings, "escaped_crlf");
  assert.throws(() => parserDraftPayload({ ...draft, name: " " }));
  assert.throws(() => parserDraftPayload({ ...draft, engine_version: "python" }));
  assert.equal(parserDraftPayload({ ...draft, engine_version: BASELINE_PARSER_ID }).options.line_endings, "actual");
});
test("parser input selection deduplicates IDs and sends raw-sensitive selection in JSON, never URLs", () => {
  assert.deepEqual(parserSourcePayload({ source: "analyses", analysisIds: `${id},\n${id}`, maxCases: 100 }), { analysis_ids: [id], max_cases: 100 });
  assert.deepEqual(parserSourcePayload({ source: "test", testRunId: id, maxCases: 20 }), { test_run_id: id, max_cases: 20 });
  for (const input of ["", "<script>", "../", "uuid"]) assert.throws(() => parserSourcePayload({ source: "analyses", analysisIds: input, maxCases: 20 }));
  for (const count of [0, -1, 1.5, 101, "bad"]) assert.throws(() => parserSourcePayload({ source: "test", testRunId: id, maxCases: count }));
  assert.throws(() => parserSourcePayload({ source: "test", testRunId: id, maxCases: 21 }, "suggestion"));
  assert.throws(() => parserSourcePayload({ source: "test", testRunId: "", maxCases: 1 }));
  assert.throws(() => parserSourcePayload({ source: "analyses", analysisIds: `${id},22222222-2222-4222-8222-222222222222`, maxCases: 1 }));
});
test("LLM proposals can only prefill supported options and require a new explicit name", () => {
  const result = { suggestion: { engine_version: "http-capture-v1", options: defaultParserOptions(), reason_ko: "Review format" } };
  const draft = parserSuggestionDraft(result, { id: "old" }); assert.equal(draft.name, ""); assert.equal(draft.parent_id, "old"); assert.equal(draft.description, "");
  assert.equal(parserSuggestionDraft({ suggestion: { ...result.suggestion, options: { ...defaultParserOptions(), code: "exec" } } }), null);
  assert.equal(parserSuggestionDraft({ suggestion: { ...result.suggestion, engine_version: "new-code" } }), null);
  const profiles = [{ name: "Existing format", waf_vendor: "Vendor A", ...defaultParserOptions() }];
  const preserved = parserSuggestionDraft(result, { id: "old", options: { ...defaultParserOptions(), profiles } });
  assert.deepEqual(preserved.options.profiles, profiles); assert.notEqual(preserved.options.profiles, profiles);
  assert.equal(parserSuggestionDraft({ suggestion: { ...result.suggestion, options: { ...defaultParserOptions(), profiles } } }), null);
});
test("bounded vendor profiles support one vendor with different formats and reject ambiguous duplicates", () => {
  const profile = { name: "Actual format", waf_vendor: "Vendor A", ...defaultParserOptions() };
  const options = { ...defaultParserOptions(), profiles: [profile, { ...profile, name: "Serialized format", line_endings: "escaped_crlf" }] };
  assert.equal(safeParserOptions(options), true);
  assert.equal(safeParserOptions({ ...options, profiles: [profile, { ...profile, waf_vendor: "  VENDOR A  " }] }), false);
  assert.equal(safeParserOptions({ ...options, profiles: [{ ...profile, regex: ".*" }] }), false);
  assert.equal(safeParserOptions({ ...options, profiles: Array(21).fill(profile) }), false);
  const draft = parserDraft({ id: "source", options }); draft.name = "Reviewed profiles";
  const payload = parserDraftPayload(draft); payload.options.profiles[0].name = "Changed copy";
  assert.equal(options.profiles[0].name, "Actual format");
  const empty = parserDraftPayload({ ...draft, options: { ...defaultParserOptions(), profiles: [] } });
  assert.equal(Object.hasOwn(empty.options, "profiles"), false);
});
test("only terminal parser jobs stop polling", () => {
  for (const status of ["pending", "running", "processing", undefined]) assert.equal(parserJobFinished({ status }), false);
  for (const status of ["completed", "failed", "canceled"]) assert.equal(parserJobFinished({ status }), true);
});
test("limited recent catalogs retain the independently returned current Production parser", () => {
  const recent = { id: "new" }, active = { id: "old-active" };
  assert.deepEqual(parserCatalogVersions({ items: [recent], active_version: active }), [recent, active]);
  assert.deepEqual(parserCatalogVersions({ items: [active], active_version: active }), [active]);
});
test("parser configuration is a real sidebar route, without selection or logs in the URL", () => {
  const state = consoleDestination({ page: "dashboard" }, "parsers");
  assert.equal(consoleLocation(state).item, "parsers"); assert.equal(consoleLocation(state).group, "configure");
  assert.equal(writeAppHash(state), "#configure/parsers"); assert.deepEqual(readAppHash("#configure/parsers"), { page: "settings", tab: "parsers" });
  assert.equal(readAppHash("#configure/parsers/PRIVATE"), null);
});
test("legacy candidates retain baseline parser and missing explicit versions fail closed", () => {
  const agents = { profiles: [{ id: "p", can_assign: true }], assignments: { test: { primary_profile_id: "p" } } };
  const prompts = { items: [{ id: "v" }], active_version_id: "v" }, schemas = { items: [{ id: "s" }], active_version_id: "s" };
  const value = defaultCandidate(agents, prompts, schemas), catalog = { agents, prompts, schemas, parsers: { items: [{ id: BASELINE_PARSER_ID }, { id: "candidate" }] } };
  assert.equal(candidateIssue(catalog, value), ""); assert.equal(candidateIssue(catalog, { ...value, parser_version_id: "candidate" }), "");
  assert.match(candidateIssue(catalog, { ...value, parser_version_id: "missing" }), /파서/);
  assert.deepEqual(configurationChanges({}, { parser: { version_id: BASELINE_PARSER_ID, content_hash: "baseline" } }), []);
  assert.deepEqual(configurationChanges({}, { parser: { version_id: "candidate" } }), ["parser"]);
});
test("parser API requests are admin-only paths, noncached and do not offer activation", async () => {
  const previous = globalThis.fetch, calls = []; globalThis.fetch = async (url, options) => { calls.push({ url, options }); return { ok: true, json: async () => ({}) }; };
  try {
    await api.parsers(); await api.parser("id/with space"); await api.createParser({ name: "safe", options: defaultParserOptions() }); await api.compareParsers({ analysis_ids: [id] }); await api.suggestParser({ profile_id: "internal", analysis_ids: [id] }); await api.parserJobs({ limit: 5, offset: 15 }); await api.parserJob(id); await api.reviewParserComparison(id);
    assert.ok(calls.every(call => call.url.startsWith("/api/v1/admin/parsers") && call.options.credentials === "include" && call.options.cache === "no-store"));
    assert.equal(calls[1].url, "/api/v1/admin/parsers/id%2Fwith%20space"); assert.equal(calls[3].options.method, "POST");
    assert.ok(!calls.some(call => call.url.includes("analysis_ids") || call.url.includes("activate")));
    assert.deepEqual(JSON.parse(calls.at(-1).options.body), { confirmed: true });
    assert.equal(calls[5].url, "/api/v1/admin/parsers/jobs?limit=5&offset=15");
  } finally { globalThis.fetch = previous; }
});
test("parser management initial view does not invent a current version or expose mutating activation", () => {
  const Page = load("./ParserManagement.jsx"); const html = renderToStaticMarkup(createElement(Page));
  assert.match(html, /현재 운영 파서/); assert.match(html, /공식 테스트/); assert.match(html, /불러오는 중/);
  assert.match(html, /원문 사본이 암호화되어 남으며, 원본 분석을 삭제해도 별도로 보존됩니다/);
  assert.doesNotMatch(html, /onclick|<textarea[^>]*>PRIVATE|운영에 즉시 적용|파서 적용 버튼/);
});
test("parser results escape raw log content and never treat a suggestion as validated", () => {
  const Result = load("./ParserManagement.jsx", "ParserJobResult");
  const result = renderToStaticMarkup(createElement(Result, { job: { status: "completed", result: { items: [{ analysis_id: id, raw_payload: "<script>alert('x')</script>", baseline: { parse_status: "partial" }, candidate: { parse_status: "success" } }] } } }));
  assert.match(result, /&lt;script&gt;/); assert.doesNotMatch(result, /<script>/); assert.match(result, /파싱만 비교/);
  const suggestion = renderToStaticMarkup(createElement(Result, { job: { status: "completed", result: { suggestion: { engine_version: "http-capture-v1", options: defaultParserOptions(), reason_ko: "Review" } } }, onUseSuggestion: () => {} }));
  assert.match(suggestion, /정답이나 검증된 파서가 아니며/); assert.match(suggestion, /검토 후 새 버전 작성/); assert.doesNotMatch(suggestion, /운영 적용 완료/);
});
test("manual parsing review reads mutable audit metadata, not the original encrypted summary", () => {
  const Result = load("./ParserManagement.jsx", "ParserJobResult"), summary = { sample_count: 1, all_valid_source_spans: true, structure_reviewed: false, status_counts: { candidate: { partial: 1 } } };
  const reviewed = renderToStaticMarkup(createElement(Result, { job: { status: "completed", summary: { ...summary, structure_reviewed: true }, result: { summary, items: [] } }, onReview: () => {} }));
  assert.match(reviewed, /disabled="">파싱 결과 확인 완료</); assert.doesNotMatch(reviewed, /확인 완료로 기록/);
  for (const change of [{ sample_count: 0 }, { all_valid_source_spans: false }, { status_counts: { candidate: { failed: 1 } } }]) {
    const blocked = renderToStaticMarkup(createElement(Result, { job: { status: "completed", summary, result: { summary: { ...summary, ...change }, items: [] } }, onReview: () => {} }));
    assert.match(blocked, /disabled="">파싱 결과 확인 완료로 기록</);
  }
});
