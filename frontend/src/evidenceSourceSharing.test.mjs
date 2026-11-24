import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import { runInNewContext } from "node:vm";
import { buildSync } from "esbuild";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { evidenceSourceReferences, sharedSourceText } from "./evidenceSourceSharing.js";
import { rememberEvidenceMeta } from "./analysisEvidenceMeta.js";
import { buildAnalysisReport, decodeReportText } from "./analysisReport.js";
import { buildReportDocument } from "./reportDocument.js";

const excerpt = "Cookie: SID=synthetic <img src=https://never.invalid/onerror>\r\nRange: bytes=0-65536  ";
const original = () => ({ evidence_id: "e3", number: 3, field: "payload", excerpt, source_spans: [{ start: 20, end: 120 }], supports: "attack_request", interpretation_ko: "민감한 자료를 내려받으려는 요청 문맥을 뒷받침합니다.", origins: [{ role: "primary", index: 0, source_id: "c1" }] });
const clone = value => structuredClone(value);

test("physical source sharing keeps each evidence, interpretation, direction and API order", () => {
  const first = original(), second = { ...clone(first), evidence_id: "e6", supports: "normal_request", interpretation_ko: "허용된 전송 방식과 정상 기능 문맥을 따로 검토한 해석입니다." };
  const input = [first, second], before = JSON.stringify(input), rows = evidenceSourceReferences(input);
  assert.deepEqual(rows.map(row => row.sourceIndex), [0, 0]);
  assert.strictEqual(rows[0].item, first); assert.strictEqual(rows[1].item, second);
  assert.equal(JSON.stringify(input), before);
  assert.equal(rows[1].item.interpretation_ko, second.interpretation_ko);
  assert.equal(sharedSourceText(3), "같은 원문은 근거 3에서 확인할 수 있습니다.");
  assert.equal(sharedSourceText(3, (ko, en) => en), "The source excerpt is shared with evidence 3.");
});

for (const [name, mutate] of [
  ["different field", item => { item.field = "extra_fields.request"; }],
  ["different position", item => { item.source_spans = [{ start: 200, end: 300 }]; }],
  ["different whitespace", item => { item.excerpt += " "; }],
  ["different line endings", item => { item.excerpt = item.excerpt.replace("\r\n", "\n"); }],
  ["missing positions", item => { delete item.source_spans; }],
  ["empty positions", item => { item.source_spans = []; }],
  ["incomplete position", item => { item.source_spans = [{ start: 20 }]; }],
  ["invalid position", item => { item.source_spans = [{ start: 20, end: 20 }]; }],
  ["coercible position", item => { item.source_spans = [{ start: "20", end: 120 }]; }],
  ["unknown position properties", item => { item.source_spans[0].assumed = true; }],
]) test(`source excerpts are not shared for ${name}`, () => {
  const a = original(), b = clone(a); mutate(b);
  assert.deepEqual(evidenceSourceReferences([a, b]).map(row => row.sourceIndex), [0, 1]);
});

test("two absent or empty source positions never imply that an identical string has the same location", () => {
  for (const positions of [undefined, [], null]) {
    const a = { ...original(), source_spans: positions };
    assert.deepEqual(evidenceSourceReferences([a, clone(a)]).map(row => row.sourceIndex), [0, 1]);
  }
  const a = { ...original(), source_spans: [{ start: 20, end: 120 }, { start: 200, end: 300 }] };
  const b = { ...clone(a), source_spans: [...a.source_spans].reverse() };
  assert.deepEqual(evidenceSourceReferences([a, b]).map(row => row.sourceIndex), [0, 1]);
});

function detail() {
  const cases = JSON.parse(readFileSync(new URL("../../backend/tests/fixtures/core_evidence_v3.json", import.meta.url)));
  const result = clone(cases[0].expected.result);
  result.evidence = [original(), { ...clone(original()), evidence_id: "e6", supports: "normal_request", interpretation_ko: "반대 방향의 독립적인 정상 사용 해석을 유지합니다." },
    { ...clone(original()), evidence_id: "e4", supports: "context", source_spans: [{ start: 200, end: 300 }], interpretation_ko: "다른 위치에 같은 문구가 있으므로 별도로 확인합니다." },
    { ...clone(original()), evidence_id: "e1", supports: "context", source_spans: [], interpretation_ko: "위치가 기록되지 않은 별도 근거도 그대로 유지합니다." }];
  result.decision_issues = [];
  const value = { id: "synthetic-shared", status: "completed", result };
  rememberEvidenceMeta(value, { view: "core-v1", status: "completed" });
  return value;
}

