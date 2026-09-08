import assert from "node:assert/strict";
import test from "node:test";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";
import { runInNewContext } from "node:vm";
import { buildSync } from "esbuild";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { api } from "./api.js";
import { evaluationExplanation } from "./labelEvaluation.js";

const bundled = buildSync({
  entryPoints: [fileURLToPath(new URL("./ReferenceLabels.jsx", import.meta.url))],
  bundle: true, write: false, platform: "node", format: "cjs", packages: "external",
  jsx: "automatic", loader: { ".css": "empty" }, logLevel: "silent",
}).outputFiles[0].text;
const module = { exports: {} };
runInNewContext(bundled, { module, exports: module.exports, require: createRequire(import.meta.url), process, Date });
const { CompactReferenceComparison, CompactEvaluationDetail, EvaluationDetail, LabelHistoryRows, LabelAttachment, EvaluationSummary, initialLabelAttachmentForm } = module.exports;
const render = (Component, props) => renderToStaticMarkup(createElement(Component, props));
const fixture = (outcome = "match", changes = {}) => ({
  status: "completed", verdict: "true_positive",
  evaluation: { outcome, reference_label: { id: "synthetic-label", revision: 1, verdict: "true_positive", source_kind: "synthetic_expected", source_ref: "synthetic-v1", ai_visible: null, created_at: "2026-09-07T00:00:00Z", ...changes } },
});

test("compact list comparison renders one criterion with a short status and explicit provenance", () => {
  const html = render(CompactReferenceComparison, { evaluation: fixture().evaluation });
  assert.match(html, /기준 <strong>정탐<\/strong>/);
  assert.match(html, />일치<\/span>/);
  assert.match(html, /기대 답안 · AI 열람 미확인/);
  assert.doesNotMatch(html, /맞음|틀림|정확도|>AI 최종|답안 비교 설명/);
});

test("ordinary comparison states do not repeat obvious help in list and detail views", () => {
  for (const outcome of ["match", "false_negative", "false_positive", "pending"]) {
    const detail = fixture(outcome);
    for (const html of [render(CompactReferenceComparison, { evaluation: detail.evaluation }), render(CompactEvaluationDetail, { detail, history: [] }), render(EvaluationDetail, { detail, history: [] })]) {
      assert.doesNotMatch(html, /답안 비교 설명|평가 제외 사유 설명|help-trigger/);
    }
  }
});

test("abstentions and unavailable metadata retain contextual comparison help", () => {
  for (const outcome of ["abstained", "expected_abstention_match", "expected_abstention_mismatch", "unknown-outcome"]) {
    const detail = fixture(outcome);
    for (const html of [render(CompactReferenceComparison, { evaluation: detail.evaluation }), render(CompactEvaluationDetail, { detail, history: [] }), render(EvaluationDetail, { detail, history: [] })]) {
      assert.match(html, /aria-label="답안 비교 설명"/);
    }
  }
  assert.match(render(CompactReferenceComparison, {}), /aria-label="답안 비교 설명"/);
});

test("unlabeled list entries alone use a dash and missing evaluation remains visible", () => {
  const unlabeled = render(CompactReferenceComparison, { evaluation: { outcome: "unlabeled", reference_label: null } });
  assert.match(unlabeled, /aria-label="참고 답안 없음">—/);
  assert.doesNotMatch(unlabeled, /evaluation-badge/);
  for (const evaluation of [undefined, null, {}]) {
    const html = render(CompactReferenceComparison, { evaluation });
    assert.match(html, /평가 정보 없음/);
    assert.doesNotMatch(html, /참고 답안 없음/);
  }
});

test("exclusion reasons use help, remain distinct, and are not guessed from equal displayed verdicts", () => {
  const reasons = { failed: /실행 실패/, stub: /실제 LLM 판정이 아니므로/, unknown_provenance: /실제 모델 실행 여부/, input_contaminated: /답안 관련 키/ };
  for (const [outcome, reason] of Object.entries(reasons)) {
    const detail = fixture(outcome);
    const html = render(CompactReferenceComparison, { evaluation: detail.evaluation, detail });
    assert.match(html, />제외<\/span>/);
    assert.match(html, /aria-label="평가 제외 사유 설명"/);
    assert.match(evaluationExplanation(detail.evaluation), reason);
    assert.doesNotMatch(html, /<details|<summary/);
    assert.doesNotMatch(html, />일치<\/span>|>다름<\/span>|open=""/);
  }
});

test("binary direction, abstention and expected abstention stay distinguishable", () => {
  for (const [outcome, label] of [["false_negative", "미탐 방향"], ["false_positive", "과탐 방향"], ["abstained", "판정 보류"], ["expected_abstention_match", "기대 보류 비교"], ["expected_abstention_mismatch", "기대 보류 비교"], ["pending", "평가 대기"]]) {
    const html = render(CompactReferenceComparison, { evaluation: fixture(outcome).evaluation });
    assert.ok(html.includes(label));
  }
});

test("detail comparison does not repeat the AI verdict or read intermediate results", () => {
  const detail = fixture("false_negative");
  Object.defineProperty(detail, "result", { get() { throw new Error("Do not read Agent results for reference comparison"); } });
  Object.defineProperty(detail, "verdict", { get() { throw new Error("Do not repeat the main verdict summary"); } });
  const html = render(CompactEvaluationDetail, { detail, history: [] });
  assert.match(html, /참고 답안/);
  assert.match(html, /<strong>정탐<\/strong>/);
  assert.match(html, />다름<\/span>/);
  assert.match(html, /미탐 방향/);
  assert.match(html, />연결 이력<\/button>/);
  assert.doesNotMatch(html, /aria-label="답안 비교 설명"/);
  assert.doesNotMatch(html, /<details|synthetic-v1/);
  assert.doesNotMatch(html.split("<details")[0], /AI 최종|↔/);
  assert.doesNotMatch(html, /open=""/);
});

