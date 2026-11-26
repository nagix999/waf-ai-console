import assert from "node:assert/strict";
import test from "node:test";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";
import { runInNewContext } from "node:vm";
import { buildSync } from "esbuild";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { outputValidationHistory } from "./outputValidationHistory.js";

const fixture = () => ({ output_validation_history: [{
  attempt: 1, grounding_attempt: 1, validation_issues: [{ field: "evidence", type: "evidence_support_conflict" }],
  evidence: [
    { position: 0, evidence_id: "e4", source_id: "c1", supports: "attack_request", interpretation_ko: "민감 파일의 사본을 직접 조회하려는 요청입니다.", related_evidence_ids: ["e2"] },
    { position: 1, evidence_id: "e2", source_id: "c1", supports: "normal_request", interpretation_ko: "범위를 지정한 파일 읽기로 해석했습니다.", related_evidence_ids: [] },
  ], truncated: false, omitted_evidence_count: 0,
}] });

function load() {
  const bundle = buildSync({ entryPoints: [fileURLToPath(new URL("./AgentHistory.jsx", import.meta.url))], bundle: true, write: false, platform: "node", format: "cjs", packages: "external", jsx: "automatic", loader: { ".css": "empty", ".md": "text" }, logLevel: "silent" });
  const module = { exports: {} };
  runInNewContext(bundle.outputFiles[0].text, { module, exports: module.exports, require: createRequire(import.meta.url), process, URL, Date });
  return module.exports;
}
const { OutputValidationHistory, default: AgentHistory } = load();
const render = (component, props) => renderToStaticMarkup(createElement(component, props));

test("failed-output evidence keeps exact IDs and same-source opposite interpretations without renumbering or repair", () => {
  const original = fixture(), stored = JSON.stringify(original);
  const [item] = outputValidationHistory(stored);
  assert.equal(item.attempt, 1); assert.equal(item.groundingAttempt, 1); assert.equal(item.limited, false);
  assert.deepEqual(item.evidence.map(value => [value.evidenceId, value.sourceId, value.supports, value.relatedIds]), [["e4", "c1", "attack_request", ["e2"]], ["e2", "c1", "normal_request", []]]);
  assert.deepEqual(item.issues, [{ field: "evidence", type: "evidence_support_conflict" }]);
  assert.equal(JSON.stringify(original), stored);
});

test("old and malformed records never invent evidence or read diagnostic-like values from metadata", () => {
  for (const value of [undefined, null, [], "", "not JSON", "{", {}, { evidence: fixture().output_validation_history[0].evidence }, { metadata: fixture() }, { output_validation_history: null }, { output_validation_history: [null, 42, []] }]) {
    assert.deepEqual(outputValidationHistory(value), []);
    assert.equal(render(OutputValidationHistory, { output: value }), "");
  }
  assert.deepEqual(outputValidationHistory('{"output_validation_history":[],"output_validation_history":[{}]}'), [], "ambiguous duplicate keys are not projected");
});

test("attempt identity is local to each call and never guessed from array position", () => {
  const output = fixture();
  output.output_validation_history.push({ ...output.output_validation_history[0], attempt: 1, grounding_attempt: 2 });
  output.output_validation_history.push({ attempt: 0, grounding_attempt: 99, evidence: [] });
  const items = outputValidationHistory(output);
  assert.deepEqual(items.map(item => [item.attempt, item.groundingAttempt]), [[1, 1], [1, 2], [null, null]]);
  const html = render(OutputValidationHistory, { output });
  assert.match(html, /초기 분석/); assert.match(html, /출처 교정/); assert.match(html, /호출 구분 미기록/); assert.match(html, /시도 번호 미기록/);
});

test("missing fields, truncation and omitted records are explicit rather than filled from another attempt", () => {
  const output = { output_validation_history: [{ attempt: 2, grounding_attempt: 1, validation_issues: [], evidence: [{ position: 3, omitted_fields: ["evidence_id", "supports", "related_evidence_ids"], interpretation_ko: "저장 가능한 앞부분", interpretation_truncated: true, links_omitted: true }], truncated: true, omitted_evidence_count: 2 }] };
  const [item] = outputValidationHistory(output);
  assert.equal(item.evidence[0].evidenceId, null); assert.equal(item.evidence[0].relatedIds, null); assert.equal(item.limited, true);
  const html = render(OutputValidationHistory, { output });
  assert.match(html, /목록 위치 4/); assert.doesNotMatch(html, />e4</); assert.match(html, /일부만 저장됨/); assert.match(html, /근거 2개 미저장/); assert.match(html, /저장하지 못한 필드/);
});

