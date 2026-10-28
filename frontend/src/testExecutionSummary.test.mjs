import assert from "node:assert/strict";
import test from "node:test";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";
import { readFileSync } from "node:fs";
import { runInNewContext } from "node:vm";
import { buildSync } from "esbuild";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { executionCount, executionSummary, parserWarningLabel, verifierReasonLabel } from "./testExecutionSummary.js";

function fixture() {
  return { version: "test-execution-summary-v1", cases: 20,
    parser: { success: 8, partial: 7, failed: 1, unrecorded: 4, warnings_recorded: 10, warnings_unrecorded: 10,
      warnings: [{ code: "request_protocol_not_recognized", cases: 7 }, { code: "header_line_not_recognized", cases: 2 }] },
    verifier: { policy_recorded: 14, executed: 10, not_executed: 3, awaiting_execution: 1, unrecorded: 4, not_applicable: 2, reasons_unrecorded: 0,
      parser_triggered: 8, parser_only: 3, parser_with_other: 5,
      reasons: [{ code: "parser_incomplete", cases: 8, only_cases: 3, with_other_cases: 5 },
        { code: "primary_inconclusive", cases: 7, only_cases: 2, with_other_cases: 5 }] },
  };
}

const bundled = buildSync({ entryPoints: [fileURLToPath(new URL("./TestExecutionSummary.jsx", import.meta.url))], bundle: true, write: false, platform: "node", format: "cjs", packages: "external", jsx: "automatic", loader: { ".css": "empty", ".md": "text" }, logLevel: "silent" }).outputFiles[0].text;
const module = { exports: {} };
runInNewContext(bundled, { module, exports: module.exports, require: createRequire(import.meta.url), process, Date, URL });
const render = (summary, extra = {}) => renderToStaticMarkup(createElement(module.exports.default, { summary, ...extra }));

test("execution counts distinguish zero from absent, coercible or invalid values", () => {
  assert.equal(executionCount(0), 0); assert.equal(executionCount(2000), 2000);
  for (const value of [null, undefined, false, true, "0", "10", -1, 1.5, NaN, Infinity, 2 ** 53, {}, []]) assert.equal(executionCount(value), null);
});

test("summary normalization preserves counters, awaiting-stage boundary and original response", () => {
  const source = fixture(), before = JSON.stringify(source), result = executionSummary(source);
  assert.equal(result.cases, 20); assert.equal(result.verifier.executed, 10); assert.equal(result.verifier.parser_only, 3);
  assert.equal(result.verifier.awaiting_execution, 1); assert.equal(result.verifier.not_applicable, 2);
  assert.equal(result.parser.warnings_recorded, 10); assert.equal(result.parser.warnings_unrecorded, 10);
  assert.deepEqual(result.verifier.reasons[0], { rowKey: "0", code: "parser_incomplete", cases: 8, only_cases: 3, with_other_cases: 5 });
  assert.equal(JSON.stringify(source), before);
});

test("missing or unsupported payloads are unavailable, while missing counters remain unrecorded", () => {
  for (const value of [undefined, null, [], {}, { version: "private-future-version" }]) assert.equal(executionSummary(value), null);
  const partial = executionSummary({ version: "test-execution-summary-v1", verifier: { executed: 0, reasons: [] } });
  assert.equal(partial.cases, null); assert.equal(partial.verifier.executed, 0); assert.equal(partial.verifier.parser_only, null);
  assert.equal(partial.parser.success, null); assert.equal(partial.parser.warnings, null); assert.deepEqual(partial.verifier.reasons, []);
});

test("fixed Korean and English reason labels cover current and legacy decisions without reflecting arbitrary codes", () => {
  assert.equal(verifierReasonLabel("parser_incomplete"), "요청 구조를 모두 인식하지 못함");
  assert.equal(verifierReasonLabel("parser_incomplete", (ko, en) => en), "Request structure not fully recognized");
  assert.match(verifierReasonLabel("tuning_recommended"), /이전 지침/);
  assert.equal(parserWarningLabel("request_protocol_not_recognized"), "HTTP 버전 미인식");
  assert.equal(parserWarningLabel("escaped_line_endings_not_decoded", (ko, en) => en), "Literal newline escapes left unchanged");
  for (const value of ["toString", "__proto__", "constructor", "PRIVATE_SECRET", [], {}, null]) {
    assert.equal(verifierReasonLabel(value), "기타 검증 사유");
    assert.equal(parserWarningLabel(value), "기타 파싱 경고");
  }
});

test("unknown and malformed rows cannot expose codes, arbitrary text or fabricate zero counts", () => {
  const source = fixture();
  source.verifier.reasons = [{ code: "PRIVATE_SECRET_REASON", cases: "PRIVATE_SECRET_COUNT", only_cases: -1 }, null];
  source.parser.warnings = [{ code: "<script>PRIVATE_SECRET</script>", cases: true }];
  const normalized = executionSummary(source), html = render(source);
  assert.equal(normalized.verifier.reasons[0].code, null); assert.equal(normalized.verifier.reasons[0].cases, null);
  assert.equal(normalized.verifier.reasons[1].only_cases, null);
  assert.doesNotMatch(JSON.stringify(normalized), /PRIVATE_SECRET/);
  assert.doesNotMatch(html, /PRIVATE_SECRET|<script|parser_incomplete|metadata_json|error_code/);
  assert.match(html, /기타 검증 사유/); assert.match(html, /기타 파싱 경고/); assert.match(html, /미기록/);
});

