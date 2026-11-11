import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import test from "node:test";
import { assessmentView } from "./assessmentData.js";
import { buildAnalysisReport, decodeReportText } from "./analysisReport.js";
import { buildReportDocument } from "./reportDocument.js";
import { buildEvidenceDisplayPlan, evidenceDisplayView, evidenceSourceHash, sha256Text, validateEvidenceDisplayPlan } from "./evidenceDisplayPlan.js";

const cases = JSON.parse(readFileSync(new URL("../../backend/tests/fixtures/evidence_display_plan.json", import.meta.url)));
const copy = value => structuredClone(value);
const sidecar = item => ({ version: "evidence-display-view-v1", status: item.status,
  plan: item.expected, relations: item.relations, metadata: {} });

test("content fingerprint matches standard SHA-256 for Unicode, escapes, empty and block boundaries", () => {
  for (const value of ["", "abc", "가🙂\r\n\\\"", ...[55, 56, 63, 64, 65, 127, 128, 1024, 25000].map(size => "한a".repeat(size))]) {
    assert.equal(sha256Text(value), createHash("sha256").update(value, "utf8").digest("hex"));
  }
});

for (const item of cases) test(`shared Python/JavaScript display fixture: ${item.name}`, () => {
  const before = JSON.stringify(item);
  assert.equal(evidenceSourceHash(item.result, item.relations), item.expected.source_hash);
  assert.equal(validateEvidenceDisplayPlan(item.result, item.expected, item.relations), true);
  const view = evidenceDisplayView(item.result, sidecar(item));
  assert.deepEqual(view.plan, item.expected);
  assert.deepEqual(view.groups.map(group => group.number), item.expected.groups.map(group => group.number));
  assert.deepEqual(view.items.map(source => source.evidence_id), item.result.evidence.map(source => source.evidence_id));
  assert.equal(JSON.stringify(item), before);
  if (!item.model_groups) assert.deepEqual(buildEvidenceDisplayPlan(item.result, item.result.evidence_presentation,
    item.relations, { status: item.status, reason: item.reason ?? null }), item.expected);
});

const damages = [
  plan => { plan.source_hash = "f".repeat(64); },
  plan => { plan.extra = "untrusted"; },
  plan => { plan.groups[0].extra = true; },
  plan => { plan.groups[0].representative_id = "e999"; },
  plan => { plan.groups[0].member_ids.push("e999"); },
  plan => { plan.groups[0].member_ids.push(plan.groups[0].member_ids[0]); },
  plan => { plan.groups.pop(); },
  plan => { plan.groups[0].number += 1; },
  plan => { plan.groups[0].supports = "normal_request"; },
  plan => { plan.groups[0].related_ids = []; },
  plan => { plan.groups[0].default_visible = false; },
  plan => { plan.groups[0].visibility_reasons = ["remaining_evidence"]; },
  plan => { plan.groups[0].visibility_reasons = ["invented_reason"]; },
  plan => { plan.metrics.merged_count += 1; },
  plan => { plan.metrics.untrusted = true; },
];
for (const [index, damage] of damages.entries()) test(`invalid display plan ${index} restores every original source`, () => {
  const item = copy(cases[0]), plan = copy(item.expected); damage(plan);
  assert.equal(validateEvidenceDisplayPlan(item.result, plan, item.relations), false);
  const view = evidenceDisplayView(item.result, { ...sidecar(item), plan });
  assert.equal(view.invalid, true);
  assert.equal(view.groups.length, item.result.evidence.length);
  assert.ok(view.groups.every(group => group.default_visible && group.member_ids.length === 1));
  assert.deepEqual(view.groups.map(group => group.evidence_id), item.result.evidence.map(source => source.evidence_id));
});

test("valid grouping can join different source fragments without changing their individual classification", () => {
  const item = cases[0], view = evidenceDisplayView(item.result, sidecar(item));
  const merged = view.groups.find(group => group.member_ids.length > 1);
  assert.ok(merged);
  assert.equal(new Set(merged.member_sources.map(source => source.excerpt)).size, 2);
  assert.equal(new Set(merged.member_sources.map(source => source.supports)).size, 1);
  assert.equal(merged.interpretation_ko, item.result.evidence.find(source => source.evidence_id === merged.representative_id).interpretation_ko);
});

test("Editor and relation composition never resurrects grouped duplicates", () => {
  const item = cases[2], view = evidenceDisplayView(item.result, sidecar(item));
  assert.equal(view.groups.length, 2);
  assert.deepEqual(view.groups[0].member_ids, ["e1", "e2"]);
  assert.equal(view.groups[0].representative_id, "e2");
  assert.deepEqual(view.groups[0].related_ids, ["e3"]);
  assert.equal(view.groups.filter(group => group.supports === "attack_request").length, 1);
});

