import assert from "node:assert/strict";
import test from "node:test";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";
import { runInNewContext } from "node:vm";
import { buildSync } from "esbuild";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { recordedProfiles, recordedInputFields, evidenceLocation } from "./inferenceDetail.js";

function load(file) {
  const bundle = buildSync({ entryPoints: [fileURLToPath(new URL(file, import.meta.url))], bundle: true, write: false, platform: "node", format: "cjs", packages: "external", jsx: "automatic", loader: { ".css": "empty", ".md": "text" }, logLevel: "silent" });
  const module = { exports: {} }; runInNewContext(bundle.outputFiles[0].text, { module, exports: module.exports, require: createRequire(import.meta.url), process, URL, Date }); return module.exports;
}
const render = (component, props) => renderToStaticMarkup(createElement(component, props));

test("execution settings are recorded values, never fabricated role assignments", () => {
  const detail = { model_profile: "old-primary", result: { agent: { role_profiles: { primary: { model_profile: "pinned-primary" }, verifier: { model_profile: "pinned-verifier" } } } } };
  const runs = [{ steps: [{ step_type: "llm_primary", metadata: { model_profile: "trace-primary" } }, { step_type: "llm_evidence_editor", metadata: { model_profile: "recorded-editor" } }] }];
  assert.deepEqual(recordedProfiles(detail, runs), { primary: "pinned-primary", verifier: "pinned-verifier", editor: "recorded-editor" });
  assert.deepEqual(recordedProfiles({ model_profile: "legacy" }), { primary: "legacy", verifier: undefined, editor: undefined });
  assert.deepEqual(recordedProfiles({}, [{ steps: [] }, ...runs]), { primary: undefined, verifier: undefined, editor: undefined });
});

test("metadata summary hides identifiers and never invents missing timing or versions", () => {
  const { default: Summary } = load("./InferenceSummary.jsx");
  const html = render(Summary, { detail: { status: "failed", analysis_purpose: "test", model_profile: "saved-primary", id: "hidden-id", queue_wait_ms: 0, input_schema_metadata: { version_id: "hidden-version-id", version_number: 4, content_hash: "hidden-hash" } } });
  assert.match(html, /saved-primary/); assert.match(html, /v4/); assert.match(html, /미기록/); assert.match(html, /미측정/);
  assert.doesNotMatch(html, /hidden-id|hidden-version-id|hidden-hash/);
});

test("only event input fields may be viewed as field evidence, never reference answers or results", () => {
  assert.deepEqual(recordedInputFields({ signature: "fixture", expected_verdict: "false_positive", evaluation: {}, payload: "secret", result: {}, prompt_version: "x" }), { signature: "fixture" });
  assert.equal(evidenceLocation({ payload: "false_positive", extra_fields: {} }, {}, { field: "expected_verdict", excerpt: "false_positive" }).found, false);
});

test("evidence jumps search literal HTTP including encoding and CRLF; no decoding or fuzzy matching", () => {
  const event = { payload: "GET /%3Cscript%3E HTTP/1.1\r\nHost: fixture\r\n", extra_fields: {} };
  for (const field of ["payload", "event.payload", "payload.request_line", "raw_payload"]) {
    const found = evidenceLocation(event, {}, { field, excerpt: "%3Cscript%3E" });
    assert.equal(found.section, "http"); assert.equal(found.found, true); assert.equal(found.value, event.payload);
  }
  assert.equal(evidenceLocation(event, {}, { field: "payload", excerpt: "<script>" }).found, false);
  assert.equal(evidenceLocation(event, {}, { field: "payload", excerpt: "host: fixture" }).found, false);
  assert.equal(evidenceLocation(event, {}, { field: "payload", excerpt: "\r\nHost" }).found, true);
});

test("field jumps keep the exact source, even if the same text appears in HTTP or a different field", () => {
  const event = { payload: "shared", extra_fields: { vendor: { note: "shared", score: 0, enabled: false }, other: "needle" } };
  assert.deepEqual(evidenceLocation(event, { signature: "shared" }, { field: "event.signature", excerpt: "shared" }), { section: "fields", field: "event.signature", value: "shared", query: "shared", found: true });
  for (const field of ["vendor.note", "extra_fields.vendor.note"]) assert.equal(evidenceLocation(event, {}, { field, excerpt: "shared" }).found, true);
  assert.equal(evidenceLocation(event, {}, { field: "vendor.note", excerpt: "needle" }).found, false);
  assert.equal(evidenceLocation(event, {}, { field: "vendor.score", excerpt: "0" }).found, true);
  assert.equal(evidenceLocation(event, {}, { field: "vendor.enabled", excerpt: "false" }).found, true);
  for (const field of ["signature", "vendor.__proto__.toString", "constructor.name", "parsed.path", "payload_typo"]) assert.equal(evidenceLocation(event, {}, { field, excerpt: "shared" }).found, false);
  assert.equal(evidenceLocation(event, {}, { field: "payload", excerpt: "" }), null);
});

test("format and citation retries are counted independently across original and repair calls", () => {
  const { RepairSummary } = load("./AgentHistory.jsx");
  const html = render(RepairSummary, { metadata: { output_validation_retry: { attempt_count: 1 }, evidence_grounding_retry: { attempted: true, recovered: true, attempt_count: 2, attempts: [{ output_validation_retry: { attempt_count: 3 } }, { output_validation_retry: { attempt_count: 1 } }] } } });
  assert.match(html, /출력 형식 재시도<\/dt><dd>2회/); assert.match(html, /근거 인용 교정<\/dt><dd>1회/); assert.match(html, /원문 대조 통과/);
  const unknown = render(RepairSummary, { metadata: { evidence_grounding_retry: { attempted: true } } });
  assert.match(unknown, /미기록/); assert.doesNotMatch(unknown, /NaN|0회/);
  assert.equal(render(RepairSummary, {}), "");
});

test("tabs have explicit panel associations, selected tab order and hidden inactive panels", () => {
  const { default: Tabs } = load("./DetailTabs.jsx");
  const html = render(Tabs, { label: "fixture", value: "result", items: [["result", "결과"], ["raw", "입력"]], children: key => key });
  assert.match(html, /role="tablist"/); assert.equal((html.match(/role="tabpanel"/g) || []).length, 2);
  assert.match(html, /aria-selected="true".*?tabindex="0"/); assert.match(html, /aria-selected="false".*?tabindex="-1"/);
  assert.match(html, /aria-labelledby="[^"]+-raw" hidden=""/);
});

test("input renders hostile content as inert text and keeps decoding a separate tab", () => {
  const { default: Input } = load("./RawEventView.jsx");
  const html = render(Input, { event: { payload: '<script>fixture()</script>', extra_fields: {} }, detail: {} });
  assert.match(html, /&lt;script&gt;/); assert.doesNotMatch(html, /<script|<iframe/);
  assert.match(html, /인코딩·난독화/); assert.match(html, /열람 기록이 남습니다/);
});
