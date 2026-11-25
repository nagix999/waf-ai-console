import assert from "node:assert/strict";
import test from "node:test";
import { createRequire } from "node:module";
import { runInNewContext } from "node:vm";
import { buildSync } from "esbuild";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { inputFieldRows, resolveEvidenceInput, splitHighlightedText } from "./evidenceInputSource.js";

const raw = "POST /🔒?q=%3Cscript%3E HTTP/1.1\r\nHost: synthetic.invalid\r\nX-Note: repeated\r\n\r\nrepeated 한글 🔑 <script>synthetic()</script>\r\n";
const event = { payload: raw, extra_fields: { note: "repeated", nested: { items: ["second", 0, false] } } };
const pointSpan = (text, excerpt, last = false) => {
  const start = last ? text.lastIndexOf(excerpt) : text.indexOf(excerpt);
  return { start: [...text.slice(0, start)].length, end: [...text.slice(0, start + excerpt.length)].length };
};

test("recorded positions target the verified repeated occurrence and convert Unicode code points", () => {
  const target = { field: "event.payload", excerpt: "repeated", source_spans: [pointSpan(raw, "repeated", true)] };
  const result = resolveEvidenceInput(event, {}, target);
  assert.equal(result.found, true);
  assert.equal(result.ranges[0].start, raw.lastIndexOf("repeated"));
  assert.equal(raw.slice(result.ranges[0].start, result.ranges[0].end), target.excerpt);
  const parts = splitHighlightedText(raw, result.ranges);
  assert.equal(parts.map(part => part.text).join(""), raw);
  assert.deepEqual(parts.filter(part => part.highlighted).map(part => part.text), ["repeated"]);
  assert.equal(parts[0].text.includes("X-Note: repeated\r\n\r\n"), true);
});

test("bad recorded positions never fall back to another matching occurrence", () => {
  for (const spans of [null, "invalid", [{ start: 0, end: 8 }], [{ start: -1, end: 5 }], [{ start: "1", end: 2 }], [{ start: 1, end: 9999 }], [{ start: 1 }], [{ ...pointSpan(raw, "repeated"), guessed: true }]]) {
    const result = resolveEvidenceInput(event, {}, { field: "payload", excerpt: "repeated", source_spans: spans });
    assert.equal(result.found, false); assert.deepEqual(result.ranges, []);
  }
  assert.equal(resolveEvidenceInput(event, {}, { field: "payload.headers.x-note", excerpt: "repeated", source_spans: [pointSpan(raw, "repeated", true)] }).found, false);
  assert.equal(resolveEvidenceInput(event, {}, { field: "payload.body", excerpt: "repeated", source_spans: [pointSpan(raw, "repeated")] }).found, false);
});

test("missing positions use a unique exact occurrence in the specified literal HTTP field", () => {
  assert.equal(resolveEvidenceInput(event, {}, { field: "payload", excerpt: "repeated" }).found, false);
  const body = resolveEvidenceInput(event, {}, { field: "payload.body", excerpt: "repeated" });
  assert.equal(body.found, true); assert.equal(body.ranges[0].start, raw.lastIndexOf("repeated"));
  const header = resolveEvidenceInput(event, {}, { field: "payload.headers.x-note", excerpt: "repeated" });
  assert.equal(header.found, true); assert.equal(header.ranges[0].start, raw.indexOf("repeated"));
  assert.equal(resolveEvidenceInput(event, {}, { field: "payload.query.q", excerpt: "q=%3Cscript%3E" }).found, true);
  assert.equal(resolveEvidenceInput(event, {}, { field: "payload.query", excerpt: "<script>" }).found, false);
  assert.equal(resolveEvidenceInput(event, {}, { field: "payload.headers.host", excerpt: "repeated" }).found, false);
  assert.equal(resolveEvidenceInput(event, {}, { field: "payload.body", excerpt: "X-Note:" }).found, false);
  const absolute = { payload: "GET https://synthetic.invalid/path?q=value HTTP/1.1\r\n\r\n" };
  assert.equal(resolveEvidenceInput(absolute, {}, { field: "payload.path", excerpt: "synthetic.invalid" }).found, false);
  assert.equal(resolveEvidenceInput(absolute, {}, { field: "payload.path", excerpt: "/path" }).found, true);
});

test("decoded or guessed fields never highlight payload even when the same characters exist", () => {
  for (const field of ["decoded_payload_hints.items.0.decoded", "payload.decoded", "payload.unknown"]) {
    assert.equal(resolveEvidenceInput(event, {}, { field, excerpt: "repeated", source_spans: [pointSpan(raw, "repeated")] }).found, false);
  }
  assert.equal(resolveEvidenceInput({ payload: "a\\r\\n\\r\\nbody" }, {}, { field: "payload.body", excerpt: "body" }).found, false);
});

