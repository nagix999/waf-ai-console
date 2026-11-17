import assert from "node:assert/strict";
import test from "node:test";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";
import { runInNewContext } from "node:vm";
import { buildSync } from "esbuild";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { codepointSegments, expectationDraft, expectationPayload, parserSessionBusy, casePriority, parserWorkbenchError, parserFieldValue, verifiedParserSpans, parserValueText } from "./parserWorkbench.js";
import { parserWorkbenchApi } from "./parserWorkbenchApi.js";
import { applyAppRoute, readAppHash, writeAppHash } from "./appRoutes.js";

const id = "11111111-1111-4111-8111-111111111111";
function load(file, name = "default") {
  const source = buildSync({ entryPoints: [fileURLToPath(new URL(file, import.meta.url))], bundle: true, write: false, platform: "node", format: "cjs", packages: "external", jsx: "automatic", loader: { ".css": "empty" }, logLevel: "silent" }).outputFiles[0].text;
  const module = { exports: {} }; runInNewContext(source, { module, exports: module.exports, require: createRequire(import.meta.url), console, URL, setTimeout, clearTimeout, AbortController }); return module.exports[name];
}

test("verified Python codepoint offsets highlight the intended text after an emoji", () => {
  const raw = "🙂GET /한글 HTTP/1.1";
  const parts = codepointSegments(raw, [{ start: 1, end: 4 }]);
  assert.equal(parts.filter(part => part.highlighted).map(part => part.text).join(""), "GET");
  assert.equal(parts.map(part => part.text).join(""), raw);
});
test("invalid spans are not highlighted or allowed to truncate the source", () => {
  for (const span of [{ start: -1, end: 2 }, { start: 2, end: 1 }, { start: 0, end: 99 }, { start: true, end: 2 }, { start: 0.5, end: 2 }]) {
    assert.deepEqual(codepointSegments("raw", [span]), [{ text: "raw", highlighted: false }]);
  }
});
test("overlapping source spans merge visually without duplicating any raw characters", () => {
  const parts = codepointSegments("0123456789", [{ start: 3, end: 6 }, { start: 1, end: 4 }]);
  assert.equal(parts.map(part => part.text).join(""), "0123456789");
  assert.equal(parts.filter(part => part.highlighted).map(part => part.text).join(""), "12345");
});
test("verified candidate source spans support review before human expectations, never unverified fallback", () => {
  const item = { valid_source_spans: true, candidate: { raw_source_spans: { method: [[1, 4]], request_target: [[5, 11]] } } };
  assert.deepEqual(verifiedParserSpans(item, "method"), [{ start: 1, end: 4 }]);
  assert.deepEqual(verifiedParserSpans(item, "uri"), [{ start: 5, end: 11 }]);
  assert.deepEqual(verifiedParserSpans({ ...item, valid_source_spans: false }, "method"), []);
  assert.deepEqual(verifiedParserSpans({ ...item, comparisons: [{ field: "method", source_spans: [] }] }, "method"), []);
});
test("expectation drafts never confirm unknown fields or infer missing as absent", () => {
  const draft = expectationDraft({ method: { state: "present", value: "GET" }, body: { state: "absent" } });
  assert.equal(draft.method.text, "GET"); assert.equal(draft.uri.state, "unknown");
  assert.equal(draft.body.state, "absent");
  assert.deepEqual(expectationPayload(draft).uri, { state: "unknown" });
  assert.deepEqual(expectationPayload(draft).body, { state: "absent" });
});
test("header/query expectations preserve repeated names, values and order", () => {
  const pairs = [{ name: "x", value: "1" }, { name: "x", value: "2" }, { name: "x", value: "1" }];
  const draft = expectationDraft({ headers: { state: "present", value: pairs }, query: { state: "present", value: pairs } });
  assert.deepEqual(expectationPayload(draft).headers.value, pairs);
  assert.deepEqual(expectationPayload(draft).query.value, pairs);
});
test("unconfirmed copied values cannot silently become confirmed expectations", () => {
  const draft = expectationDraft(); draft.method = { state: "unknown", text: "POST", copied: true };
  assert.deepEqual(expectationPayload(draft).method, { state: "unknown" });
});
test("pre-comparison header dictionaries normalize into duplicate-preserving editable rows", () => {
  const parsed = { headers: { host: ["a", "b"], "x-value": "same" }, query: [["q", "1"], ["q", "2"]] };
  assert.deepEqual(parserFieldValue(parsed, "headers"), [{ name: "host", value: "a" }, { name: "host", value: "b" }, { name: "x-value", value: "same" }]);
  assert.deepEqual(parserFieldValue(parsed, "query"), [{ name: "q", value: "1" }, { name: "q", value: "2" }]);
  const draft = expectationDraft({ headers: { state: "present", value: parserFieldValue(parsed, "headers") } });
  assert.equal(expectationPayload(draft).headers.value.length, 3);
});
test("basic header and query cells use one name/value per line without losing duplicates", () => {
  const pairs = [{ name: "q", value: "1" }, { name: "q", value: "2" }];
  assert.equal(parserValueText(pairs, "headers"), "q: 1\nq: 2");
  assert.equal(parserValueText(pairs, "query"), "q: 1\nq: 2");
  assert.equal(parserValueText('<script>raw</script>', "body"), '<script>raw</script>');
});
test("structured pair validation rejects objects, coerced values and unknown fields", () => {
  for (const text of ['{"x":"1"}', '[{"name":"x","value":1}]', '[{"name":"x","value":"1","extra":true}]', 'broken']) {
    const draft = expectationDraft(); draft.headers = { state: "present", text };
    assert.throws(() => expectationPayload(draft), /invalid_parser_pair_list/);
  }
});
test("priority is only presentation ordering, with conflicts and failures first", () => {
  assert.ok(casePriority({ selection_status: "ambiguous" }) < casePriority({ candidate: { parse_status: "failed" } }));
  assert.ok(casePriority({ changed_fields: ["uri"] }) < casePriority({}));
});
test("only active stored jobs keep polling eligible", () => {
  for (const status of ["pending", "running", "processing"]) assert.equal(parserSessionBusy({ jobs: [{ status }] }), true);
  for (const status of ["completed", "failed", "canceled"]) assert.equal(parserSessionBusy({ jobs: [{ status }] }), false);
  assert.equal(parserSessionBusy(null), false);
});
test("fixed error messages never display upstream logs or provider keys", () => {
  for (const english of [false, true]) {
    assert.doesNotMatch(parserWorkbenchError(new Error("PRIVATE PAYLOAD sk-secret"), english), /PRIVATE|sk-secret/);
    assert.ok(parserWorkbenchError({ status: 409 }, english));
  }
});
for (const [view, step] of [["new", "data"], ["session", "formats"], ["session", "verify"], ["legacy", undefined]]) {
  test(`parser ${view}/${step || "history"} routes round trip without data in history URLs`, () => {
    const state = { page: "settings", settingsTab: "parsers", parserRoute: { view, ...(step ? { step } : {}), ...(view === "session" ? { sessionId: id } : {}) } };
    const hash = writeAppHash(state), route = readAppHash(hash), restored = applyAppRoute({}, route);
    assert.equal(writeAppHash(restored), hash);
    assert.equal(route.page, "settings"); assert.equal(route.tab, "parsers");
    assert.doesNotMatch(hash, /payload|dataset|Cookie/);
  });
}
test("parser routes reject arbitrary IDs, query strings, steps and traversal", () => {
  for (const hash of ["#configure/parsers/sessions/secret/verify", `#configure/parsers/sessions/${id}/unknown`, `#configure/parsers/sessions/${id}/verify?payload=PRIVATE`, "#configure/parsers/new/../data"]) assert.equal(readAppHash(hash), null);
  assert.deepEqual(applyAppRoute({ parserRoute: { view: "legacy" } }, readAppHash("#configure/parsers")).parserRoute, { view: "list" });
});
test("workbench API uses authenticated noncached admin JSON with revision guards", async () => {
  const previous = globalThis.fetch, calls = [];
  globalThis.fetch = async (url, options) => { calls.push({ url, options }); return { ok: true, json: async () => ({}), headers: new Headers() }; };
  try {
    await parserWorkbenchApi.catalog(); await parserWorkbenchApi.createSession({ source: { test_run_id: id } });
    await parserWorkbenchApi.session(id); await parserWorkbenchApi.jobs(id);
    await parserWorkbenchApi.discover(id, { expected_revision: 1, profile_id: id, internal_data_confirmed: true });
    await parserWorkbenchApi.groups(id, [], 2); await parserWorkbenchApi.generate(id, { expected_revision: 3 });
    await parserWorkbenchApi.expectations(id, [], 4); await parserWorkbenchApi.validate(id, 5); await parserWorkbenchApi.saveCandidate(id, { name: "draft", expected_revision: 6 });
    await parserWorkbenchApi.sourcePreview({ source: { dataset_version_id: id }, limit: 20, offset: 0, max_cases: 50 });
    assert.ok(calls.every(call => call.url.startsWith("/api/v1/admin/parser-workbench/") && call.options.credentials === "include" && call.options.cache === "no-store"));
    assert.equal(JSON.parse(calls[5].options.body).expected_revision, 2);
    assert.equal(JSON.parse(calls[7].options.body).expected_revision, 4);
    assert.equal(JSON.parse(calls[8].options.body).expected_revision, 5);
    assert.equal(calls.at(-1).url, "/api/v1/admin/parser-workbench/source-preview");
    assert.ok(!calls.some(call => /activate|promote|payload=/.test(call.url)));
  } finally { globalThis.fetch = previous; }
});
test("default page is the real rule catalog, while legacy features remain linked", () => {
  const Page = load("./ParserManagement.jsx"); const html = renderToStaticMarkup(createElement(Page));
  assert.match(html, /벤더별 파서 규칙/); assert.match(html, /이전 파서 \/ 비교 이력/);
  assert.match(html, /불러오는 중/); assert.doesNotMatch(html, /운영 사용 중|실제 정규식 실행 완료/);
});
test("vendor options use Korean display ordering without changing or duplicating stored names", () => {
  const Page = load("./ParserWorkbench.jsx", "ParserRuleCatalog");
  const vendors = ["Zulu", "나무", "Alpha", "가나다", "Alpha"];
  const rules = vendors.map((waf_vendor, index) => ({ waf_vendor, rule_id: `rule-${index}`, version_id: "fixture-version", name: `format-${index}`, method: "raw_payload" }));
  const html = renderToStaticMarkup(createElement(Page, { catalog: { rules } }));
  const values = [...html.matchAll(/<option value="([^"]+)">/g)].map(match => match[1]);
  assert.deepEqual(values, [...new Set(vendors)].sort((left, right) => left.localeCompare(right, "ko")));
});
test("recent rule work shows ten rows with page controls instead of hiding older work", () => {
  const Page = load("./ParserWorkbench.jsx", "ParserRecentWork");
  const sessions = Array.from({ length: 24 }, (_, index) => ({ id: `${index}`, name: `work-${String(index + 1).padStart(2, "0")}` }));
  const html = renderToStaticMarkup(createElement(Page, { sessions }));
  assert.equal((html.match(/work-\d\d/g) || []).length, 10);
  assert.match(html, /work-10/); assert.doesNotMatch(html, /work-11/);
  assert.match(html, /규칙 작업 페이지/); assert.match(html, /처음/); assert.match(html, /다음/); assert.match(html, /끝/);
  assert.match(html, /24/); assert.match(html, /3페이지/);
});
test("review source and values are escaped and compare fields never borrow case denominators", () => {
  const Page = load("./ParserWorkbenchReview.jsx");
  const html = renderToStaticMarkup(createElement(Page, { session: { groups: [], cases: [{ case_id: id, role: "holdout", raw_payload: "<script>not executed</script>", comparisons: [{ field: "body", actual: "text", source_spans: [{ start: 0, end: 8 }] }] }], validation_summary: { field_total: 0, field_matches: 0, field_mismatches: 0, compared_cases: 999, holdout: { field_total: 0, field_matches: 0 }, ready: false } } }));
  assert.match(html, /&lt;script&gt;/); assert.doesNotMatch(html, /<script>/);
  assert.match(html, /비교 필드/); assert.doesNotMatch(html, /999/);
  assert.match(html, /규칙 제안에 쓰지 않은 문항/); assert.match(html, /후보 저장 조건을 충족하지 못했습니다/);
});
test("corrected expectations preserve the prior candidate without claiming it is the current reviewed version", () => {
  const Page = load("./ParserWorkbenchReview.jsx");
  const session = { groups: [], cases: [], candidate_version_id: id, status: "generated", validation_summary: null };
  const changed = renderToStaticMarkup(createElement(Page, { session }));
  assert.match(changed, /이전 후보 버전은 보존됩니다/);
  assert.match(changed, /후보 버전 저장/);
  assert.doesNotMatch(changed, /후보 버전을 저장했습니다/);
  const saved = renderToStaticMarkup(createElement(Page, { session: { ...session, status: "saved" } }));
  assert.match(saved, /후보 버전을 저장했습니다/);
  assert.doesNotMatch(saved, /버전 이름/);
});
