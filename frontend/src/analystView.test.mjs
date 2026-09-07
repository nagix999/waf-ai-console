import assert from "node:assert/strict";
import test from "node:test";
import { analysisNotices, analystFieldLabel, analystFollowUp, analystGuidance, analystItems, analystSummary, analystText, decodingDisplayText, decodingEncodingText, decodingItemStatus, decodingWarningText, finalValue, groupedEvidence, hasTuningContent, isTechnicalText, visibleDecoding } from "./analystView.js";

const fixture = () => ({
  status: "completed", verdict: "true_positive", summary_ko: "이전 조회 요약", input_truncated: false,
  result: { verdict: "inconclusive", summary_ko: "Primary와 Verifier 판정이 서로 다릅니다.",
    recommended_checks: ["Primary / Verifier 결과를 확인하세요.", "애플리케이션 로그에서 요청의 처리 결과를 확인하세요."] },
});

test("analyst field labels name known sources without rewriting unknown paths or header names", () => {
  for (const [field, expected] of [
    ["payload", "HTTP 원문"], ["event.payload.body", "요청 본문"], ["payload.query", "요청 파라미터"],
    ["payload.query.q", "요청 파라미터 · q"], ["payload.headers.Cookie", "HTTP 헤더 · Cookie"],
    ["headers.X-Synthetic", "headers.X-Synthetic"], ["src_ip", "출발지 IP"],
    ["query", "query"], ["method", "method"], ["event.query.q", "event.query.q"],
    ["extra_fields.attributes.rule", "extra_fields.attributes.rule"], ["payload.signature", "payload.signature"],
    ["<script>synthetic</script>", "<script>synthetic</script>"], [null, "위치 미기록"],
  ]) assert.equal(analystFieldLabel(field), expected);
  assert.equal(analystFieldLabel({ secret: "NEVER_DUMP" }), "위치 미기록");
});

test("optional analyst sections omit empty content but retain real risk despite no tuning recommendation", () => {
  assert.deepEqual(analystItems([null, "", "  ", "Primary 오류", "실제 요청 처리 결과 미제공"]), ["실제 요청 처리 결과 미제공"]);
  for (const value of [null, [], {}, { recommended: false, scope: null, proposal_ko: null, risk_ko: "", validation_ko: null }]) assert.equal(hasTuningContent(value), false);
  assert.equal(hasTuningContent({ recommended: true }), true);
  assert.equal(hasTuningContent({ recommended: false, risk_ko: "범위를 넓히면 다른 공격까지 허용할 수 있습니다." }), true);
  assert.equal(hasTuningContent({ recommended: false, validation_ko: "정상·공격 요청을 분리해서 확인하세요." }), true);
  assert.equal(hasTuningContent({ recommended: false, risk_ko: "Primary/Verifier 오류" }), false);
});

test("presentation keeps the stored final verdict and explicit null; never reads Primary snapshots", () => {
  const detail = fixture();
  Object.defineProperty(detail.result, "primary", { get() { throw new Error("Intermediate result must not be read"); } });
  assert.equal(finalValue(detail, "verdict"), "inconclusive");
  assert.doesNotThrow(() => analystGuidance(detail));
  detail.result.verdict = null;
  assert.equal(finalValue(detail, "verdict"), null);
  assert.equal(finalValue({ verdict: "false_positive" }, "verdict"), "false_positive");
});

test("legacy internal disagreement and technical failures become neutral guidance, not invented missing evidence", () => {
  for (const summary of ["Primary와 Verifier 판정이 다릅니다.", "독립 검증 실패로 보류합니다.", "분석 결과가 서로 다릅니다.", "판정 불일치 발생", "판정이 일치하지 않아 보류", "1차 판정의 신뢰도가 낮습니다", "실패 ID 확인", "failure_id synthetic"]) {
    const detail = fixture(); detail.result.summary_ko = summary;
    const text = analystSummary(detail);
    assert.equal(text, "현재 분석에서는 정탐·오탐 판정을 보류했습니다.");
    assert.equal(isTechnicalText(text), false);
    assert.equal(text.includes("근거가 부족"), false);
    assert.equal(detail.result.summary_ko, summary);
  }
});