test("Markdown and PDF document reuse one exact source without deleting any analysis or reordering API evidence", () => {
  const value = detail(), before = JSON.stringify(value);
  const markdown = buildAnalysisReport(value, { evidenceView: "core-v1" });
  const report = buildReportDocument({ detail: value, evidenceView: "core-v1" });
  const evidence = report.sections.find(section => section.title === "보안 분석 근거");
  assert.equal(evidence.blocks.filter(block => block.type === "code").length, 3);
  assert.deepEqual(evidence.blocks.filter(block => block.type === "code").map(block => block.text), [excerpt, excerpt, excerpt]);
  assert.deepEqual([...decodeReportText(markdown).matchAll(/^#### 근거 (\d+) —/gm)].map(match => Number(match[1])), [3, 6, 4, 1]);
  assert.match(decodeReportText(markdown), /같은 원문은 근거 3에서 확인할 수 있습니다/);
  for (const item of value.result.evidence) assert.ok(decodeReportText(markdown).includes(item.interpretation_ko));
  assert.equal(JSON.stringify(value), before);
});

const compiled = buildSync({ stdin: { contents: 'export { EvidenceCards, EvidenceWorkspace } from "./AnalysisDecision.jsx"; export { rememberEvidenceMeta } from "./analysisEvidenceMeta.js";', resolveDir: new URL(".", import.meta.url).pathname }, bundle: true, write: false, platform: "node", format: "cjs", packages: "external", jsx: "automatic", loader: { ".css": "empty", ".md": "text" }, logLevel: "silent" });
const module = { exports: {} }; runInNewContext(compiled.outputFiles[0].text, { module, exports: module.exports, require: createRequire(import.meta.url), process, URL, Date });

test("real core cards keep all interpretations but show a physically shared excerpt once with a keyboard source target", () => {
  const value = detail(), before = JSON.stringify(value);
  module.exports.rememberEvidenceMeta(value, { view: "core-v1", status: "completed" });
  const html = renderToStaticMarkup(createElement(module.exports.EvidenceCards, { detail: value, onViewInput() {} }));
  assert.equal((html.match(/class="decision-evidence-row decision-core-evidence /g) || []).length, 4);
  assert.equal((html.match(/<code /g) || []).length, 3);
  assert.match(html, /같은 원문 보기 \(근거 3\)/);
  assert.match(html, /aria-controls="[^"]+-source-0"/);
  assert.match(html, /tabindex="0" aria-label="근거 3 원문 발췌"/);
  assert.deepEqual([...html.matchAll(/<strong>근거 (\d+)<\/strong>/g)].map(match => Number(match[1])), [3, 6, 4, 1]);
  for (const item of value.result.evidence) assert.ok(html.includes(item.interpretation_ko));
  assert.doesNotMatch(html, /<img|<script|onerror=/);
  assert.equal(JSON.stringify(value), before);
});

test("evidence workspace preserves each selectable interpretation without repeating its exact shared source", () => {
  const value = detail();
  module.exports.rememberEvidenceMeta(value, { view: "core-v1", status: "completed" });
  const html = renderToStaticMarkup(createElement(module.exports.EvidenceWorkspace, { detail: value, target: value.result.evidence[1], onSelect() {}, active: false }));
  assert.equal((html.match(/aria-pressed=/g) || []).length, 4);
  assert.equal((html.match(/<code>/g) || []).length, 3);
  assert.match(html, /같은 원문은 근거 3에서 확인할 수 있습니다/);
  assert.match(html, /반대 방향의 독립적인 정상 사용 해석을 유지합니다/);
  assert.doesNotMatch(html, /<img|<script/);
});
