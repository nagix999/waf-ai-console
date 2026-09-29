// Display-only semantics. Never rewrite model output or infer a missing fact.
export const severityMeaning = ["잠재 영향 기준 · 실제 공격 성공 여부와 별도", "Potential impact · separate from actual attack success"];
export const disagreementNotice = ["자동 분석 해석이 서로 달랐습니다.", "The automated analyses reached different interpretations."];
export const legacySignatureNotice = ["관계 설명은 기록된 관계값을 기준으로 표시합니다. 기존 분석 설명은 검증된 결론이 아닙니다.", "The summary follows the recorded relation. The original explanation is not a verified conclusion."];
const relations = {
  exact: ["탐지 설명과 요청의 핵심 동작이 일치합니다.", "The detection description matches the request's core behavior."],
  partial: ["일부는 일치하지만 중요한 차이가 있습니다.", "Some aspects match, but important differences remain."],
  mismatch: ["탐지 설명과 요청의 핵심 동작이 다릅니다.", "The detection description differs from the request's core behavior."],
  unknown: ["현재 입력만으로 관계를 확인하기 어렵습니다.", "The available input does not establish the relationship."],
};
export const signatureSummary = relation => Object.hasOwn(relations, relation) ? relations[relation] : ["관계가 기록되지 않았습니다.", "No relation was recorded."];
export function verifierDisagreed(detail) {
  const result = detail?.result;
  return detail?.status === "completed" && result?.verdict === "inconclusive"
    && Array.isArray(result.diagnostics?.inconclusive_reasons)
    && result.diagnostics.inconclusive_reasons.includes("verdict_disagreement");
}
export const wafObservation = action => ({
  D: ["이 요청은 WAF에서 차단으로 기록됐습니다. 우회·반복 시도나 실제 영향은 별도로 확인합니다.", "WAF recorded this request as denied. Bypass, repeat attempts and actual impact are separate checks."],
  A: ["이 요청은 WAF에서 허용으로 기록됐습니다. 애플리케이션 처리나 공격 성공을 뜻하지는 않습니다.", "WAF recorded this request as allowed. This does not establish application processing or attack success."],
})[action] || null;
export function evidenceSections(verdict) {
  const attack = { side: "true_positive", label: ["정탐 근거", "Attack evidence"] };
  const benign = { side: "false_positive", label: ["오탐 근거", "Benign evidence"] };
  const opposite = ["반대 근거", "Counter-evidence"];
  const leading = verdict === "false_positive" ? [benign, { ...attack, label: opposite }]
    : verdict === "true_positive" ? [attack, { ...benign, label: opposite }]
      : verdict === "inconclusive" ? [{ ...attack, label: ["공격 해석", "Attack interpretation"] }, { ...benign, label: ["정상 해석", "Benign interpretation"] }]
        : [attack, benign];
  return [...leading, { side: "context", label: ["참고 내용", "Context"] }, { side: "unclassified", label: ["구분 미기록", "Unclassified"] }];
}