test("unfinished and failed status takes precedence over any persisted suggestion", () => {
  for (const status of ["pending", "processing", "failed"]) {
    const detail = fixture(); detail.status = status;
    detail.result.analyst_guidance = { summary_ko: "정탐으로 확정합니다." };
    assert.ok(!analystSummary(detail).includes("정탐으로 확정"));
  }
  const failed = { ...fixture(), status: "failed" };
  assert.match(analystSummary(failed), /자동 분석을 완료하지 못했습니다/);
  assert.doesNotMatch(analystSummary(failed), /근거가 부족|정상 요청/);
});

test("final server guidance is preferred, with source/check/reason and limitations preserved", () => {
  const detail = fixture();
  detail.result.analyst_guidance = {
    summary_ko: "요청 처리 경로를 확인한 후 공격 여부를 판단해 주세요.",
    checks: [{ source_ko: "애플리케이션 접근 로그", check_ko: "같은 시각의 처리 결과를 확인하세요.", why_ko: "입력 구문이 실제로 처리됐는지 확인하는 데 도움이 됩니다." }],
    limitations: ["현재 입력에는 응답 상태가 없습니다."],
  };
  assert.deepEqual(analystGuidance(detail), detail.result.analyst_guidance);
  assert.equal(analystSummary(detail), detail.result.analyst_guidance.summary_ko);
});

test("structured analyst checks outrank old generic checks; unsafe development-only checks stay out", () => {
  const detail = fixture();
  detail.result.analyst_checks = [
    { source_ko: "웹 접근 로그", check_ko: "동일 출발지의 요청 흐름을 확인하세요.", why_ko: "반복 탐색 여부 확인" },
    { source_ko: "Verifier 결과", check_ko: "추가 결과 확인", why_ko: "개발자 확인" },
  ];
  const before = JSON.stringify(detail);
  const guidance = analystGuidance(detail);
  assert.equal(guidance.checks.length, 1);
  assert.equal(guidance.checks[0].source_ko, "웹 접근 로그");
  assert.equal(JSON.stringify(detail), before);
  delete detail.result.analyst_checks;
  const fallback = analystGuidance(detail).checks;
  assert.equal(fallback.length, 1);
  assert.equal(fallback[0].source_ko, "확인 위치 미기록");
  assert.equal(fallback[0].check_ko, detail.result.recommended_checks[1]);
  assert.equal(fallback[0].why_ko, "확인 목적 미기록");
});

test("malformed or missing guidance never dumps objects or invents completed evidence", () => {
  const detail = fixture();
  detail.result.analyst_guidance = { summary_ko: { secret: "NEVER_DUMP" }, checks: [null, {}, { check_ko: 1 }], limitations: [null, {}, "독립 검증 결과 확인"] };
  detail.result.recommended_checks = [null, {}, "Primary 오류", ""];
  assert.deepEqual(analystGuidance(detail).checks, []);
  assert.deepEqual(analystGuidance(detail).limitations, []);
  assert.equal(analystText({ secret: "NEVER_DUMP" }), "미기록");
  assert.doesNotThrow(() => analystGuidance(null));
  assert.doesNotThrow(() => analystSummary({ result: [] }));
});

test("ordinary analyst language and hostile strings remain literal text, not interpreted markup", () => {
  const text = "<script>synthetic</script> [link](javascript:synthetic)\r\n%253Cscript%253E";
  assert.equal(analystText(text), text);
  assert.equal(analystText("시그니처와 실제 공격 유형이 불일치합니다."), "시그니처와 실제 공격 유형이 불일치합니다.");
  assert.equal(isTechnicalText("대상 서비스에서 정상 업무인지 확인하세요."), false);
});

test("mock, truncated input and missing result notices remain separate and honest", () => {
  assert.deepEqual(analysisNotices(fixture()), []);
  const detail = fixture(); detail.model_profile = "stub-no-llm"; detail.result.input_truncated = true;
  const notices = analysisNotices(detail);
  assert.equal(notices.length, 2); assert.match(notices[0], /모의 분석/); assert.match(notices[1], /일부가 생략/);
  detail.result.input_truncated = false;
  assert.equal(analysisNotices(detail).length, 1);
  assert.match(analysisNotices({ status: "completed", result: null })[0], /결과 본문이 없어/);
  assert.deepEqual(analysisNotices({ status: "completed", verdict: "inconclusive" }), []);
});

