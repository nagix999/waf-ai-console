import assert from "node:assert/strict";
import test from "node:test";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";
import { runInNewContext } from "node:vm";
import { buildSync } from "esbuild";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { analysisElapsedTime, analysisQuery, analysisReceivedAt, analysisRowState, appliedFilterTags, emptyFilters, initialListState, removeAppliedFilter } from "./analysisView.js";

const bundled = buildSync({
  entryPoints: [fileURLToPath(new URL("./App.jsx", import.meta.url))],
  bundle: true, write: false, platform: "node", format: "cjs", packages: "external",
  jsx: "automatic", loader: { ".css": "empty", ".md": "text" }, logLevel: "silent",
}).outputFiles[0].text;
const module = { exports: {} };
runInNewContext(bundled, { module, exports: module.exports, require: createRequire(import.meta.url), process, URL, Date });
const { AnalysisTable, AnalysisList } = module.exports;
const fixture = (id, status = "completed") => ({
  id, event_id: `synthetic-${id}`, signature: "Synthetic detection", company_name: "합성 회사", analysis_purpose: "test",
  src_ip: "192.0.2.10", dest_ip: "198.51.100.10", src_port: 0, dest_port: 443,
  waf_action: "D", waf_vendor: "NEVER_SHOW_WAF_VENDOR_COLUMN", status, verdict: "inconclusive", severity: "UNKNOWN",
  summary_ko: "합성 요청에서 문자열을 확인했습니다. 실제 처리 방식은 확인되지 않았습니다.",
  total_elapsed_ms: 1000, created_at: "2026-09-07T00:00:00Z", evaluation: { outcome: "unlabeled", reference_label: null },
});
const table = (items, extra = {}) => renderToStaticMarkup(createElement(AnalysisTable, { items, onOpen() {}, ...extra }));

test("model validation is a named ingest-channel filter while the source-scoped dataset link stays intact", () => {
  const state = initialListState("test"); state.advanced = true;
  state.applied = { ...state.applied, ingest_channel: "model_validation", source_system: "waf-internal-model-test-synthetic" };
  state.draft = { ...state.applied };
  const html = renderToStaticMarkup(createElement(AnalysisList, { state, setState() {}, onOpen() {}, onUnauthorized() {} }));
  assert.match(html, /<option value="model_validation"[^>]*>모델 검증<\/option>/);
  assert.match(html, /모델 검증/);
  assert.equal(analysisQuery(state.applied, 25, 0).ingest_channel, "model_validation");
  assert.equal(analysisQuery(state.applied, 25, 0).source_system, "waf-internal-model-test-synthetic");
});

test("unified list has exactly six stable columns without a WAF column or alternate evaluation mode", () => {
  const html = table([fixture("complete")]);
  assert.equal((html.match(/<th\b/g) || []).length, 6);
  const headers = [...html.matchAll(/<th\b[^>]*>([^<]+)<\/th>/g)].map(match => match[1]);
  assert.deepEqual(headers, ["판정 / 심각도", "이벤트 / 요약", "회사 / 연결", "참고 답안 비교", "전체 소요 시간", "접수 시각"]);
  assert.doesNotMatch(html, /NEVER_SHOW_WAF_VENDOR_COLUMN|Deny|status-completed|분석 보기|평가 보기/);
  assert.match(html, /class="sr-only">분석 완료/);
  assert.match(html, /unified-event-title[^>]*>Synthetic detection/);
  assert.match(html, /unified-event-id[^>]*><span[^>]*>synthetic-complete/);
  assert.match(table([]), /colSpan="6"/i);
});

test("row presentation uses execution status only and never equates a completed hold or wrong answer with failure", () => {
  const items = [fixture("waiting", "pending"), fixture("running", "processing"), fixture("failed", "failed"), fixture("hold")];
  items[3].evaluation = { outcome: "false_positive", reference_label: { verdict: "false_positive", source_kind: "reference", ai_visible: false } };
  const html = table(items);
  for (const status of ["pending", "processing", "failed", "completed"]) assert.match(html, new RegExp(`class="analysis-row-${status}"`));
  assert.match(html, /분석 대기/);
  assert.match(html, /분석 중/);
  assert.match(html, /분석 실패/);
  assert.deepEqual(analysisRowState("completed"), { className: "analysis-row-completed", label: "분석 완료" });
  assert.deepEqual(analysisRowState("inconclusive"), { className: "analysis-row-unknown", label: "처리 상태 미확인" });
  assert.equal(analysisRowState('<img src=x>').className, "analysis-row-unknown");
});

