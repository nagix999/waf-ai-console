import assert from "node:assert/strict";
import test from "node:test";
import { compactEvaluation, evaluationExplanation, evaluationOutcomeText, evaluationOutcomes, evaluationTone, isMockAnalysis, labelAttachmentError, labelPreviewForm, ratioText, referenceSourceText, referenceVisibilityText, validateLabelAttachment } from "./labelEvaluation.js";
import { analysisQuery, emptyFilters } from "./analysisView.js";

const form = { source_system: "admin-ui", source_kind: "synthetic_expected", source_ref: "waf-dummy-v1", ai_visible: "unknown" };
const file = { name: "synthetic-answers.json", size: 500 };

const comparison = (outcome, reference = {}) => ({ outcome, reference_label: { verdict: "true_positive", source_kind: "reference", ai_visible: false, ...reference } });

test("compact comparisons use the server outcome and retain every evaluation category", () => {
  for (const [outcome, text] of Object.entries({ match: "일치", false_negative: "다름", false_positive: "다름", abstained: "판정 보류", expected_abstention_match: "일치", expected_abstention_mismatch: "다름", pending: "평가 대기", failed: "제외", stub: "제외", unknown_provenance: "제외", input_contaminated: "제외" })) {
    const result = compactEvaluation(comparison(outcome));
    assert.equal(result.text, text);
    assert.equal(result.unlabeled, false);
    assert.equal(result.referenceText, "정탐");
    assert.ok(result.explanation);
  }
  assert.equal(compactEvaluation(comparison("false_negative")).direction, "미탐 방향");
  assert.equal(compactEvaluation(comparison("false_positive")).direction, "과탐 방향");
  assert.equal(compactEvaluation(comparison("expected_abstention_match")).direction, "기대 보류 비교");
});

test("only explicit unlabeled metadata yields a dash, never absent or unknown information", () => {
  assert.equal(compactEvaluation({ outcome: "unlabeled", reference_label: null }).text, "—");
  assert.equal(compactEvaluation({ outcome: "unlabeled" }).unlabeled, true);
  for (const value of [null, undefined, {}, { outcome: null }, { outcome: "toString" }, { outcome: "<img src=synthetic>" }, comparison("unlabeled")]) {
    const result = compactEvaluation(value);
    assert.equal(result.unlabeled, false);
    assert.equal(result.text, "평가 정보 없음");
    assert.match(result.explanation, /새로고침/);
  }
});

test("compact provenance does not turn synthetic, assisted, unknown or self-reported answers into gold", () => {
  assert.equal(compactEvaluation(comparison("match", { source_kind: "synthetic_expected", ai_visible: true })).sourceText, "합성 기대값 · 지원 판정");
  assert.equal(compactEvaluation(comparison("match", { ai_visible: false })).sourceText, "참고 답안 · AI 미열람 (신고)");
  assert.equal(compactEvaluation(comparison("match", { ai_visible: null })).sourceText, "참고 답안 · AI 열람 미확인");
  assert.equal(compactEvaluation(comparison("match", { source_kind: "toString", ai_visible: null })).sourceText, "출처 미기록 · AI 열람 미확인");
  assert.equal(compactEvaluation(comparison("match", { verdict: "toString" })).referenceText, "미기록");
});

test("compact exclusions retain their distinct reasons and never become a security mismatch", () => {
  for (const outcome of ["failed", "stub", "unknown_provenance", "input_contaminated"]) {
    const result = compactEvaluation(comparison(outcome));
    assert.equal(result.excluded, true);
    assert.equal(result.tone, "excluded");
    assert.equal(result.text, "제외");
    assert.equal(result.explanation, evaluationExplanation(comparison(outcome)));
  }
  for (const outcome of ["pending", "abstained", "match", "false_negative", "false_positive"]) assert.equal(compactEvaluation(comparison(outcome)).excluded, false);
});

test("mock execution display is independent of label presence and never inferred from an arbitrary name", () => {
  assert.equal(isMockAnalysis({ model_profile: "stub-no-llm", evaluation: { outcome: "unlabeled" } }), true);
  assert.equal(isMockAnalysis({ result: { agent: { framework: "local-stub" } }, evaluation: { outcome: "unlabeled" } }), true);
  assert.equal(isMockAnalysis({ model_profile: "actual-name-containing-stub" }), false);
  assert.equal(isMockAnalysis({ result: { agent: { llm_called: false } } }), true);
  assert.equal(isMockAnalysis(null), false);
});

test("synthetic expectations and assisted judgments are never presented as measured accuracy", () => {
  assert.equal(evaluationOutcomeText({ outcome: "match", reference_label: { source_kind: "synthetic_expected", ai_visible: false } }), "기대 일치");
  assert.equal(evaluationOutcomeText({ outcome: "match", reference_label: { source_kind: "reference", ai_visible: true } }), "지원 판정 일치");
  assert.equal(evaluationOutcomeText({ outcome: "match", reference_label: { source_kind: "reference", ai_visible: false } }), "맞음 · Label 기준");
  assert.match(evaluationOutcomeText({ outcome: "false_negative", reference_label: { source_kind: "synthetic_expected" } }), /기대 불일치.*미탐 방향/);
  assert.match(evaluationOutcomeText({ outcome: "false_positive", reference_label: { source_kind: "reference" } }), /틀림.*과탐 방향/);
  assert.equal(referenceVisibilityText(null), "AI 열람 여부 미확인");
  assert.equal(referenceVisibilityText(undefined), "AI 열람 여부 미확인");
  assert.match(referenceVisibilityText(false), /입력자 신고/);
  assert.equal(referenceSourceText({ source_kind: "unexpected" }), "출처 미기록");
});