test("multiple decoding candidates and multi-stage values preserve exact text with bounded metadata allowlist", () => {
  const source = { decoder_version: "synthetic-v1", scan_truncated: true, scanned_chars: 100, total_chars: 200, warnings: ["합성 범위 제한"],
    items: [
      { id: "one", field: "payload.query", start: 0, end: 12, original: "%253Ctag%253E", decoded: "<tag>",
        steps: [{ encoding: "url", input: "%253Ctag%253E", output: "%3Ctag%3E" }, { encoding: "url", input: "%3Ctag%3E", output: "<tag>" }], warnings: ["문법 해석 후보"] },
      { id: "two", field: "payload.body", start: 20, end: 24, original: "  \r\n", decoded: "&lt;\n", steps: [], warnings: [] },
    ],
  };
  Object.defineProperty(source, "payload", { get() { throw new Error("No raw payload read"); } });
  Object.defineProperty(source.items[0], "api_key", { get() { throw new Error("No extra fields read"); } });
  const projected = visibleDecoding(source);
  assert.deepEqual(projected, source);
  assert.equal(projected.items[0].steps.length, 2);
  assert.equal(projected.items[1].original, "  \r\n");
  assert.notEqual(projected, source); assert.notEqual(projected.items[0], source.items[0]);
});

test("missing/malformed decoding is not described as an exhaustive clean result", () => {
  assert.equal(visibleDecoding(null), null); assert.equal(visibleDecoding([]), null);
  const projected = visibleDecoding({ scan_truncated: "false", scanned_chars: -1, total_chars: null, warnings: [null, "literal"], items: [null, {}, { original: "x", decoded: "y", start: -1, end: null, steps: [{ encoding: "url", input: {}, output: "y" }] }] });
  assert.equal(projected.items.length, 1);
  assert.equal(projected.items[0].start, null); assert.equal(projected.items[0].end, null);
  assert.equal(projected.scanned_chars, null); assert.equal(projected.total_chars, null);
  assert.deepEqual(projected.items[0].steps, []); assert.deepEqual(projected.warnings, ["literal"]);
});

test("invisible controls are explicit display markers without changing decoded source data", () => {
  const original = "literal\\u0000\u0000line\r\n\t\u202Etext\u2066fin";
  const display = decodingDisplayText(original);
  assert.equal(display, "literal\\u0000⟦U+0000⟧line⟦U+000D⟧\n⟦U+0009⟧⟦U+202E⟧text⟦U+2066⟧fin");
  assert.ok(original.includes("\u0000"));
  assert.equal(decodingDisplayText("<script>합성 🛡️</script>"), "<script>합성 🛡️</script>");
  assert.equal(decodingDisplayText(null), "");
});

test("decoder warnings explain uncertainty and limits without turning missing candidates into clean requests", () => {
  assert.match(decodingWarningText("application_decoding_unverified"), /확인되지 않았습니다/);
  assert.match(decodingWarningText("base64_candidate"), /후보/);
  assert.match(decodingWarningText("scan_limit_reached"), /일부 구간/);
  assert.match(decodingWarningText("synthetic_future_warning"), /추가 도구 안내/);
});

test("supported decoding stages use shared Korean labels and unknown values stay safe literal metadata", () => {
  const expected = {
    url_percent: "URL 퍼센트 인코딩", html_entity: "HTML 문자 참조", unicode_escape: "유니코드·문자 이스케이프",
    base64: "표준 Base64 해석 후보", url_percent_u: "이전 방식 %uNNNN 해석 후보",
    base64url: "URL-safe Base64 해석 후보", log4j_lookup_static: "Log4j 조회 표현식 · 실행 없는 정적 해석",
  };
  for (const [code, label] of Object.entries(expected)) assert.equal(decodingEncodingText(code), label);
  assert.equal(decodingEncodingText(null), "변환 방식 미기록");
  assert.equal(decodingEncodingText({ secret: "NEVER_DUMP" }), "변환 방식 미기록");
  assert.equal(decodingEncodingText("synthetic <script>\u202E"), "기타 변환: synthetic <script>⟦U+202E⟧");
});