test("reusable section renders compact counters, reasons, parser distribution and explicit denominator boundaries", () => {
  const html = render(fixture());
  for (const label of ["추가 검증 현황", "추가 검증 단계", "파싱 사유 포함", "파싱 사유만", "검증 결정 후 미진입", "모의 실행 등 제외", "포함 건수", "단독", "다른 사유와 함께", "HTTP 구조 인식", "성공", "일부 미인식", "인식 실패", "기록 없음", "HTTP 버전 미인식"]) assert.ok(html.includes(label), label);
  assert.match(html, /HTTP 전송·재시도 횟수는 아닙니다/);
  assert.match(html, /안전하게 검증을 생략할 수 있는 것은 아닙니다/);
  assert.match(html, /합계가 검증 문항 수를 넘을 수 있습니다/);
  assert.match(html, /모의 실행도 실제 수행한 파싱 상태는 포함/);
  assert.match(html, /집계 기준·항목 검색·페이지 변경에는 영향을 받지 않습니다/);
  assert.match(html, /role="region" aria-label="이유별 추가 검증 문항 수" tabindex="0"/);
  assert.match(html, /role="region" aria-label="파싱 경고별 문항 수" tabindex="0"/);
  assert.match(html, /scope="col"/); assert.match(html, /aria-labelledby=/);
  assert.equal((html.match(/<table/g) || []).length, 2);
  assert.doesNotMatch(html, /role="tab|role="button|<button|metric-help|fetch\(|api\/|원문 보기/);
});

test("English labels and accessible table names do not depend on Korean defaults", () => {
  const html = render(fixture(), { words: (ko, en) => en });
  for (const label of ["Additional Verification", "Additional verification stage", "Parsing reason only", "Required, stage not entered", "HTTP Structure Recognition", "Request structure not fully recognized", "HTTP version not recognized", "Case counts by parsing warning"]) assert.ok(html.includes(label), label);
  assert.doesNotMatch(html, /추가 검증|미기록|파싱 경고/);
});

test("unavailable summary does not invent cards, rows or zero counters", () => {
  for (const value of [undefined, null, { version: "unknown" }]) {
    const html = render(value);
    assert.match(html, /추가 검증 통계가 제공되지 않습니다/);
    assert.doesNotMatch(html, /<table|<dl|<dd|>0</);
  }
  const partial = render({ version: "test-execution-summary-v1", verifier: { executed: 0 } });
  assert.match(partial, /<dd>0<\/dd>/); assert.match(partial, /미기록/);
  assert.match(partial, /검증 사유별 기록이 제공되지 않습니다/); assert.match(partial, /경고별 통계가 제공되지 않습니다/);
});

test("historical missing warnings are not represented as warning-free runs", () => {
  const source = fixture(); source.parser.warnings = []; source.parser.warnings_recorded = 0; source.parser.warnings_unrecorded = 20;
  const historical = render(source);
  assert.match(historical, /이전 실행에는 경고별 기록이 없습니다/);
  assert.match(historical, /미기록 문항은 경고 없음으로 계산하지 않습니다/);
  assert.doesNotMatch(historical, /기록된 문항에서는 파싱 경고가 없습니다/);
  source.parser.warnings_recorded = 10;
  assert.match(render(source), /기록된 문항에서는 파싱 경고가 없습니다/);
});

test("recorded verification entry without reasons is distinct from unknown execution", () => {
  const source = fixture(); source.verifier.reasons = []; source.verifier.reasons_unrecorded = 4;
  const html = render(source);
  assert.match(html, /추가 검증 단계 진입은 확인되지만 사유별 기록이 없습니다/);
  assert.match(html, /검증 사유 미기록[^<]*4/);
  assert.equal(executionSummary(source).verifier.reasons_unrecorded, 4);
  delete source.verifier.reasons_unrecorded;
  assert.equal(executionSummary(source).verifier.reasons_unrecorded, null);
  assert.doesNotMatch(render(source), /검증 사유 미기록/);
});

test("existing statistics route embeds the summary once and keeps filtering, grouping and pagination unchanged", () => {
  const source = readFileSync(new URL("./TestStatistics.jsx", import.meta.url), "utf8");
  assert.equal((source.match(/<TestExecutionSummary /g) || []).length, 1);
  assert.match(source, /summary=\{data\.execution_summary\}/);
  assert.ok(source.indexOf("<TestExecutionSummary ") > source.indexOf("<Pagination "));
  assert.match(source, /api\.testStatistics\(run\.id, query, options\)/);
  assert.match(source, /<TestCaseFilter /); assert.match(source, /statisticsQuery\(filters, state\)/);
});

test("section styles preserve theme variables, breathing room, narrow layouts and keyboard-scrollable tables", () => {
  const css = readFileSync(new URL("./testStatistics.css", import.meta.url), "utf8");
  assert.match(css, /\.test-execution-summary[^}]*margin-top: 28px[^}]*padding-top: 22px/s);
  assert.match(css, /\.test-execution-summary \.data-table-wrap[^}]*overflow: auto/s);
  assert.match(css, /\.test-execution-metrics[^}]*gap: 12px/s);
  for (const variable of ["--text", "--muted", "--line", "--raised", "--accent-soft", "--danger"]) assert.ok(css.includes(`var(${variable})`), variable);
  assert.match(css, /@media\(max-width:600px\)/);
  assert.doesNotMatch(css, /\.test-execution[^}]*outline:\s*(?:none|0)/s);
});