test("field sources stay in their own scalar with array paths, false and zero preserved", () => {
  const value = resolveEvidenceInput(event, { signature: "repeated" }, { field: "event.signature", excerpt: "repeated" });
  assert.equal(value.section, "fields"); assert.equal(value.rowField, "signature");
  assert.equal(resolveEvidenceInput(event, {}, { field: "note", excerpt: "repeated" }).rowField, "extra_fields.note");
  for (const [field, excerpt] of [["nested.items[1]", "0"], ["extra_fields.nested.items.2", "false"]]) assert.equal(resolveEvidenceInput(event, {}, { field, excerpt }).found, true);
  assert.equal(resolveEvidenceInput(event, {}, { field: "constructor.name", excerpt: "Object" }).found, false);
  assert.equal(resolveEvidenceInput({ extra_fields: { note: "same same" } }, {}, { field: "note", excerpt: "same" }).found, false);
  const rows = inputFieldRows({ signature: "repeated", expected_verdict: "false_positive", result: {} }, event);
  assert.equal(rows.some(row => row.field === "expected_verdict" || row.field === "result"), false);
  assert.equal(rows.find(row => row.field === "extra_fields.nested.items.1").text, "0");
  assert.equal(rows.find(row => row.field === "extra_fields.nested.items.2").text, "false");
  const dotted = inputFieldRows({}, { extra_fields: { "a.b": "literal", a: { b: "nested" } } });
  assert.equal(new Set(dotted.map(row => row.id)).size, 2);
});

const bundle = buildSync({ stdin: { contents: 'export { TestAnalysisWorkspace, ExecutionView } from "./AnalysisDecision.jsx"; export { default as AgentHistory } from "./AgentHistory.jsx"; export { rememberEvidenceMeta } from "./analysisEvidenceMeta.js";', resolveDir: new URL(".", import.meta.url).pathname }, bundle: true, write: false, platform: "node", format: "cjs", packages: "external", jsx: "automatic", loader: { ".css": "empty", ".md": "text" }, logLevel: "silent" });
const module = { exports: {} }; runInNewContext(bundle.outputFiles[0].text, { module, exports: module.exports, require: createRequire(import.meta.url), process, URL, Date });
const render = (component, props) => renderToStaticMarkup(createElement(component, props));

test("real split workspace renders unchanged model text, exact inert raw source, and field-only highlights", () => {
  const result = { schema_version: "waf-analysis-v3", verdict: "inconclusive", summary_ko: "합성 판정 요약", threat_analysis: { severity: "UNKNOWN" }, evidence: [{ evidence_id: "e4", field: "signature", excerpt: "repeated", source_spans: [], supports: "context", interpretation_ko: "실제 저장된 참고 설명", origins: [] }], decision_issues: [] };
  const detail = { id: "synthetic", status: "completed", analysis_purpose: "test", signature: "repeated", result };
  module.exports.rememberEvidenceMeta(detail, { view: "core-v2", status: "completed" });
  const before = JSON.stringify({ event, detail });
  const html = render(module.exports.TestAnalysisWorkspace, { detail, event, target: result.evidence[0], onSelect() {}, active: false });
  assert.match(html, /test-results-pane/); assert.match(html, /test-input-pane/);
  assert.match(html, /실제 저장된 참고 설명/); assert.match(html, /test-input-field-selected/);
  assert.match(html, /<mark>repeated<\/mark>/);
  assert.doesNotMatch(html, /data-source-highlight="true"/);
  assert.match(html, /&lt;script&gt;synthetic\(\)&lt;\/script&gt;/);
  assert.doesNotMatch(html, /<script>|<iframe|dangerouslySetInnerHTML/);
  assert.equal(JSON.stringify({ event, detail }), before);
});

test("execution selection displays only the chosen saved output and retains missing records", () => {
  const runs = [{ id: "run-1", status: "completed", steps: [{ id: "step-1", name: "llm_primary", status: "completed", output: "saved primary", input: "saved input", metadata: {} }, { id: "step-2", name: "llm_verifier", status: "failed", output: null, input: "saved verifier input", metadata: { usage: { input_tokens: 0, output_tokens: null } }, tool_calls: [{ name: "saved-tool" }] }] }];
  const html = render(module.exports.AgentHistory, { runs, selection: { runId: "run-1", stepId: "step-2", view: "output" }, active: false });
  assert.match(html, /자료가 기록되지 않았습니다/); assert.doesNotMatch(html, /saved primary|saved verifier input/);
  assert.match(html, /출력 토큰<\/dt><dd>미기록/); assert.match(html, /입력 토큰<\/dt><dd>0/);
  const metadata = render(module.exports.AgentHistory, { runs, selection: { runId: "run-1", stepId: "step-2", view: "metadata" }, active: false });
  assert.match(metadata, /tool_calls/); assert.match(metadata, /배열 · 1개/);
});

test("a completed case with no stored result is not presented as waiting for an analysis", () => {
  const html = render(module.exports.TestAnalysisWorkspace, { detail: { id: "missing-result", status: "completed", analysis_purpose: "test", result: null }, event, active: false });
  assert.match(html, /결과 본문 미기록/); assert.match(html, /저장된 결과 본문이 없습니다/);
  assert.doesNotMatch(html, /완료된 판정 근거를 기다리고 있습니다/);
});
