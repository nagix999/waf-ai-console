// Deterministic presentation of saved fields and explicitly loaded decoding.
// Do not stringify whole result objects: raw payloads, Agent outputs and unknown
// extension fields are intentionally outside this report's allowlist.
import { evaluationExplanation, evaluationOutcomeText, isMockAnalysis, referenceSourceText, referenceVerdicts, referenceVisibilityText } from "./labelEvaluation.js";
import { analystFieldLabel, analystFollowUp, analystGuidance, analystItems, analystText, decodingEncodingText, decodingItemStatus, decodingWarningText, groupedEvidence, hasTuningContent, isTechnicalText, visibleDecoding } from "./analystView.js";
const punctuation = /[\x21-\x2f\x3a-\x40\x5b-\x60\x7b-\x7e]/;
const escapedPunctuation = /\\([\x21-\x2f\x3a-\x40\x5b-\x60\x7b-\x7e])/g;
const entities = {
  "&amp;": "&", "&lt;": "<", "&gt;": ">",
  "&#9;": "\t", "&#10;": "\n", "&#13;": "\r", "&#32;": " ",
  "&#8232;": "\u2028", "&#8233;": "\u2029",
};
const isRecord = (value) => value !== null && typeof value === "object" && !Array.isArray(value);
const owns = (value, key) => isRecord(value) && Object.hasOwn(value, key);

function scalar(value) {
  if (value === null) return "미기록 (null)";
  if (typeof value === "string") return value === "" ? "빈 문자열" : value;
  if (typeof value === "boolean" || (typeof value === "number" && Number.isFinite(value))) return String(value);
  return "미기록";
}

function escapeText(value) {
  const text = scalar(value);
  const trailingSpaces = / +$/.exec(text)?.[0].length || 0;
  let leading = true;
  let offset = 0;
  let escaped = "";
  for (const character of text) {
    if (character === "&") escaped += "&amp;";
    else if (character === "<") escaped += "&lt;";
    else if (character === ">") escaped += "&gt;";
    else if (character === "\n") escaped += "&#10;";
    else if (character === "\r") escaped += "&#13;";
    else if (character === "\t") escaped += "&#9;";
    else if (character === "\u2028") escaped += "&#8232;";
    else if (character === "\u2029") escaped += "&#8233;";
    else if (character === " " && (leading || offset >= text.length - trailingSpaces)) escaped += "&#32;";
    else escaped += punctuation.test(character) ? `\\${character}` : character;
    if (/[\r\n\u2028\u2029]/.test(character)) leading = true;
    else if (character !== " " && character !== "\t") leading = false;
    offset += character.length;
  }
  return escaped;
}

