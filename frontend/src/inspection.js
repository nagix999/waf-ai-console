// Display helpers only. Never decode, execute, persist or send inspected text.
export function textMatches(text, query, limit = 200) {
  if (typeof text !== "string" || !query) return { positions: [], limited: false };
  const positions = [];
  let from = 0;
  while (positions.length <= limit) {
    const at = text.indexOf(query, from);
    if (at < 0) break;
    positions.push(at);
    from = at + Math.max(query.length, 1);
  }
  return { positions: positions.slice(0, limit), limited: positions.length > limit };
}

const steps = {
  input: "이벤트 접수", parser: "HTTP 구조 확인", decoder: "문자열 변환 확인",
  agent_input: "분석 입력 준비", primary: "위협 분석", verifier: "추가 검증",
  llm_primary: "위협 분석", llm_verifier: "추가 검증", finalize: "최종 판정 정리",
  final: "최종 판정 정리", policy: "추가 검증 조건 확인", result: "결과 저장",
};
export const stepLabel = name => steps[name] || name || "이름 없는 단계";
export const recordText = value => typeof value === "string" ? value : value == null ? "" : JSON.stringify(value, null, 2);

export function loginError(error) {
  if (error?.message === "invalid_credentials" || error?.status === 401) return "아이디와 비밀번호를 확인하세요.";
  if (error?.status === 429) return "로그인 시도가 많습니다. 잠시 후 다시 시도하세요.";
  return "로그인 서버에 연결하지 못했습니다. 잠시 후 다시 시도하세요.";
}