test("unchanged JNDI and unresolved lookups are static notices, not successful decoding steps", () => {
  for (const value of ["${jndi:ldap://synthetic.invalid/a}", "${env:SYNTHETIC_VAR}", "$${jndi:ldap://synthetic.invalid/b}"]) {
    const item = { original: value, decoded: value, steps: [] };
    assert.equal(decodingItemStatus(item), "변환하지 않음 · 정적 확인 안내");
    assert.equal(item.original, value); assert.equal(item.decoded, value);
  }
  assert.equal(decodingItemStatus({ original: "x", decoded: "y", steps: [] }), "변환 단계 미기록");
  assert.match(decodingItemStatus({ original: "%u003C", decoded: "<", steps: [{ encoding: "url_percent_u" }] }), /기록된 변환 1단계.*미확인/);
  assert.equal(decodingItemStatus(null), "변환 단계 미기록");
});

test("new decoder warnings distinguish assumed syntax, conditional defaults and unexecuted JNDI", () => {
  assert.match(decodingWarningText("legacy_percent_u_candidate"), /표준 URL 인코딩이 아닌/);
  assert.match(decodingWarningText("unicode_codepoint_escape_candidate"), /중괄호 코드 포인트/);
  assert.match(decodingWarningText("base64_padding_inferred"), /누락된.*가정/);
  assert.match(decodingWarningText("default_lookup_candidate"), /속성이 정의되지 않았을 때/);
  assert.match(decodingWarningText("default_lookup_candidate"), /실제 환경의 값은 조회하지 않았습니다/);
  assert.match(decodingWarningText("unresolved_lookup"), /그대로 남겼습니다/);
  assert.match(decodingWarningText("jndi_lookup_not_executed"), /조회와 외부 연결은 수행하지 않았습니다/);
  assert.match(decodingWarningText("jndi_lookup_not_executed"), /공격 성공을 확인할 수 없습니다/);
  assert.equal(decodingWarningText({ message: "NEVER_DUMP" }), "도구 안내 미기록");
});

test("Log4j malformed, escaped, bounded and locale-dependent cases have explicit Korean guidance", () => {
  for (const code of ["escaped_log4j_lookup", "malformed_log4j_lookup", "log4j_input_too_long", "log4j_output_too_long", "log4j_depth_limit_reached", "log4j_node_limit_reached", "log4j_operation_limit_reached", "log4j_unicode_case_unsupported", "log4j_case_locale_unverified"]) {
    const message = decodingWarningText(code);
    assert.ok(!message.startsWith("추가 도구 안내"), code);
    assert.match(message, /[가-힣]/);
  }
  assert.match(decodingWarningText("escaped_log4j_lookup"), /환경과 처리 단계/);
  assert.match(decodingWarningText("log4j_case_locale_unverified"), /JVM 언어·지역 설정/);
  assert.match(decodingWarningText("log4j_unicode_case_unsupported"), /수행하지 않고 보존/);
});

test("follow-up checks distinguish unresolved judgment from optional decisive-result follow-up, independent of score", () => {
  for (const confidence_score of [0, 0.49, 0.97, 1]) {
    const detail = fixture();
    detail.result.confidence_score = confidence_score;
    assert.equal(analystFollowUp(detail).visible, true);
    assert.match(analystFollowUp(detail).introduction_ko, /판정 보류를 해소하려면 아래 자료/);
    for (const verdict of ["true_positive", "false_positive"]) {
      detail.result.verdict = verdict;
      assert.equal(analystFollowUp(detail).visible, true);
      assert.match(analystFollowUp(detail).introduction_ko, /필요한 경우/);
      assert.doesNotMatch(analystFollowUp(detail).introduction_ko, /판정 보류/);
    }
  }
});

test("explicit empty final guidance suppresses legacy checks but missing guidance retains compatibility", () => {
  const detail = fixture();
  detail.result.verdict = "true_positive";
  assert.equal(analystFollowUp(detail).visible, true);
  detail.result.analyst_guidance = { checks: [], limitations: ["입력 일부 생략"] };
  assert.deepEqual(analystGuidance(detail).checks, []);
  assert.equal(analystFollowUp(detail).visible, false);
  assert.deepEqual(analystGuidance(detail).limitations, ["입력 일부 생략"]);
  detail.result.verdict = "inconclusive";
  assert.equal(analystFollowUp(detail).visible, true);
  assert.match(analystFollowUp(detail).empty_ko, /구체적인 확인 자료는 기록되지 않았습니다/);
  for (const status of ["failed", "pending", "processing"]) {
    detail.status = status;
    assert.equal(analystFollowUp(detail).visible, false);
  }
  assert.equal(analystFollowUp({ status: "completed", verdict: "inconclusive", result: null }).visible, false);
});

