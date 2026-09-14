import assert from "node:assert/strict";
import test from "node:test";
import { assessmentView } from "./assessmentData.js";
import { buildAnalysisReport, decodeReportText } from "./analysisReport.js";
import { matrixDrilldown, metricHelp } from "./evaluationMetrics.js";
import { selectionIssue, hasExternalRole } from "./agentSettings.js";

const result = () => ({ verdict: "inconclusive", analyst_assessment: {
  version: "analyst-assessment-v1", evidence: [
    { evidence_id: "e1", field: "payload.query", excerpt: "q=test", supports: "false_positive", interpretation_ko: "일반 검색 값입니다." },
    { evidence_id: "e2", field: "payload.query", excerpt: "q=test", supports: "false_positive", interpretation_ko: "검색어 값이며 실행 구문은 없습니다." },
    { evidence_id: "e3", field: "payload.query", excerpt: "q=test", supports: "true_positive", interpretation_ko: "다른 의미의 관찰은 보존합니다." },
  ], decision_issues: [{ point_ko: "처리 방식을 확인해야 합니다.", evidence_ids: ["e1", "e2", "e3"], missing_condition_ko: "검색어 처리 규칙" }],
}, evidence_presentation: { version: "evidence-editor-v1", status: "completed", groups: [
  { member_ids: ["e1", "e2"], representative_id: "e2" }, { member_ids: ["e3"], representative_id: "e3" },
] } });

test("representative explanation, opposing evidence, and all issue references survive", () => {
  const input = result(), before = structuredClone(input);
  const view = assessmentView(input);
  assert.equal(view.evidence.length, 2);
  assert.equal(view.evidence[0].interpretation_ko, input.analyst_assessment.evidence[1].interpretation_ko);
  assert.deepEqual(view.issues[0].evidence_numbers, [1, 2]);
  assert.equal(view.issues[0].missing_condition_ko, "검색어 처리 규칙");
  const report = decodeReportText(buildAnalysisReport({ status: "completed", result: input }));
  assert.ok(report.includes("검색어 값이며 실행 구문은 없습니다."));
  assert.ok(!report.includes("일반 검색 값입니다."));
  assert.deepEqual(input, before);
});

test("invalid, incomplete, opposing or changed-source mappings retain all originals", () => {
  for (const corrupt of [
    value => value.evidence_presentation.groups.pop(),
    value => value.evidence_presentation.groups[0].member_ids.push("unknown"),
    value => value.evidence_presentation.groups[0].member_ids.push("e1"),
    value => { value.evidence_presentation.groups = [{ member_ids: ["e1", "e2", "e3"], representative_id: "e2" }]; },
    value => { value.analyst_assessment.evidence[0].source_spans = [{ start: 1, end: 7 }]; },
  ]) {
    const input = result(); corrupt(input);
    assert.equal(assessmentView(input).evidence.length, 3);
  }
});

test("hold-reference drilldown separates final attack and normal predictions", () => {
  assert.deepEqual(matrixDrilldown("expected_hold_positive"), { reference_verdict: "inconclusive", verdict: "true_positive", evaluation_outcome: "expected_abstention_mismatch" });
  assert.deepEqual(matrixDrilldown("expected_hold_negative"), { reference_verdict: "inconclusive", verdict: "false_positive", evaluation_outcome: "expected_abstention_mismatch" });
  assert.deepEqual(matrixDrilldown("expected_hold_match"), { reference_verdict: "inconclusive", verdict: "inconclusive", evaluation_outcome: "expected_abstention_match" });
  assert.match(metricHelp.expected_hold_decided_rate, /미탐·과탐률과 다릅니다/);
});

test("editor selection needs a verified profile and acknowledges external transfer", () => {
  const catalog = { state_token: "a".repeat(64), profiles: [{ id: "local", provider: "vllm", can_assign: true }, { id: "remote", provider: "openai", can_assign: true }], assignments: { production: { primary_profile_id: "local", verifier_profile_id: null, evidence_editor_enabled: true, evidence_editor_profile_id: "remote" }, test: { primary_profile_id: null, verifier_profile_id: null } } };
  assert.equal(selectionIssue(catalog, catalog.assignments), "");
  assert.equal(hasExternalRole(catalog, catalog.assignments), true);
  catalog.profiles[1].can_assign = false;
  assert.match(selectionIssue(catalog, catalog.assignments), /전체 검증/);
});
