import assert from "node:assert/strict";
import test from "node:test";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";
import { runInNewContext } from "node:vm";
import { buildSync } from "esbuild";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { parserJobTimeline, parserJobDiagnostics, parserJobFailure, parserJobKindLabel,
  parserJobStatusLabel } from "./parserWorkbenchJobs.js";

function load(name = "default") {
  const source = buildSync({ entryPoints: [fileURLToPath(new URL("./ParserWorkbenchJobs.jsx", import.meta.url))],
    bundle: true, write: false, platform: "node", format: "cjs", packages: "external", jsx: "automatic",
    loader: { ".css": "empty" }, logLevel: "silent" }).outputFiles[0].text;
  const module = { exports: {} };
  runInNewContext(source, { module, exports: module.exports, require: createRequire(import.meta.url), console,
    URL, setTimeout, clearTimeout, AbortController });
  return module.exports[name];
}
const job = (number, status = "failed", changes = {}) => ({ id: `job-${number}`, kind: "rule_generation", status,
  session_revision: number, created_at: `2026-11-18T00:${String(number).padStart(2, "0")}:00Z`,
  error_code: status === "failed" ? "parser_workbench_output_incomplete" : null, ...changes });

for (const status of ["completed", "pending", "running", "processing"]) {
  test(`a latest ${status} job supersedes an earlier failure without altering history`, () => {
    const jobs = [job(1), job(2, status)], before = JSON.stringify(jobs);
    const value = parserJobTimeline(jobs);
    assert.deepEqual(value.latest.map(row => row.id), ["job-2"]);
    assert.equal(value.failures.length, 0);
    assert.deepEqual(value.history.map(row => row.id), ["job-1"]);
    assert.equal(JSON.stringify(jobs), before);
  });
}
test("latest statuses stay distinct by work kind and admission time, not response order", () => {
  const jobs = [job(3), job(1, "completed", { kind: "format_discovery" }), job(2, "completed")];
  const value = parserJobTimeline(jobs);
  assert.deepEqual(value.latest.map(row => row.id), ["job-1", "job-3"]);
  assert.deepEqual(value.failures.map(row => row.id), ["job-3"]);
  assert.deepEqual(value.history.map(row => row.id), ["job-2"]);
});
test("starting format discovery retires an older generation failure", () => {
  const value = parserJobTimeline([job(1), job(2, "running", { kind: "format_discovery" })]);
  assert.deepEqual(value.latest.map(row => row.id), ["job-2"]);
  assert.equal(value.failures.length, 0);
  assert.deepEqual(value.history.map(row => row.id), ["job-1"]);
});
for (const status of ["pending", "running", "completed", "failed"]) {
  test(`new ${status} generation retires a failed discovery after manual confirmation`, () => {
    const value = parserJobTimeline([job(2, status), job(1, "failed", { kind: "format_discovery" })]);
    assert.deepEqual(value.latest.map(row => row.id), ["job-2"]);
    assert.deepEqual(value.failures.map(row => row.id), status === "failed" ? ["job-2"] : []);
    assert.deepEqual(value.history.map(row => row.id), ["job-1"]);
  });
}
test("missing timestamps use the stored order and revision without inventing chronology", () => {
  const value = parserJobTimeline([job(1, "failed", { created_at: null }), job(2, "completed", { created_at: null })]);
  assert.equal(value.failures.length, 0);
  assert.equal(parserJobTimeline(null).latest.length, 0);
  assert.equal(parserJobTimeline([{ kind: "PRIVATE_PROVIDER_BODY", status: "failed" }]).latest.length, 0);
});
test("only fixed error categories choose human explanations, in Korean and English", () => {
  const categories = [
    ["parser_workbench_output_incomplete", /끝까지 생성/, /complete its response/],
    ["parser_workbench_json_invalid", /JSON으로/, /as JSON/],
    ["parser_workbench_schema_invalid", /정의와 맞지/, /does not match/],
    ["parser_workbench_authoring_failed", /입력을 올바르게 추출/, /correctly extract/],
    ["parser_regex_capture_invalid", /문법이나 추출 설정/, /syntax or extraction/],
    ["parser_json_invalid", /JSON 입력/, /input as JSON/],
    ["parser_source_invalid", /문자 인코딩/, /character encoding/],
    ["parser_group_confirmation_required", /형식 구성이 확인되지/, /not been confirmed/],
    ["parser_suggestion_timeout", /시간이 초과/, /timed out/],
    ["parser_suggestion_connection_failed", /연결하지 못/, /could not be reached/],
    ["parser_workbench_permission_denied", /권한이나 승인/, /authorization or approval/],
    ["parser_workbench_rate_limited", /요청 한도/, /request limit/],
    ["parser_workbench_provider_unavailable", /요청을 처리하지 못/, /could not process/],
    ["parser_workbench_refusal", /요청을 거절/, /rejected/],
    ["parser_workbench_context_limit", /작업 한도/, /workbench limit/],
    ["parser_profile_changed", /설정이 변경/, /settings changed/],
    ["parser_regex_unavailable", /처리 엔진/, /engine is unavailable/],
    ["parser_workbench_attempt_limit", /요청 한도/, /request limit/],
  ];
  for (const [error_code, ko, en] of categories) {
    assert.match(parserJobFailure(job(1, "failed", { error_code })).message, ko);
    assert.match(parserJobFailure(job(1, "failed", { error_code }), true).message, en);
    assert.ok(parserJobFailure(job(1, "failed", { error_code }), true).action);
  }
});
test("legacy invalid_rule and unknown errors do not blame connectivity or permissions", () => {
  for (const error_code of ["parser_workbench_invalid_rule", "PRIVATE_RAW_BODY sk-secret", null]) {
    const failure = parserJobFailure(job(1, "failed", { error_code }));
    assert.match(failure.message, /자세한 원인은 기록되지/);
    assert.doesNotMatch(failure.message + failure.action, /연결|권한|PRIVATE|sk-secret/);
  }
});
test("diagnostics preserve explicit correction counts, never infer retries from calls or groups", () => {
  const value = parserJobDiagnostics(job(1, "failed", { summary: { call_count: 8, diagnostics: {
    attempt_count: 7, correction_count: 2, last_error_stage: "authoring",
    attempts: [{ attempt: 7, correction: true, stage: "authoring", code: "parser_workbench_authoring_failed",
      group_index: 3, output_limit: 3072, usage: { prompt_tokens: 345, completion_tokens: 78, total_tokens: 423 },
      failed_sample_ids: ["private-source-id-1", "private-source-id-2"] }],
  } } }));
  assert.equal(value.attempt_count, 7);
  assert.equal(value.correction_count, 2);
  assert.equal(value.call_reservations, 8);
  assert.equal(value.attempts[0].group_index, 3);
  assert.deepEqual(value.attempts[0].usage, { prompt_tokens: 345, completion_tokens: 78, total_tokens: 423 });
  assert.equal(value.attempts[0].failed_sample_count, 2);
  assert.doesNotMatch(JSON.stringify(value), /private-source-id/);
  assert.equal(parserJobDiagnostics(job(1)).correction_count, null);
});
test("diagnostics reject arbitrary codes, issue fields, provider messages and nonnumeric usage", () => {
  const value = parserJobDiagnostics(job(1, "failed", { error_code: "PRIVATE_PROVIDER_BODY", summary: {
    raw_response: "PRIVATE_RAW_INPUT", call_count: "PRIVATE_COUNT", diagnostics: {
      last_error_code: "PRIVATE_LAST_ERROR", last_error_stage: "PRIVATE_STAGE", attempt_count: true,
      correction_count: -1, attempts: [{ stage: "PRIVATE_STAGE", code: "PRIVATE_PROVIDER_BODY", output_limit: "PRIVATE_LIMIT",
        finish_reason: "PRIVATE_STOP", usage: { input_tokens: "PRIVATE_TOKEN", output_tokens: true,
          total_tokens: Infinity, PRIVATE_FIELD: "PRIVATE_VALUE" }, issues: [
          { field: "extract.PRIVATE_PAYLOAD", type: "PRIVATE_TYPE", input: "PRIVATE_INPUT" },
          { field: "extract.pattern", type: "string_type" },
        ], previous_rule: "PRIVATE_RULE", failed_sample_ids: ["PRIVATE_ID"] }],
    },
  } }));
  assert.doesNotMatch(JSON.stringify(value), /PRIVATE/);
  assert.equal(value.attempt_count, null);
  assert.equal(value.correction_count, null);
  assert.equal(value.call_reservations, null);
  assert.deepEqual(value.attempts[0].issues, [
    { field: "extract.<unknown>", type: "validation_error" }, { field: "extract.pattern", type: "string_type" },
  ]);
});
test("a recovered completed job keeps prior diagnostic errors as history, not a current failure", () => {
  const value = parserJobDiagnostics(job(1, "completed", { summary: { diagnostics: {
    last_error_code: "parser_workbench_schema_invalid", attempts: [{ stage: "rule_validation", code: "ok" }],
  } } }));
  assert.equal(value.error_code, null);
  assert.equal(value.last_error_code, "parser_workbench_schema_invalid");
  assert.equal(value.attempts[0].code, "ok");
});
test("fixed authoring mismatch reasons remain available only in safe technical issues", () => {
  const row = job(1, "failed", { error_code: "parser_workbench_authoring_failed", summary: { diagnostics: {
    attempts: [{ code: "parser_workbench_authoring_failed", stage: "authoring", issues: [
      { field: "extract", type: "parser_rule_not_matched" },
      { field: "extract", type: "parser_json_pointer_missing" },
      { field: "extract", type: "parser_request_not_qualified" },
    ] }],
  } } });
  assert.deepEqual(parserJobDiagnostics(row).attempts[0].issues.map(item => item.type), [
    "parser_rule_not_matched", "parser_json_pointer_missing", "parser_request_not_qualified",
  ]);
  assert.doesNotMatch(parserJobFailure(row).message, /parser_/);
});
test("failed latest work shows a concise reason and corrections, with technical codes in details only", () => {
  const Page = load();
  const html = renderToStaticMarkup(createElement(Page, { jobs: [job(1, "failed", { summary: {
    diagnostics: { correction_count: 2, attempt_count: 3 },
  } })] }));
  assert.match(html, /role="alert"/);
  assert.match(html, /끝까지 생성/);
  assert.match(html, /교정 시도/);
  assert.match(html, /2회/);
  assert.match(html, /작업 상세/);
  assert.doesNotMatch(html, /parser_workbench_output_incomplete|연결과 권한/);
});
for (const status of ["completed", "running", "pending"]) {
  test(`old failures do not announce a current error after latest work is ${status}`, () => {
    const Page = load(), html = renderToStaticMarkup(createElement(Page, { jobs: [job(1), job(2, status)] }));
    assert.doesNotMatch(html, /role="alert"|완료하지 못한 모델 작업/);
    assert.match(html, /이전 작업 이력/);
    assert.match(html, /<details/);
    assert.match(html, /<summary/);
  });
}
test("job history is paginated and current status is separate from previous rows", () => {
  const Page = load();
  const jobs = Array.from({ length: 24 }, (_, index) => job(index + 1, index === 23 ? "completed" : "failed"));
  const html = renderToStaticMarkup(createElement(Page, { jobs }));
  assert.doesNotMatch(html, /role="alert"/);
  assert.match(html, /이전 작업 이력.*23/);
  assert.match(html, /이전 모델 작업 페이지/);
  assert.match(html, /3페이지/);
});
for (const english of [false, true]) {
  test(`technical job details are safe and fixed in ${english ? "English" : "Korean"}`, () => {
    const Details = load("ParserWorkbenchJobDetails"), html = renderToStaticMarkup(createElement(Details, {
      english, job: job(1, "failed", { error_code: "PRIVATE_PROVIDER_BODY", summary: { diagnostics: {
        attempts: [{ stage: "PRIVATE_STAGE", code: "PRIVATE_ERROR", issues: [{ field: "PRIVATE_PAYLOAD", type: "PRIVATE_TYPE" }] }],
      } } }),
    }));
    assert.doesNotMatch(html, /PRIVATE|<script>/);
    assert.match(html, english ? /not recorded/ : /원인은 기록되지/);
    assert.match(html, english ? /Call reservations may differ/ : /실제 전송 횟수와 다를/);
    assert.match(html, /unrecorded_error/);
  });
}
test("work labels and unknown statuses never echo arbitrary server content", () => {
  for (const english of [false, true]) {
    assert.doesNotMatch(parserJobKindLabel("PRIVATE_KIND", english), /PRIVATE/);
    assert.doesNotMatch(parserJobStatusLabel("PRIVATE_STATUS", english), /PRIVATE/);
  }
  const Details = load("ParserWorkbenchJobDetails");
  const html = renderToStaticMarkup(createElement(Details, { job: job(1, "PRIVATE_STATUS") }));
  assert.doesNotMatch(html, /PRIVATE_STATUS/);
});