// Call once on parsed non-code text, then render as React text children only.
// Never feed the decoded string back into Markdown or an HTML interpreter.
export function decodeReportText(text) {
  if (typeof text !== "string") return "";
  return text.replace(escapedPunctuation, "$1")
    .replace(/&(?:amp|lt|gt|#9|#10|#13|#32|#8232|#8233);/g, (entity) => entities[entity]);
}

function named(value, labels) {
  const key = ["string", "boolean", "number"].includes(typeof value) ? String(value) : null;
  return key !== null && Object.hasOwn(labels, key)
    ? `${labels[key]} (${key})` : scalar(value);
}

function table(rows) {
  return ["| 항목 | 내용 |", "| --- | --- |", ...rows.map(([label, value]) => `| ${label} | ${escapeText(value)} |`)].join("\n");
}

function textList(value, empty = "저장된 항목이 없습니다.") {
  const items = Array.isArray(value) ? value.filter((item) => item === null || ["string", "number", "boolean"].includes(typeof item)) : [];
  return items.length ? items.map((item) => `- ${escapeText(item)}`).join("\n") : empty;
}

function fencedExcerpt(value) {
  let longest = 0;
  for (const run of value.matchAll(/`+/g)) longest = Math.max(longest, run[0].length);
  const fence = "`".repeat(Math.max(3, longest + 1));
  // The final LF is always fence syntax. Report rendering removes exactly that
  // one LF from the parsed code text; original trailing whitespace is preserved.
  return `${fence}text\n${value}\n${fence}`;
}

function confidence(value) {
  return typeof value === "number" && Number.isFinite(value) && value >= 0 && value <= 1
    ? `${value} (${Math.round(value * 10000) / 100}%)` : scalar(value);
}

function duration(value) {
  if (value === null) return "미측정 (null)";
  return typeof value === "number" && Number.isFinite(value) && value >= 0 ? `${value} ms` : "미측정";
}

export function buildAnalysisReport(input, { decoding = null, includeAppendix = false } = {}) {
  const detail = isRecord(input) ? input : {};
  const result = isRecord(detail.result) ? detail.result : null;
  const finalValue = (key) => owns(result, key) ? result[key] : detail[key];
  const threat = isRecord(result?.threat_analysis) ? result.threat_analysis : {};
  const signature = isRecord(result?.signature_assessment) ? result.signature_assessment : {};
  const tuning = isRecord(result?.tuning_recommendation) ? result.tuning_recommendation : {};
  const verifier = includeAppendix && isRecord(result?.verifier) ? result.verifier : null;
  const stub = isMockAnalysis(detail);
  const completed = detail.status === "completed";
  const final = completed && result !== null && !stub;
  const inconclusive = finalValue("verdict") === "inconclusive";
  const guidance = analystGuidance(detail);
  const followUp = analystFollowUp(detail);
  const explanation = (value) => analystText(value, value === null ? "미기록 (null)" : "분석가가 원문과 확인 항목을 함께 검토해 주세요.");
  const analystList = (value) => textList(Array.isArray(value) ? value.filter((item) => typeof item === "string" && !isTechnicalText(item)) : []);
  const report = [
    `# ${stub ? "WAF 모의 분석 보고서" : final ? "WAF 분석 보고서" : "WAF 분석 상태 보고서"}`,
    "저장된 자동 분석 결과를 정리한 보고서입니다. 보고서 조회로 새 분석이 실행되지는 않습니다.",
  ];
  // Always end with the analyst's next steps, including when the optional
  // execution/답안 appendix is included. No generic checklist for every result.
  const finishReport = () => {
    if (followUp.visible) {
      report.push("## 추가 확인 사항", escapeText(followUp.introduction_ko), "안내된 자료를 시스템이 이미 조회했다는 뜻은 아닙니다.");
      if (!followUp.checks.length) report.push(escapeText(followUp.empty_ko));
      followUp.checks.forEach((item, index) => report.push(`### 확인 ${index + 1}`, table([
        ["확인 위치", item.source_ko], ["확인할 내용", item.check_ko], ["확인 목적", item.why_ko],
      ])));
    }
    return report.join("\n\n") + "\n";
  };
  if (stub) report.push("모의 분석(stub) 결과입니다. 실제 LLM의 보안 판정이나 공격 여부를 입증하는 보고서가 아닙니다.");
  if (!completed) report.push("분석이 완료되지 않았습니다. 저장된 판정 정보가 있더라도 최종 판정 보고서가 아닙니다.");
  else if (!result) report.push("저장된 결과 본문이 없어 최종 판정 보고서를 구성할 수 없습니다.");
  if (final) report.push("자동 분석 결과 · 최종 판단은 분석가가 검토합니다. 공격 시도와 실제 피해 발생은 구분합니다.");

  const overview = ["## 분석 개요", table([
    ["처리 상태", named(detail.status, { pending: "대기", processing: "처리 중", completed: "완료", failed: "실패" })],
    ["회사명", detail.company_name],
    ["출발 IP", detail.src_ip], ["출발 포트", detail.src_port],
    ["목적 IP", detail.dest_ip], ["목적 포트", detail.dest_port],
    ["WAF 벤더", detail.waf_vendor], ["WAF Action", named(detail.waf_action, { D: "Deny", A: "Allow" })],
    ["이벤트명", detail.event_name], ["입력 시그니처", detail.signature],
  ]), "WAF Action은 관측값이며 AI 판정의 정답이나 공격 성공 여부가 아닙니다."];

  const severity = owns(threat, "severity") ? threat.severity
    : result ? (result.schema_version === "waf-analysis-v2" ? "미평가(심각도 미기록)" : "미평가(이전 결과)")
      : (detail.severity ?? "미평가");
  report.push(`## ${stub ? "모의 판정 정보" : final ? "판정 요약" : "저장된 판정 정보 (최종 아님)"}`, table([
    ["판정", named(finalValue("verdict"), { true_positive: "정탐", false_positive: "오탐", inconclusive: "판단 보류" })],
    ["위협 심각도", severity],
    ["입력 잘림", named(finalValue("input_truncated"), { true: "있음", false: "없음" })],
  ]), escapeText(guidance.summary_ko));
  if (inconclusive) report.push("아래 기법·영향·근거 해석은 검토할 가능성이며 확정된 공격이나 피해를 뜻하지 않습니다.");
  report.push(...overview);

  report.push("## 세부 분석", table([
    ["공격 유형", explanation(threat.category)], ["분석 위치", analystFieldLabel(analystText(threat.target))], ["분석 내용", explanation(threat.technique_ko)], ["예상 영향", explanation(threat.potential_impact_ko)],
  ]));
  if (analystItems(threat.obfuscations).length) report.push("### 인코딩·난독화", analystList(threat.obfuscations));
  if (isRecord(result?.signature_assessment)) report.push("### 탐지 내용과 요청의 연관성", table([
    ["관계", named(signature.relation, { exact: "일치", partial: "부분 일치", mismatch: "불일치", unknown: "평가 불가" })],
    ["설명", explanation(signature.explanation_ko)],
  ]));

  report.push("## 판정 근거");
  const evidence = groupedEvidence(result?.evidence);
  if (!evidence.length) report.push("저장된 원문 발췌 근거가 없습니다.");
  evidence.forEach((item, index) => {
    report.push(`### 근거 ${index + 1}`, `위치: ${escapeText(analystFieldLabel(item.field))}`, `필드: ${escapeText(item.field)}`, "원문 발췌:",
      typeof item.excerpt === "string" ? fencedExcerpt(item.excerpt) : escapeText(item.excerpt),
      "분석 내용:");
    item.interpretations.forEach((interpretation) => report.push(escapeText(explanation(interpretation))));
  });
  if (analystItems(result?.conflicting_evidence).length) report.push("## 함께 고려할 정황", analystList(result?.conflicting_evidence));
  if (guidance.limitations.length) report.push("## 해석 시 주의할 점", textList(guidance.limitations));
  if (hasTuningContent(tuning)) report.push("## WAF 정책 검토", "WAF 설정을 자동 변경하지 않습니다.", table([
    ["제안 여부", tuning.recommended], ...[["범위", tuning.scope], ["제안", tuning.proposal_ko],
      ["변경 시 주의사항", tuning.risk_ko], ["적용 전 확인", tuning.validation_ko]].filter(([, value]) => analystText(value, "")),
  ]));

  report.push("## 인코딩·난독화 문자열");
  const decoded = visibleDecoding(decoding);
  if (!decoded) report.push("아직 원문 기반 디코딩 결과를 조회하지 않았습니다. HTTP 원문 탭을 열어 조회한 뒤 보고서에 원문과 변환 결과를 포함할 수 있습니다. 보고서가 원문을 자동 조회하지는 않습니다.");
  else {
    report.push("HTTP 원문 조회 시 계산한 해석 후보입니다. 당시 분석에 사용됐다는 뜻이 아니며, 애플리케이션이 실제로 같은 변환을 수행했거나 공격이 성립한다는 증거도 아닙니다.", table([
      ["디코더 버전", decoded.decoder_version], ["조회 범위 제한", decoded.scan_truncated], ["검사한 문자 수", decoded.scanned_chars], ["원문 문자 수", decoded.total_chars],
    ]));
    report.push("조회 표현식의 실행이나 외부 연결은 수행하지 않습니다. 일부 표현식은 문자열을 바꾸지 않고 정적 확인 안내만 제공합니다.");
    if (decoded.scan_truncated) report.push("크기·개수 등 도구 처리 한도로 일부 후보나 구간을 확인하지 못했습니다. 표시되지 않은 인코딩·조회 표현식이 있을 수 있습니다.");
    if (!decoded.items.length) report.push("조회 범위에서 표시할 디코딩 후보를 찾지 못했습니다. 인코딩 부재나 정상 요청을 보장하지 않습니다.");
    decoded.items.forEach((item, index) => {
      report.push(`### 문자열 ${index + 1}`, table([["원문 위치", analystFieldLabel(item.field)], ["원문 필드", item.field], ["시작 문자 위치 (0부터)", item.start], ["끝 문자 위치 (미포함)", item.end], ["처리 표시", decodingItemStatus(item)]]),
        "원본 문자열:", fencedExcerpt(item.original), !item.steps.length && item.original === item.decoded ? "변환 없이 보존한 문자열:" : "변환 결과:", fencedExcerpt(item.decoded));
      item.steps.forEach((step, stepIndex) => report.push(`### 후보 ${index + 1} · 변환 ${stepIndex + 1}`, table([["변환 방식", decodingEncodingText(step.encoding)]]),
        "변환 전:", fencedExcerpt(step.input), "변환 후:", fencedExcerpt(step.output)));
      if (item.warnings.length) report.push("해석 주의사항:", textList(item.warnings.map(decodingWarningText)));
    });
    if (decoded.warnings.length) report.push("조회 주의사항:", textList(decoded.warnings.map(decodingWarningText)));
  }

  report.push("## 소요 시간", table([
    ["접수 시각", detail.created_at], ["최초 처리 시작", detail.started_at], ["종료 시각", detail.completed_at],
    ["전체 경과 시간", duration(detail.total_elapsed_ms)], ["큐 대기 시간", duration(detail.queue_wait_ms)],
    ["처리 경과 시간", duration(detail.processing_duration_ms)],
  ]), "시간은 API가 제공한 조회 시점의 값입니다. 진행 중인 분석은 그 시점까지의 경과이며, 처리 시간에는 재시도와 작업 복구 대기가 포함될 수 있습니다. 단계 시간의 합계나 CPU 사용시간이 아닙니다.");

  if (!includeAppendix) return finishReport();
  const evaluation = isRecord(detail.evaluation) ? detail.evaluation : null;
  const reference = isRecord(evaluation?.reference_label) ? evaluation.reference_label : null;
  report.push("## 부록 · 참고 답안 평가", table([
    ["참고 답안", reference ? (referenceVerdicts[reference.verdict] || "미기록") : "답안 없음"],
    ["답안 비교 결과", evaluationOutcomeText(evaluation)],
    ["답안 출처", reference ? referenceSourceText(reference) : "해당 없음"],
    ["답안 출처 / 버전", reference?.source_ref ?? "해당 없음"],
    ["답안 작성 시 AI 결과 열람", reference ? referenceVisibilityText(reference.ai_visible) : "해당 없음"],
    ["답안 버전", reference?.revision ?? "해당 없음"],
    ["답안 연결 시각 (UTC)", reference?.created_at ?? "해당 없음"],
  ]), evaluationExplanation(evaluation), "이 기능에서 연결한 답안은 AI 입력이나 학습에 사용하지 않습니다. 기대 답안·AI 지원 판정은 검증된 운영 정답이 아니며, 리뷰 등록 상태는 답안 일치 여부와 별개입니다.");

  report.push("## 부록 · 독립 검증");
  if (!verifier) report.push("독립 검증 실행 정보가 저장되지 않았습니다. 이전 결과의 정보 부재를 미실행이나 성공으로 추정하지 않습니다.");
  else {
    const execution = verifier.executed === true ? "실행됨 (true)" : verifier.executed === false ? "미실행 (false)" : scalar(verifier.executed);
    const agreement = verifier.executed === false ? "해당 없음"
      : verifier.agreement === true ? "판정 일치 (true)"
        : verifier.agreement === false ? "판정 불일치 또는 검증 실패 (false)" : scalar(verifier.agreement);
    report.push(table([
      ["실행 여부", execution], ["판정 일치 여부", agreement],
      ["실패 ID", verifier.failure_id],
    ]), "### 실행 사유", textList(verifier.reasons));
  }

  report.push("## 부록 · 실행 정보", table([
    ["분석 ID", detail.id], ["이벤트 ID", detail.event_id], ["분석 출처", detail.source_system],
    ["분석 목적", named(detail.analysis_purpose, { production: "프로덕션", test: "테스트", legacy_unknown: "기존 미분류" })],
    ["유입 경로", named(detail.ingest_channel, { service_api: "서비스 API", file_upload: "배치 파일 분석", test_lab: "단건 분석", model_validation: "모델 검증", legacy_unknown: "기존 미분류" })],
    ["리뷰 상태", named(detail.review_state, { unreviewed: "미검토", confirmed: "리뷰 등록됨", deferred: "리뷰 보류" })],
    ["모델 자기평가 신뢰도", confidence(finalValue("confidence_score"))], ["모델 프로필", detail.model_profile],
    ["LLM 공급자", result?.agent?.llm_provider], ["모델 ID", result?.agent?.model_name],
    ["프롬프트 버전", detail.prompt_version], ["결과 계약", result?.schema_version], ["분석 실패 코드", detail.error_code],
  ]), "신뢰도는 모델의 자기평가값이며 보정된 확률이나 분석 정확도가 아닙니다. 리뷰 등록 여부는 AI 판정과 사람 판정의 일치를 의미하지 않습니다.");
  return finishReport();
}
