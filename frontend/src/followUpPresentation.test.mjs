import assert from "node:assert/strict";
import test from "node:test";
import { presentFollowUpChecks } from "./followUpPresentation.js";
import { analystFollowUp, analystGuidance } from "./analystView.js";
import { buildAnalysisReport, decodeReportText } from "./analysisReport.js";
import { stepLabel } from "./inspection.js";

const checks = () => [
  { source_ko: "검색 API의 입력 처리 규격", check_ko: "검색어를 문자열 값으로만 사용하는지 SQL 구문에 연결하는지 확인하세요.", why_ko: "정상 검색 데이터와 SQL 조건 변경 시도를 구분하기 위한 확인입니다." },
  { source_ko: "검색 API의 입력 처리 규격", check_ko: "첨부 경로에 상위 디렉터리 이동 제한이 적용되는지 확인하세요.", why_ko: "검색어 처리와 별개인 파일 접근 범위를 확인하기 위한 작업입니다." },
  { source_ko: "검색 API의 입력 처리 규격", check_ko: "검색 입력이 문자열 데이터인지 SQL의 일부로 해석되는지 확인하세요.", why_ko: "검색 입력이 SQL 조건을 변경할 수 있는지 구분하는 데 필요합니다." },
];
const presentation = (tasks = checks()) => ({
  version: "follow-up-editor-v1", status: "completed",
  items: tasks.map((task, index) => ({ check_id: `c${index + 1}`, ...task })),
  groups: [{ member_ids: ["c1", "c3"], representative_id: "c3" }, { member_ids: ["c2"], representative_id: "c2" }],
});

test("only recorded representatives are shown, distinct checks and original records survive", () => {
  const tasks = checks(), saved = presentation(tasks);
  const detail = { status: "completed", result: { verdict: "inconclusive", analyst_guidance: { checks: tasks }, follow_up_presentation: saved } };
  const before = structuredClone(detail);
  assert.deepEqual(presentFollowUpChecks(tasks, saved), [tasks[2], tasks[1]]);
  assert.deepEqual(analystGuidance(detail).checks, [tasks[2], tasks[1]]);
  assert.deepEqual(analystFollowUp(detail).checks, [tasks[2], tasks[1]]);
  const markdown = decodeReportText(buildAnalysisReport(detail));
  assert.ok(!markdown.includes(tasks[0].check_ko));
  assert.ok(markdown.includes(tasks[1].check_ko));
  assert.ok(markdown.includes(tasks[2].check_ko));
  assert.deepEqual(detail, before);
  saved.groups.reverse();
  assert.deepEqual(presentFollowUpChecks(tasks, saved), [tasks[2], tasks[1]]);
});

test("invalid coverage, changed originals and cross-source mapping fall back to original checks", () => {
  for (const corrupt of [
    (tasks, saved) => saved.groups.pop(),
    (tasks, saved) => { saved.groups = []; },
    (tasks, saved) => saved.groups[0].member_ids.push("c1"),
    (tasks, saved) => saved.groups[0].member_ids.push("c4"),
    (tasks, saved) => { saved.groups[0].representative_id = "c2"; },
    (tasks, saved) => { saved.groups[0].explanation = "invented"; },
    (tasks, saved) => saved.groups.push(saved.groups[1]),
    (tasks, saved) => { saved.items[0].why_ko = "modified"; },
    (tasks, saved) => { tasks[2].source_ko = "다른 서버의 기록"; saved.items[2].source_ko = tasks[2].source_ko; },
    (tasks, saved) => { saved.items.reverse(); },
    (tasks, saved) => { saved.items[0].extra = "unrecognized"; },
    (tasks, saved) => { saved.status = "fallback"; },
    (tasks, saved) => { saved.status = "skipped"; },
    (tasks, saved) => { saved.version = "unknown"; },
    (tasks, saved) => { saved.groups = null; },
    (tasks, saved) => { tasks[0].check_ko = "a".repeat(801); saved.items[0].check_ko = tasks[0].check_ko; },
  ]) {
    const tasks = checks(), saved = presentation(tasks);
    corrupt(tasks, saved);
    assert.strictEqual(presentFollowUpChecks(tasks, saved), tasks);
  }
});

test("absent mapping preserves legacy display and does not resurrect decisive empty checks", () => {
  const tasks = checks();
  assert.strictEqual(presentFollowUpChecks(tasks, undefined), tasks);
  const detail = { status: "completed", result: { verdict: "true_positive", analyst_guidance: { checks: [] }, analyst_checks: tasks,
    follow_up_presentation: presentation(tasks), recommended_checks: ["과거 권고"] } };
  assert.deepEqual(analystGuidance(detail).checks, []);
  assert.equal(analystFollowUp(detail).visible, false);
  assert.deepEqual(presentFollowUpChecks([], { ...presentation([]), groups: [] }), []);
});

test("editor history labels distinguish expanded and pinned legacy stages", () => {
  assert.equal(stepLabel("llm_evidence_editor", { editor_version: "result-editor-v2" }), "근거·확인사항 정리");
  assert.equal(stepLabel("llm_evidence_editor", { editor_version: "evidence-editor-v1" }), "근거 정리");
  assert.equal(stepLabel("llm_evidence_editor"), "근거 정리");
});
