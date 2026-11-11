// Fabricated data for local tests and the isolated browser harness only.
export function relationFixture() {
  const evidence = (id, role, index, field, excerpt, supports, interpretation_ko) => ({ evidence_id: id, field, excerpt, supports, interpretation_ko,
    origins: [{ role, index, source_id: `c${index + 1}` }], source_spans: [] });
  return {
    schema_version: "waf-analysis-v3", verdict: "inconclusive", summary_ko: "입력값의 사용 문맥을 검토했습니다.",
    evidence: [
      evidence("e1", "primary", 0, "payload.request_line", "POST /api/entry HTTP/1.1", "context", "요청 대상을 보여줍니다."),
      evidence("e2", "primary", 1, "payload.body", "<img src=x onerror=alert(1)>", "attack_request", "입력값에 이벤트 실행 구문을 혼입한 시도로 해석합니다."),
      evidence("e3", "verifier", 0, "payload.request_line", "POST /api/entry HTTP/1.1", "context", "요청 대상을 보여줍니다."),
      evidence("e4", "verifier", 1, "payload.body", "<img src=x onerror=alert(1)>", "normal_request", "코드를 문자열로 전달하는 요청으로 해석했습니다."),
    ],
    decision_issues: [{ point_ko: "입력값의 쓰임이 판정을 가릅니다.", evidence_ids: ["e2", "e4"], missing_condition_ko: "입력값의 사용 방식을 확인하세요." }],
  };
}
export const relationMapping = () => ({ version: "evidence-relations-v1", groups: [
  { role: "primary", representative_evidence_id: "e2", related_evidence_ids: ["e1"] },
  { role: "verifier", representative_evidence_id: "e4", related_evidence_ids: ["e3"] },
] });

export function sharedRelationFixture() {
  const result = relationFixture();
  result.evidence = result.evidence.slice(0, 2).map((item, index) => ({ ...item,
    origins: [...item.origins, { role: "verifier", index, source_id: `c${index + 1}` }],
  }));
  result.decision_issues = [];
  const mapping = { version: "evidence-relations-v1", groups: ["primary", "verifier"].map(role => ({
    role, representative_evidence_id: "e2", related_evidence_ids: ["e1"],
  })) };
  return { result, mapping };
}