test("detail hides only explicit unlabeled state, never missing metadata or history failure", () => {
  const detail = { evaluation: { outcome: "unlabeled", reference_label: null } };
  assert.equal(render(CompactEvaluationDetail, { detail, history: [] }), "");
  assert.equal(render(CompactEvaluationDetail, { detail, history: null }), "");
  const failed = render(CompactEvaluationDetail, { detail, history: null, historyError: "synthetic failure" });
  assert.match(failed, /role="alert">연결 이력 조회 실패/);
  assert.doesNotMatch(failed, /synthetic failure/);
  for (const detail of [{}, { evaluation: null }]) assert.match(render(CompactEvaluationDetail, { detail, history: [] }), /평가 정보 없음/);
});

test("history preserves all versions while identities stay behind explicit row detail", () => {
  const detail = fixture("match", { source_ref: "<img src='synthetic.invalid'>", ai_visible: true });
  const history = [1, 2].map((revision) => ({ ...detail.evaluation.reference_label, id: `synthetic-${revision}`, revision, created_by: "<script>synthetic</script>" }));
  const before = JSON.stringify({ detail, history });
  const html = render(LabelHistoryRows, { history });
  assert.match(html, /지원 판정/);
  assert.match(html, /<td>1<\/td>/);
  assert.match(html, /<td>2<\/td>/);
  assert.match(html, /aria-label="답안 버전 1 상세"/);
  assert.match(html, /aria-label="답안 버전 2 상세"/);
  assert.doesNotMatch(html, /synthetic-1|synthetic.invalid|&lt;script/);
  assert.doesNotMatch(html, /<img\b|<script\b|href=/);
  assert.equal(JSON.stringify({ detail, history }), before);
});

test("history loading, malformed data, empty history and refresh failure are distinct", () => {
  for (const [history, pattern] of [[null, /이력을 불러오는 중/], [undefined, /이력을 불러오는 중/], [{}, /이력 정보를 확인할 수 없습니다/], [[], /연결된 답안 이력이 없습니다/]]) {
    assert.match(render(LabelHistoryRows, { history }), pattern);
  }
  const html = render(LabelHistoryRows, { history: [], historyError: "<script>UNSAFE_ERROR_MESSAGE</script>" });
  assert.match(html, /마지막 조회 결과/);
  assert.doesNotMatch(html, /연결된 답안 이력이 없습니다|UNSAFE_ERROR_MESSAGE|script/);
});

test("controlled attachment can be opened by its parent without changing the dedicated form", () => {
  const closed = render(LabelAttachment, { controlledOpen: false, onOpenChange() {}, onAttached() {} });
  const opened = render(LabelAttachment, { controlledOpen: true, onOpenChange() {}, onAttached() {} });
  const standalone = render(LabelAttachment, { onAttached() {} });
  assert.equal(closed, "", "controlled dialog content is not part of server-rendered page");
  assert.equal(opened, "", "native dialog mounts only in a browser");
  assert.match(standalone, />참고 답안 연결<\/button>/);
  for (const html of [closed, opened, standalone]) {
    assert.doesNotMatch(html, /<details|<input|답안 연결 확정/);
  }
  assert.deepEqual(JSON.parse(JSON.stringify(initialLabelAttachmentForm())), { source_system: "", source_kind: "synthetic_expected", source_ref: "", ai_visible: "" });
  assert.equal(initialLabelAttachmentForm("exact-server-source").source_system, "exact-server-source");
  for (const source of [null, 1, {}, ["guessed"]]) assert.equal(initialLabelAttachmentForm(source).source_system, "");
});

test("existing summary remains an unfolded body for the parent-owned collapsed aggregate", () => {
  const summary = { total: 30, labeled: 3, evaluable: 2, matches: 1, outcomes: { unlabeled: 27, pending: 1, match: 1, false_negative: 1 }, source_groups: [] };
  const html = render(EvaluationSummary, { summary });
  assert.match(html, /전체 30건 · 현재 페이지 한정 아님/);
  assert.match(html, />제외·보류 내역<\/button>/);
  assert.match(html, /aria-label="평가 기준 설명"/);
  assert.doesNotMatch(html, /<details|평가 범위 설명/);
});

test("reference attachment retains preview then explicit confirmation endpoints without analysis calls", async () => {
  const savedFetch = globalThis.fetch;
  const calls = [];
  globalThis.fetch = async (path, options) => {
    calls.push({ path, options });
    return { status: 200, ok: true, json: async () => ({ preview_token: "synthetic-preview-token" }) };
  };
  try {
    const body = new FormData();
    body.append("source_system", "admin-ui");
    const preview = await api.previewEvaluationLabels(body);
    assert.equal(calls.length, 1);
    assert.equal(calls[0].path, "/api/v1/evaluation-labels/preview");
    await api.confirmEvaluationLabels(preview.preview_token);
    assert.equal(calls.length, 2);
    assert.equal(calls[1].path, "/api/v1/evaluation-labels/confirm");
    assert.deepEqual(JSON.parse(calls[1].options.body), { preview_token: "synthetic-preview-token" });
    assert.ok(calls.every(({ path, options }) => path.includes("/evaluation-labels/") && options.method === "POST"));
  } finally { globalThis.fetch = savedFetch; }
});