test("failed organization exposes all original explanations rather than declaring a successful no-op", () => {
  const item = cases[1], view = evidenceDisplayView(item.result, sidecar(item));
  assert.equal(view.plan.status, "fallback");
  assert.equal(view.core.length, item.result.evidence.length);
  assert.equal(view.additional.length, 0);
  assert.ok(view.groups.every(group => group.visibility_reasons.join() === "fallback_original"));
});

test("failed job without a saved map cannot silently reuse a legacy reduction", () => {
  const item = cases[2], view = evidenceDisplayView(item.result, { ...sidecar(item), status: "fallback", plan: null });
  assert.equal(view.plan.status, "fallback");
  assert.equal(view.groups.length, item.result.evidence.length);
  assert.ok(view.groups.every(group => group.default_visible && group.member_ids.length === 1));
});

test("pending and completed without reduction have distinct truthful report status", () => {
  const item = cases[2], plan = buildEvidenceDisplayPlan(item.result, null, item.relations);
  const pending = decodeReportText(buildAnalysisReport({ status: "completed", result: item.result,
    evidence_display: { ...sidecar(item), status: "pending", plan: null } }));
  assert.match(pending, /근거 정리 중입니다/);
  const completed = decodeReportText(buildAnalysisReport({ status: "completed", result: item.result,
    evidence_display: { ...sidecar(item), status: "completed", plan } }));
  assert.match(completed, /검토 완료 — 합친 근거가 없습니다/);
});

test("source changes invalidate a previously saved display map", () => {
  for (const change of [
    item => { item.result.evidence[0].excerpt += "new"; },
    item => { item.result.evidence[0].interpretation_ko += "new"; },
    item => { item.result.decision_issues[0].point_ko += "new"; },
    item => { item.result.verdict = "inconclusive"; },
    item => { item.relations.groups.reverse(); },
  ]) {
    const item = copy(cases[0]); change(item);
    assert.equal(validateEvidenceDisplayPlan(item.result, item.expected, item.relations), false);
  }
});

test("conditions resolve merged member IDs to the same stable representative number", () => {
  const item = cases[2], result = copy(item.result);
  // Adding a new condition changes the source hash and requires a fresh plan.
  result.decision_issues = [{ point_ko: "확인할 합성 조건", evidence_ids: ["e1", "e2"], missing_condition_ko: "입력 사용 방식" }];
  const plan = buildEvidenceDisplayPlan(result, result.evidence_presentation, item.relations);
  const display = { version: "evidence-display-view-v1", status: "disabled", plan, relations: item.relations };
  const view = assessmentView(result, { display });
  assert.deepEqual(view.issues[0].evidence_numbers, [2]);
  assert.deepEqual(assessmentView(result, { preserveEvidence: true, display }).evidence.map(source => source.number), [1, 2, 3]);
});

test("condition-isolated display groups are not merged again by the assessment adapter", () => {
  const result = copy(cases[2].result);
  result.evidence[1] = { ...copy(result.evidence[0]), evidence_id: "e2" };
  result.decision_issues = [{ point_ko: "첫 근거에만 연결한 합성 조건", evidence_ids: ["e1"], missing_condition_ko: "입력 사용 방식" }];
  delete result.evidence_presentation;
  const display = { version: "evidence-display-view-v1", status: "disabled", relations: null, plan: null };
  const view = assessmentView(result, { display });
  assert.equal(view.display.groups.length, 3);
  assert.deepEqual(view.evidence.map(item => item.number), [1, 2, 3]);
  assert.deepEqual(view.issues[0].evidence_numbers, [1]);
});

test("web report and offline PDF document preserve all display groups, ordering and source excerpts", () => {
  const item = cases[0], detail = { status: "completed", result: item.result, evidence_display: sidecar(item) };
  const before = JSON.stringify(detail), report = decodeReportText(buildAnalysisReport(detail));
  assert.match(report, /핵심 근거/); assert.match(report, /나머지 근거/);
  const positions = item.expected.groups.map(group => report.indexOf(`#### 근거 ${group.number} —`));
  assert.ok(positions.every(position => position >= 0));
  assert.deepEqual([...positions].sort((a, b) => a - b), positions);
  for (const source of item.result.evidence) assert.ok(report.includes(source.excerpt));
  assert.ok(buildReportDocument({ detail }).sections.length > 0);
  assert.equal(JSON.stringify(detail), before);
});
