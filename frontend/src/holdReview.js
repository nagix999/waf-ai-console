// Read-only parity with backend/app/services/hold_review.py. No role outputs,
// payload reparsing, inferred environment facts or invented missing conditions.
import { assessmentView } from "./assessmentData.js";

function location(field) {
  field = field.startsWith("event.") ? field.slice(6) : field;
  if (["payload", "raw_payload"].includes(field)) return "HTTP 원문";
  if (field.startsWith("extra_fields.")) return "추가 입력값";
  for (const [key, label] of [["body", "요청 본문"], ["query", "요청 파라미터"], ["headers", "HTTP 헤더"], ["uri", "요청 주소"], ["request_target", "요청 주소"], ["path", "요청 경로"]]) {
    if (field === `payload.${key}` || field.startsWith(`payload.${key}.`)) return label;
  }
  return null;
}

export function holdReview(detail, decision) {
  const result = detail?.result ?? {};
  if (!decision || detail?.status !== "completed" || result.verdict !== "inconclusive"
    || !["assessment_pending", "model_abstained", "reason_unrecorded", "input_limited"].includes(decision.code)) return { issues: [], checks: [] };
  const view = assessmentView(result);
  if (!view) return { issues: [], checks: [] };
  let issues = [...view.issues];
  if (!issues.length && decision.code === "assessment_pending") {
    const groups = new Map();
    for (const item of view.evidence) {
      const label = location(item.field);
      if (label) {
        if (!groups.has(label)) groups.set(label, []);
        groups.get(label).push(item.number);
      }
    }
    issues = [...groups.entries()].slice(0, 3).map(([label, numbers]) => ({
      point_ko: `${label}에 있는 값을 공격 구문으로 볼지 정상 데이터로 볼지 검토해야 합니다.`,
      evidence_numbers: numbers, missing_condition_ko: null,
    }));
  }
  const checks = [], seen = new Set();
  if (decision.code !== "input_limited") {
    for (const issue of issues) {
      const condition = issue.missing_condition_ko;
      if (!condition || seen.has(condition)) continue;
      seen.add(condition);
      checks.push({ source_ko: "해당 기능의 담당자 또는 입력·처리 규격", check_ko: condition,
        why_ko: `관련 근거 ${issue.evidence_numbers.join(" · ")}의 해석을 구분하기 위해 확인할 조건입니다.` });
      if (checks.length === 3) break;
    }
    if (!checks.length && issues.length) {
      const numbers = [...new Set(issues.flatMap(issue => issue.evidence_numbers))].join(" · ");
      checks.push({ source_ko: "저장된 HTTP 원문과 판정 근거",
        check_ko: `근거 ${numbers}의 발췌와 판단 이유를 대조해 공격·정상 해석 중 어느 쪽이 요청 문맥에 맞는지 검토하세요.`,
        why_ko: "추가 자료가 부족하다고 확인된 것은 아닙니다. 먼저 기록된 근거의 해석을 검토해야 합니다." });
    }
  }
  return { issues, checks };
}