test("received time has separate date/time lines and ongoing duration is explicitly elapsed rather than final", () => {
  const received = analysisReceivedAt("2026-09-07T00:00:00");
  assert.equal(received.dateTime, "2026-09-07T00:00:00.000Z");
  assert.match(received.time, /^\d{2}:\d{2}:\d{2}$/);
  assert.equal(analysisReceivedAt("invalid").date, "—");
  assert.equal(analysisElapsedTime({ status: "processing", total_elapsed_ms: 1000 }), "1.00초 경과");
  assert.equal(analysisElapsedTime({ status: "pending", total_elapsed_ms: 0 }), "0 ms 경과");
  assert.equal(analysisElapsedTime({ status: "processing", total_elapsed_ms: null }), "진행 중");
  assert.equal(analysisElapsedTime({ status: "completed", total_elapsed_ms: 1000 }), "1.00초");
  assert.equal(analysisElapsedTime({ status: "failed", total_elapsed_ms: null }), "측정 전 데이터");
  assert.match(table([fixture("date")]), /<time class="analysis-received-at"[^>]+><span>[^<]+<\/span><small>[^<]+<\/small><\/time>/);
});

test("purpose badges appear only for mixed scopes while stub and input truncation remain visible", () => {
  const item = fixture("retained");
  item.model_profile = "stub-no-llm"; item.input_truncated = true;
  assert.match(table([item]), /purpose-test/);
  for (const purpose of ["test", "production", "legacy_unknown"]) {
    const html = table([item], { purpose });
    assert.doesNotMatch(html, /purpose-test/);
    assert.match(html, /모의 판정 · LLM 아님/);
    assert.match(html, /입력 일부 생략/);
  }
  const before = JSON.stringify(item);
  table([item]);
  assert.equal(JSON.stringify(item), before);
});

test("applied chips reflect all active filters including false, zero and hidden advanced filters", () => {
  const filters = { ...emptyFilters, search_field: "src_ip", q: " 192.0.2.10 ", input_truncated: false, confidence_min: 0,
    label_presence: "labeled", label_source_ref: " synthetic-v1 ", label_ai_visible: "unknown", waf_action: "D", injected: "NEVER_SHOW" };
  const tags = appliedFilterTags(filters, { label_presence: { labeled: "있음" } });
  assert.deepEqual(tags.find(tag => tag.key === "q"), { key: "q", label: "Source IP (정확히)", value: "192.0.2.10" });
  assert.equal(tags.find(tag => tag.key === "label_presence").value, "있음");
  assert.equal(tags.find(tag => tag.key === "input_truncated").value, "false");
  assert.equal(tags.find(tag => tag.key === "confidence_min").value, "0");
  assert.equal(tags.find(tag => tag.key === "label_source_ref").value, "synthetic-v1");
  assert.ok(tags.some(tag => tag.key === "waf_action"));
  assert.ok(!tags.some(tag => ["injected", "search_field"].includes(tag.key)));
  assert.deepEqual(appliedFilterTags({ ...emptyFilters, q: "   " }), []);
});