test("empty model interpretation is distinct from an interpretation that was not saved", () => {
  const output = fixture();
  output.output_validation_history[0].evidence[0].interpretation_ko = "";
  delete output.output_validation_history[0].evidence[1].interpretation_ko;
  const [item] = outputValidationHistory(output);
  assert.equal(item.evidence[0].interpretation, "");
  assert.equal(item.evidence[1].interpretation, null);
  const html = render(OutputValidationHistory, { output });
  assert.match(html, /해석 문장이 비어 있습니다/);
  assert.match(html, /해석 문장이 기록되지 않았습니다/);
});

test("legacy hostile nested fields cannot become React children, CSS classes, links or HTML", () => {
  const output = fixture();
  output.output_validation_history[0].evidence[0] = { evidence_id: { value: "not an ID" }, source_id: ["c1"], supports: "__proto__", interpretation_ko: '<script>window.BAD=true</script><img src="https://example.invalid/raw" onerror="bad()">', related_evidence_ids: ["e2", {}, null] };
  const html = render(OutputValidationHistory, { output });
  assert.match(html, /&lt;script&gt;/); assert.match(html, /&lt;img/);
  assert.doesNotMatch(html, /<script|<img|<a\s|support-__proto__|\[object Object\]/);
  assert.match(html, /support-unknown/);
});

test("projection bounds malformed legacy collections and exposes that it is partial", () => {
  const entry = fixture().output_validation_history[0];
  const output = { output_validation_history: Array.from({ length: 20 }, () => ({ ...entry, evidence: Array.from({ length: 20 }, () => ({ ...entry.evidence[0], interpretation_ko: "가".repeat(9000), related_evidence_ids: Array(15).fill("e2") })) })) };
  const items = outputValidationHistory(output);
  assert.equal(items.length, 12); assert.equal(items[0].evidence.length, 10); assert.equal(items[0].evidence[0].interpretation.length, 4096); assert.equal(items[0].evidence[0].relatedIds.length, 10); assert.equal(items[0].limited, true);
  assert.equal(items[0].evidence[0].interpretationTruncated, false);
  assert.equal(items[0].evidence[0].interpretationDisplayTruncated, true);
});

test("diagnostic is visibly unvalidated, uses keyboard-accessible native disclosure and keeps original JSON", () => {
  const output = fixture();
  const runs = [{ id: "run", status: "failed", steps: [{ id: "step", step_type: "llm_primary", status: "failed", output: JSON.stringify(output) }] }];
  const html = render(AgentHistory, { runs });
  assert.match(html, /검증을 통과하지 못한 출력/); assert.match(html, /최종 판정이나 검증된 근거가 아닙니다/);
  assert.match(html, /<details class="execution-validation-attempt" open=""/); assert.match(html, /<summary>/);
  assert.match(html, /evidence_support_conflict/); assert.match(html, /단계 처리 결과/); assert.match(html, /output_validation_history/);
  assert.match(html, /공격 요청 근거/); assert.match(html, /정상 요청 근거/);
});

test("input, metadata and public final-result views do not project the rejected-output panel", () => {
  const runs = [{ id: "run", status: "completed", steps: [{ id: "step", step_type: "llm_primary", status: "completed", output: JSON.stringify(fixture()), input: "synthetic input", metadata: {} }] }];
  for (const view of ["input", "metadata"]) {
    const html = render(AgentHistory, { runs, selection: { stepId: "step", view } });
    assert.doesNotMatch(html, /class="execution-validation-history"/);
  }
  const html = render(AgentHistory, { runs, finalResult: { schema_version: "waf-analysis-v3", evidence: [] }, selection: { stepId: "result" } });
  assert.doesNotMatch(html, /class="execution-validation-history"|output_validation_history/);
  assert.match(html, /최종 결과 JSON/);
});
