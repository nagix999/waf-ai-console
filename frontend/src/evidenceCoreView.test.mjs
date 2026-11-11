import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import { runInNewContext } from "node:vm";
import { buildSync } from "esbuild";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { api } from "./api.js";
import { analysisEvidenceMeta, rememberEvidenceMeta, responseEvidenceMeta } from "./analysisEvidenceMeta.js";
import { assessmentView } from "./assessmentData.js";
import { decisionConditions } from "./decisionDetail.js";
import { buildAnalysisReport, decodeReportText } from "./analysisReport.js";
import { buildReportDocument } from "./reportDocument.js";

const fixtures = JSON.parse(readFileSync(new URL("../../backend/tests/fixtures/evidence_display_plan.json", import.meta.url)));
const coreFixtures = JSON.parse(readFileSync(new URL("../../backend/tests/fixtures/core_evidence_v3.json", import.meta.url)));
const copy = value => structuredClone(value);

for (const item of coreFixtures) test(`Python/JS core response contract: ${item.name}`, () => {
  const value = { status: "completed", result: copy(item.expected.result), evidence_display: item.display_view };
  const before = JSON.stringify(value);
  rememberEvidenceMeta(value, { view: "core-v1", status: item.expected.metadata.status });
  const view = assessmentView(value.result, { display: value.evidence_display });
  assert.equal(view.core, true); assert.equal(view.display, undefined);
  assert.deepEqual(view.evidence.map(source => source.evidence_id), item.expected.result.evidence.map(source => source.evidence_id));
  assert.deepEqual(view.evidence.map(source => source.number), item.expected.result.evidence.map(source => Number(source.evidence_id.slice(1))));
  for (let index = 0; index < view.evidence.length; index++) for (const [key, expected] of Object.entries(item.expected.result.evidence[index])) assert.deepEqual(view.evidence[index][key], expected);
  const report = decodeReportText(buildAnalysisReport(value));
  const numbers = [...report.matchAll(/^#### 근거 (\d+) — /gm)].map(match => Number(match[1]));
  assert.deepEqual(numbers, view.evidence.map(source => source.number));
  assert.equal(JSON.stringify(value), before);
});
function detail() {
  const result = copy(fixtures[0].result);
  result.evidence = ["e3", "e6", "e4", "e1"].map(id => result.evidence.find(item => item.evidence_id === id));
  // A full-source map is deliberately stale and must never be reapplied.
  result.evidence_presentation = copy(fixtures[2].result.evidence_presentation);
  return { id: "synthetic-core", status: "completed", result, evidence_display: {
    version: "evidence-display-view-v1", status: "completed", plan: copy(fixtures[0].expected), relations: copy(fixtures[0].relations),
  } };
}

test("response headers are WeakMap-only metadata; the API JSON and canonical evidence remain unchanged", () => {
  const value = detail(), before = JSON.stringify(value), keys = Reflect.ownKeys(value);
  responseEvidenceMeta(value, new Headers({ "X-Evidence-View": "core-v1", "X-Evidence-Status": "completed", "X-Untrusted": "ignored" }));
  assert.deepEqual(analysisEvidenceMeta(value), { view: "core-v1", status: "completed" });
  assert.equal(analysisEvidenceMeta(value.result), analysisEvidenceMeta(value));
  assert.deepEqual(Reflect.ownKeys(value), keys); assert.equal(JSON.stringify(value), before);
  assert.equal(analysisEvidenceMeta(copy(value)), null);
});

for (const [view, status] of [["core-v2", "completed"], ["core-v1", "unknown"], [null, "completed"], ["core-v1", null]]) test(`unrecognized metadata is not trusted: ${view}/${status}`, () => {
  const value = detail(); responseEvidenceMeta(value, new Headers({ ...(view ? { "X-Evidence-View": view } : {}), ...(status ? { "X-Evidence-Status": status } : {}) }));
  assert.equal(analysisEvidenceMeta(value), null);
});

test("the fetch client reads metadata without adding any response field", async () => {
  const value = detail(), original = globalThis.fetch;
  globalThis.fetch = async () => new Response(JSON.stringify(value), { headers: { "X-Evidence-View": "core-v1", "X-Evidence-Status": "fallback" } });
  try {
    const received = await api.analysis("synthetic-core");
    assert.deepEqual(received, value);
    assert.deepEqual(api.analysisEvidenceMeta(received), { view: "core-v1", status: "fallback" });
  } finally { globalThis.fetch = original; }
});

test("core view preserves server order, original numbers, directions and explanations without grouping", () => {
  const value = detail(), before = JSON.stringify(value);
  rememberEvidenceMeta(value, { view: "core-v1", status: "completed" });
  const view = assessmentView(value.result, { display: value.evidence_display });
  assert.equal(view.core, true); assert.equal(view.display, undefined);
  assert.deepEqual(view.evidence.map(item => item.number), [3, 6, 4, 1]);
  for (let index = 0; index < value.result.evidence.length; index++) for (const [key, expected] of Object.entries(value.result.evidence[index])) assert.deepEqual(view.evidence[index][key], expected);
  assert.equal(JSON.stringify(value), before);
});

test("even fallback, empty input, and identical-looking core items never restore or regroup missing evidence", () => {
  for (const count of [0, 1, 4]) {
    const value = detail(); value.result.evidence = value.result.evidence.slice(0, count);
    if (count > 1) value.result.evidence[1] = { ...copy(value.result.evidence[0]), evidence_id: "e6" };
    rememberEvidenceMeta(value, { view: "core-v1", status: "fallback" });
    const view = assessmentView(value.result);
    assert.equal(view.evidence.length, count); assert.equal(view.display, undefined);
  }
});

test("core conditions preserve every server-provided condition and its exact reference order", () => {
  const value = detail(); value.result.verdict = "inconclusive";
  value.result.decision_issues = [
    { point_ko: "같은 합성 조건", missing_condition_ko: "같은 확인 내용", evidence_ids: ["e6", "e3"] },
    { point_ko: "같은 합성 조건", missing_condition_ko: "같은 확인 내용", evidence_ids: ["e4"] },
    { point_ko: "이미 확인한 합성 조건", missing_condition_ko: null, evidence_ids: ["e1"] },
  ];
  rememberEvidenceMeta(value, { view: "core-v1", status: "completed" });
  const conditions = decisionConditions(value);
  assert.equal(conditions.items.length, 3);
  assert.deepEqual(conditions.items.map(item => item.evidence_numbers), [[6, 3], [4], [1]]);
});

test("web and offline PDF formatter use the received evidence once, in API order, without full-source mappings", () => {
  const value = detail(), before = JSON.stringify(value);
  const text = decodeReportText(buildAnalysisReport(value, { evidenceView: "core-v1" }));
  const positions = [3, 6, 4, 1].map(number => text.indexOf(`#### 근거 ${number} —`));
  assert.deepEqual([...positions].sort((a, b) => a - b), positions); assert.ok(positions.every(position => position >= 0));
  assert.doesNotMatch(text, /#### 근거 2 —|#### 근거 5 —|나머지 근거|정리됨 —|함께 본 원문/);
  const document = buildReportDocument({ detail: value, evidenceView: "core-v1" });
  const evidence = document.sections.find(section => section.title === "보안 분석 근거");
  assert.deepEqual(evidence.blocks.filter(block => block.type === "heading").map(block => block.text.match(/^근거 (\d+)/)?.[1]).filter(Boolean), ["3", "6", "4", "1"]);
  assert.equal(JSON.stringify(value), before);
});

const compiled = buildSync({ stdin: { contents: 'export { EvidenceCards, EvidenceWorkspace } from "./AnalysisDecision.jsx"; export { rememberEvidenceMeta } from "./analysisEvidenceMeta.js";', resolveDir: new URL(".", import.meta.url).pathname }, bundle: true, write: false, platform: "node", format: "cjs", packages: "external", jsx: "automatic", loader: { ".css": "empty", ".md": "text" }, logLevel: "silent" });
const module = { exports: {} }; runInNewContext(compiled.outputFiles[0].text, { module, exports: module.exports, require: createRequire(import.meta.url), process, URL, Date });
test("React core cards preserve the received list without more-evidence toggles or alternate source links", () => {
  const value = detail(); module.exports.rememberEvidenceMeta(value, { view: "core-v1", status: "completed" });
  const html = renderToStaticMarkup(createElement(module.exports.EvidenceCards, { detail: value, onViewInput() {}, onExecution() {} }));
  assert.equal((html.match(/class="decision-evidence-row decision-core-evidence /g) || []).length, 4);
  assert.doesNotMatch(html, /decision-additional-toggle|decision-display-group |함께 본 원문|정리됨 —/);
  assert.match(html, /전체 원본 근거는 실행 이력에서 확인/);
  const labels = [...html.matchAll(/<strong>근거 (\d+)<\/strong>/g)].map(match => Number(match[1]));
  assert.deepEqual(labels, [3, 6, 4, 1]);
});