test("Asia/Seoul local date filters and zoned dashboard filters show the same instants as their API queries", { concurrency: false }, () => {
  const previousTimezone = process.env.TZ;
  process.env.TZ = "Asia/Seoul";
  try {
    const local = { ...emptyFilters, created_from: "2026-09-07T09:00:00.123", created_to: "2026-09-07T10:30:00.456" };
    const zoned = { ...emptyFilters, created_from: "2026-09-07T00:00:00.123Z", created_to: "2026-09-07T01:30:00.456Z" };
    const output = {
      localQuery: analysisQuery(local, 25, 0), zonedQuery: analysisQuery(zoned, 25, 0),
      localTags: appliedFilterTags(local), zonedTags: appliedFilterTags(zoned),
      expected: [new Date("2026-09-07T00:00:00.123Z").toLocaleString("ko-KR"), new Date("2026-09-07T01:30:00.456Z").toLocaleString("ko-KR")],
    };
    assert.deepEqual(output.localQuery, output.zonedQuery);
    assert.equal(output.localQuery.created_from, "2026-09-07T00:00:00.123Z");
    assert.equal(output.localQuery.created_to, "2026-09-07T01:30:00.456Z");
    assert.deepEqual(output.localTags.map(tag => tag.value), output.expected);
    assert.deepEqual(output.zonedTags.map(tag => tag.value), output.expected);
    assert.equal(analysisReceivedAt("2026-09-07T00:00:00.123").dateTime, "2026-09-07T00:00:00.123Z", "DB timestamps without a zone retain their UTC interpretation");
  } finally {
    if (previousTimezone === undefined) delete process.env.TZ;
    else process.env.TZ = previousTimezone;
  }
});

test("long company and connection values remain complete text and titles inside a bounded cell", () => {
  const item = fixture("long-company");
  item.company_name = "합성회사이름".repeat(50);
  item.src_ip = "2001:db8:1234:5678:abcd:ef12:3456:7890";
  const before = JSON.stringify(item);
  const html = table([item]);
  assert.match(html, /class="table-meta unified-company"/);
  assert.ok(html.includes(`<strong title="${item.company_name}">${item.company_name}</strong>`));
  assert.ok(html.includes(`title="${item.src_ip} : 0"`));
  assert.equal(JSON.stringify(item), before);
});

test("removing one applied filter preserves unrelated draft edits, scope and AND conditions while resetting pagination", () => {
  const state = initialListState("test");
  state.offset = 50;
  state.applied = { ...state.applied, q: "synthetic", search_field: "signature", label_presence: "labeled", waf_action: "D" };
  state.draft = { ...state.applied, company_name: "unsaved edit" };
  const before = JSON.stringify(state);
  const next = removeAppliedFilter(state, "label_presence");
  assert.equal(next.offset, 0);
  assert.equal(next.draft.company_name, "unsaved edit");
  assert.equal(next.applied.company_name, "");
  assert.deepEqual(analysisQuery(next.applied, 25, 0), { limit: 25, offset: 0, analysis_purpose: "test", q: "synthetic", search_field: "signature", waf_action: "D" });
  const clearedSearch = removeAppliedFilter(next, "q");
  assert.equal(clearedSearch.applied.search_field, "all");
  assert.equal(clearedSearch.draft.q, "");
  assert.equal(JSON.stringify(state), before);
  assert.equal(removeAppliedFilter(state, "__proto__"), state);
});

test("actual list always exposes reference filters, an attachment action and an open quality summary without view controls", () => {
  const state = initialListState("test");
  state.applied.label_source_ref = "synthetic-reference";
  state.draft = { ...state.applied };
  const html = renderToStaticMarkup(createElement(AnalysisList, { state, setState() {}, onOpen() {}, onUnauthorized() {} }));
  assert.match(html, /name="label_presence"/);
  assert.match(html, /name="evaluation_outcome"/);
  assert.match(html, /aria-label="적용된 검색 조건"/);
  assert.match(html, /답안 출처 \/ 버전: synthetic-reference 조건 해제/);
  assert.match(html, /aria-controls="analysis-label-attachment"/);
  assert.match(html, /<details class="panel analysis-evaluation-overview" open="">/);
  assert.match(html, /판정 평가 지표/);
  assert.doesNotMatch(html, /목록 표시 방식|분석 보기|평가 보기/);
});

test("unified list keeps hostile summary, title and Event ID inert and does not read payloads", () => {
  const item = fixture("text-only");
  const hostile = "<img src=https://synthetic.invalid/x> [link](javascript:synthetic)";
  item.signature = hostile; item.summary_ko = hostile; item.event_id = hostile;
  Object.defineProperty(item, "payload", { get() { throw new Error("Payload must not be read"); } });
  const html = table([item]);
  assert.match(html, /&lt;img/);
  assert.doesNotMatch(html, /<img\b|<a\b/);
});