test("incomplete, failed, stub, unknown and contaminated inputs are distinct from wrong predictions", () => {
  for (const outcome of ["unlabeled", "pending", "failed", "stub", "unknown_provenance", "input_contaminated"]) {
    assert.ok(evaluationOutcomes[outcome]);
    assert.ok(!evaluationOutcomeText({ outcome }).includes("틀림"));
    assert.ok(evaluationExplanation({ outcome }));
    assert.notEqual(evaluationTone(outcome), "match");
  }
  assert.equal(evaluationOutcomeText(null), "평가 정보 없음");
  assert.equal(evaluationOutcomeText({ outcome: "<img src=synthetic>" }), "평가 정보 없음");
});

test("abstention is separate from binary error and expected-abstention agreement", () => {
  assert.equal(evaluationOutcomeText({ outcome: "abstained" }), "모델 판단 보류");
  assert.equal(evaluationTone("abstained"), "waiting");
  assert.equal(evaluationOutcomeText({ outcome: "expected_abstention_match" }), "기대 보류 일치");
  assert.equal(evaluationOutcomeText({ outcome: "expected_abstention_mismatch" }), "기대 보류 불일치");
  assert.match(evaluationExplanation({ outcome: "expected_abstention_match" }), /운영 정답이 아닙니다/);
});

test("ratios retain their actual denominators and never fabricate an empty-set percentage", () => {
  assert.equal(ratioText(80, 100), "80.0% (80 / 100건)");
  assert.equal(ratioText(0, 0), "산출 대상 없음 (0 / 0건)");
  assert.equal(ratioText(0, 50), "0.0% (0 / 50건)");
  for (const pair of [[null, 0], [1, 0], [-1, 10], [NaN, 10], [1, 1.5]]) assert.equal(ratioText(...pair), "미기록");
});

test("answer attachment requires explicit source and AI-visible choice without default independence", () => {
  assert.equal(validateLabelAttachment(file, form), "");
  for (const ai_visible of ["unknown", "true", "false"]) assert.equal(validateLabelAttachment(file, { ...form, ai_visible }), "");
  assert.match(validateLabelAttachment(file, { ...form, ai_visible: "" }), /명시적으로/);
  assert.match(validateLabelAttachment(file, { ...form, source_system: " " }), /Source System/);
  assert.match(validateLabelAttachment(file, { ...form, source_ref: " " }), /출처/);
  assert.match(validateLabelAttachment(file, { ...form, source_ref: "synthetic <img>" }), /120자/);
  assert.match(validateLabelAttachment(file, { ...form, source_ref: "x".repeat(121) }), /120자/);
  assert.equal(validateLabelAttachment(file, { ...form, source_ref: "합성 답안_1.0-검토" }), "");
});

test("answer attachment checks file format and exact two-MiB boundary without reading raw logs", () => {
  assert.match(validateLabelAttachment(null, form), /파일/);
  assert.match(validateLabelAttachment({ name: "synthetic.csv", size: 100 }, form), /JSON/);
  assert.equal(validateLabelAttachment({ ...file, size: 2097152 }, form), "");
  assert.match(validateLabelAttachment({ ...file, size: 2097153 }, form), /2 MiB/);
});

test("safe server attachment errors provide bounded corrective guidance", () => {
  assert.match(labelAttachmentError("too_many_label_rows"), /최대 500행/);
  assert.match(labelAttachmentError("analysis_not_found_in_source"), /Source System/);
  assert.match(labelAttachmentError("label_preview_stale"), /이력이 변경/);
  assert.match(labelAttachmentError("inconclusive_requires_synthetic_expected"), /합성 기대값/);
  assert.equal(labelAttachmentError("HTTP 403"), "HTTP 403");
});

test("preview multipart is a dedicated label-only workflow and retains unknown versus false", () => {
  const answer = new File([JSON.stringify([{ event_id: "synthetic-event", expected_verdict: "inconclusive" }])], "synthetic.json", { type: "application/json" });
  const body = labelPreviewForm(answer, { ...form, source_system: " admin-ui ", source_ref: " waf-dummy-v1 ", payload: "NEVER_FORWARD", model_profile: "NEVER_FORWARD" });
  assert.deepEqual([...body.keys()].sort(), ["ai_visible", "file", "source_kind", "source_ref", "source_system"]);
  assert.equal(body.get("source_system"), "admin-ui");
  assert.equal(body.get("source_ref"), "waf-dummy-v1");
  assert.equal(body.get("ai_visible"), "unknown");
  assert.equal(labelPreviewForm(answer, { ...form, ai_visible: "false" }).get("ai_visible"), "false");
});

test("all label filters retain AND scope and are independent of pagination", () => {
  const filters = { ...emptyFilters, analysis_purpose: "test", label_presence: "labeled", reference_label: "false_positive", evaluation_outcome: "abstained", label_source_kind: "reference", label_source_ref: "  synthetic-reference-v1  ", label_ai_visible: "false" };
  const query = analysisQuery(filters, 25, 50);
  assert.equal(query.label_source_ref, "synthetic-reference-v1");
  assert.equal(query.label_ai_visible, "false");
  assert.equal(query.reference_label, "false_positive");
  assert.equal(query.evaluation_outcome, "abstained");
  assert.equal(query.label_presence, "labeled");
  assert.deepEqual({ ...query, offset: 0, limit: 100 }, analysisQuery(filters, 100, 0));
});
