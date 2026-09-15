import assert from "node:assert/strict";
import test from "node:test";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";
import { runInNewContext } from "node:vm";
import { buildSync } from "esbuild";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { completionReason, diagnosticCount, modelValidationError } from "./modelCheckDiagnostics.js";

test("incomplete output, refusal and timeout have distinct actionable messages", () => {
  for (const provider of ["vllm", "openai"]) {
    const length = modelValidationError(`${provider}_output_incomplete`);
    assert.match(length, /토큰 한도/); assert.match(length, /동시 요청 수가 1이어도/);
    assert.doesNotMatch(length, /거절했습니다|GPU 메모리 부족|자동으로 재시도/);
    const refusal = modelValidationError(`${provider}_refusal`);
    assert.match(refusal, /응답을 거절/); assert.match(refusal, /출력 길이 부족과는 다른/);
    assert.match(modelValidationError(`${provider}_timeout`), /제한 시간 안에/);
    assert.match(modelValidationError(`${provider}_connection_failed`), /주소·포트/);
    assert.match(modelValidationError(`${provider}_http_429`), /호출 한도/);
    assert.match(modelValidationError(`${provider}_http_503`), /HTTP 상태/);
  }
  assert.match(modelValidationError("concurrency_limit_below_test"), /서버 상한/);
  assert.match(modelValidationError("json_schema_validation_failed"), /JSON 형식/);
  for (const code of [null, undefined, {}, "toString", "__proto__", "synthetic-secret-error"]) {
    assert.equal(modelValidationError(code), "검증을 완료하지 못했습니다. 확인 내용과 검증 기술정보를 확인하세요.");
  }
});

test("missing counters stay unknown and real zero stays zero", () => {
  assert.equal(diagnosticCount(0), "0"); assert.equal(diagnosticCount(256), "256");
  assert.equal(diagnosticCount(3072), "3,072");
  for (const value of [null, undefined, true, false, -1, 1.2, NaN, Infinity, 2 ** 53, "256", "secret", {}]) {
    assert.equal(diagnosticCount(value), "미기록");
  }
});

test("finish reasons are allowlisted and refusal takes precedence over stop", () => {
  assert.equal(completionReason({ finish_reason: "length" }), "토큰 한도 도달");
  assert.equal(completionReason({ finish_reason: "stop" }), "정상 종료");
  assert.equal(completionReason({ finish_reason: "stop", refused: true }), "응답 거절");
  assert.equal(completionReason({ finish_reason: "content_filter" }), "콘텐츠 필터");
  assert.equal(completionReason({ error_code: "vllm_timeout" }), "시간 초과");
  assert.equal(completionReason({ error_code: "vllm_invalid_response", finish_reason: "stop" }), "응답 형식 오류");
  assert.equal(completionReason({ error_code: "openai_http_429" }), "HTTP 429");
  assert.equal(completionReason(null), "미기록");
  for (const value of ["toString", "__proto__", "synthetic-secret", {}, ["stop"]]) {
    assert.equal(completionReason({ finish_reason: value }), "알 수 없음");
  }
});

const bundled = buildSync({ entryPoints: [fileURLToPath(new URL("./ModelCheckDetail.jsx", import.meta.url))], bundle: true, write: false, platform: "node", format: "cjs", packages: "external", jsx: "automatic", loader: { ".css": "empty" }, logLevel: "silent" }).outputFiles[0].text;
const module = { exports: {} }; runInNewContext(bundled, { module, exports: module.exports, require: createRequire(import.meta.url), process, URL });
const render = check => renderToStaticMarkup(createElement(module.exports.default, { check }));

test("check detail shows recorded budgets and outcomes without raw JSON by default", () => {
  const html = render({ name: "concurrency", status: "failed", error_code: "vllm_output_incomplete",
    detail: { requests: 2, private: "synthetic-technical-record-only" },
    response_diagnostics: [
      { request_index: 1, requested_max_output_tokens: 256, prompt_tokens: 1024, completion_tokens: 256, finish_reason: "length" },
      { request_index: 2, requested_max_output_tokens: 256, prompt_tokens: 1024, completion_tokens: 0, finish_reason: "stop", refused: true },
    ],
  });
  for (const label of ["동시 요청", "실패", "출력 한도", "입력 사용량", "출력 사용량", "종료 사유", "토큰 한도 도달", "응답 거절", "1,024", "256", "기술 기록"]) assert.ok(html.includes(label), label);
  assert.match(html, /<td>0<\/td>/); assert.match(html, /aria-expanded="false"/);
  assert.equal((html.match(/<tbody><tr>|<\/tr><tr>/g) || []).length, 2);
  assert.doesNotMatch(html, /synthetic-technical-record-only|text-inspector|metric-help/);
  assert.match(html, /role="region" aria-label="요청별 토큰과 종료 사유" tabindex="0"/);
});

test("legacy detail does not manufacture request values from current profile settings", () => {
  const html = render({ name: "concurrency", status: "failed", error_code: "vllm_output_incomplete", message: "vllm did not return a completed non-refused response" });
  assert.match(html, /기존 기록에는 요청별 토큰·종료 정보가 없습니다/);
  assert.doesNotMatch(html, /<table|<td>0|256|non-refused/);
  const noDispatch = render({ name: "concurrency", status: "failed", error_code: "concurrency_limit_below_test", response_diagnostics: [] });
  assert.match(noDispatch, /기록된 LLM 요청이 없습니다/); assert.doesNotMatch(noDispatch, /기존 기록/);
  assert.doesNotMatch(render({ name: "models", status: "passed", response_diagnostics: [] }), /기록된 LLM 요청|기존 기록/);
});

test("malformed and partial diagnostics remain readable with unknown values", () => {
  const html = render({ name: "basic_chat", status: "failed", error_code: "vllm_timeout", response_diagnostics: [null, {
    request_index: 1, requested_max_output_tokens: 256, completion_tokens: "synthetic-secret", finish_reason: "synthetic-secret",
  }] });
  assert.match(html, /제한 시간 안에/); assert.match(html, /미기록/); assert.match(html, /알 수 없음/);
  assert.doesNotMatch(html, /synthetic-secret|<td>0/);
  assert.match(render(null), /기록된 검증 항목이 없습니다/);
});