test("evidence grouping displays a shared exact quotation once and preserves opposing interpretations without mutation", () => {
  const first = { field: "payload.query", excerpt: "q=synthetic", interpretation_ko: "공격 구문으로 사용될 가능성이 있습니다." };
  const opposite = { ...first, interpretation_ko: "문서 예제 문자열을 저장하는 정상 요청일 수도 있습니다." };
  const original = [first, { ...first }, opposite, { ...opposite }];
  const before = JSON.stringify(original);
  const groups = groupedEvidence(original);
  assert.equal(groups.length, 1);
  assert.equal(groups[0].excerpt, first.excerpt);
  assert.deepEqual(groups[0].interpretations, [first.interpretation_ko, opposite.interpretation_ko]);
  assert.equal(JSON.stringify(original), before);
  assert.notEqual(groups[0], first);
});

test("evidence grouping does not equate different fields, case, whitespace, or malformed field locations", () => {
  const item = { field: "payload.query", excerpt: "q=X", interpretation_ko: "합성 해석" };
  const items = [item, { ...item, field: "payload.body" }, { ...item, excerpt: "q=x" }, { ...item, excerpt: " q=X" },
    { ...item, interpretation_ko: "합성 해석 " }, {}, {}, null, false];
  const groups = groupedEvidence(items);
  assert.equal(groups.length, 6);
  assert.deepEqual(groups[0].interpretations, ["합성 해석", "합성 해석 "]);
  assert.deepEqual(groupedEvidence(null), []);
});

test("the exact historical grounding diagnostic becomes one limitation, not follow-up work", () => {
  const diagnostic = "일부 LLM 근거가 지정된 필드의 원문과 일치하지 않아 제외되었습니다. 남은 근거를 직접 확인하세요.";
  const limitation = "일부 발췌는 지정한 원문에서 확인되지 않아 판정 근거에서 제외했습니다.";
  const detail = fixture();
  detail.result.verdict = "true_positive";
  detail.result.recommended_checks = [diagnostic];
  detail.result.analyst_guidance = { checks: [{ source_ko: "해당 요청과 관련된 운영 자료", check_ko: diagnostic, why_ko: "후속 확인" }], limitations: [limitation, diagnostic] };
  const before = JSON.stringify(detail);
  assert.deepEqual(analystGuidance(detail).checks, []);
  assert.deepEqual(analystGuidance(detail).limitations, [limitation]);
  assert.equal(analystFollowUp(detail).visible, false);
  assert.equal(JSON.stringify(detail), before);
  delete detail.result.analyst_guidance;
  assert.deepEqual(analystGuidance(detail).checks, []);
  assert.deepEqual(analystGuidance(detail).limitations, [limitation]);
});

test("grounding diagnostic compatibility preserves all other checks and respects explicit empty lists", () => {
  const diagnostic = "일부 LLM 근거가 지정된 필드의 원문과 일치하지 않아 제외되었습니다. 남은 근거를 직접 확인하세요.";
  const work = { source_ko: "합성 접근 로그", check_ko: "동일 요청의 처리 결과를 확인하세요.", why_ko: "서비스 영향 확인" };
  const different = { ...work, check_ko: `${diagnostic} 다른 조사도 필요합니다.` };
  const detail = fixture();
  detail.result.verdict = "false_positive";
  detail.result.analyst_guidance = { checks: [{ ...work, check_ko: diagnostic }, work, different] };
  assert.deepEqual(analystGuidance(detail).checks, [work, different]);
  assert.equal(analystFollowUp(detail).visible, true);
  detail.result.analyst_guidance.checks = [];
  detail.result.recommended_checks = [diagnostic, work.check_ko];
  assert.deepEqual(analystGuidance(detail).checks, []);
  assert.equal(analystGuidance(detail).limitations.length, 1);
  assert.equal(analystFollowUp(detail).visible, false);
  detail.result.verdict = "inconclusive";
  assert.equal(analystFollowUp(detail).visible, true);
});
