export const emptyAgentSettingsState = () => ({ catalog: null, draft: null, loading: true, busy: false, error: "", notice: "", needsRefresh: false });
export function validAgentCatalog(value) {
  return Boolean(value && /^[a-f0-9]{64}$/.test(value.state_token) && Array.isArray(value.profiles)
    && ["production", "test"].every(purpose => value.assignments?.[purpose]
      && ["primary_profile_id", "verifier_profile_id"].every(role => value.assignments[purpose][role] === null
        || typeof value.assignments[purpose][role] === "string")));
}
export function selectionIssue(catalog, draft) {
  if (!validAgentCatalog(catalog) || !draft) return "설정을 먼저 조회하세요.";
  for (const purpose of ["test"]) {
    const roles = draft[purpose];
    if ((roles.verifier_profile_id || roles.evidence_editor_enabled) && !roles.primary_profile_id) return "Primary 모델을 먼저 지정하세요.";
    for (const id of [roles.primary_profile_id, roles.verifier_profile_id, roles.evidence_editor_profile_id]) {
      if (id && !catalog.profiles.some(profile => profile.id === id && profile.can_assign === true)) return "현재 설정으로 전체 검증을 통과한 모델만 지정할 수 있습니다.";
    }
  }
  return "";
}
export function hasExternalRole(catalog, draft) {
  return draft?.test && [draft.test.primary_profile_id, draft.test.verifier_profile_id, draft.test.evidence_editor_enabled ? draft.test.evidence_editor_profile_id : null].some(id => catalog?.profiles.some(p => p.id === id && p.provider === "openai"));
}
export function createAgentSettingsController({ api, onChange, onCommitted }) {
  let state = emptyAgentSettingsState(); let disposed = false; let sequence = 0;
  const publish = patch => { if (!disposed) { state = { ...state, ...patch }; onChange(state); } };
  async function refresh() {
    if (disposed || state.busy) return;
    const ticket = ++sequence; publish({ loading: true, error: "", notice: "" });
    try {
      const catalog = await api.agentSettings();
      if (ticket !== sequence || disposed) return;
      if (!validAgentCatalog(catalog)) throw new Error("invalid_agent_configuration_response");
      publish({ catalog, draft: structuredClone(catalog.assignments), loading: false, needsRefresh: false });
    } catch { if (ticket === sequence) publish({ loading: false, needsRefresh: true, error: "Agent 설정을 불러오지 못했습니다. 새로고침해 주세요." }); }
  }
  async function save(acknowledged) {
    if (disposed || state.busy || state.loading || state.needsRefresh || selectionIssue(state.catalog, state.draft)) return false;
    if (hasExternalRole(state.catalog, state.draft) && acknowledged !== true) return false;
    publish({ busy: true, error: "", notice: "" });
    try {
      const catalog = await api.updateAgentSettings({ expected_state_token: state.catalog.state_token,
        test: state.draft.test, external_transfer_acknowledged: acknowledged === true });
      if (disposed) return false;
      if (!validAgentCatalog(catalog)) throw new Error("invalid_agent_configuration_response");
      publish({ catalog, draft: structuredClone(catalog.assignments), busy: false, notice: "모델 배정을 저장했습니다. LLM은 호출하지 않았습니다." });
      onCommitted?.(catalog); return true;
    } catch (error) {
      publish({ busy: false, needsRefresh: true, error: error.message === "agent_configuration_changed"
        ? "다른 화면에서 설정이 바뀌었습니다. 새로고침 후 다시 확인하세요."
        : error.message === "agent_context_budget_too_small" ? "공통 지침과 재시도를 위한 입력 공간이 부족합니다. 모델 입력·출력 한도나 지침 길이를 확인하세요."
        : error.status ? "설정을 적용하지 못했습니다. 모델 검증·연결 허용 상태를 확인하고 새로고침하세요."
        : "적용 응답을 확인하지 못했습니다. 자동 재요청하지 않습니다. 새로고침으로 현재 배정을 확인하세요." });
      return false;
    }
  }
  return { refresh, save, getState: () => state, dispose() { disposed = true; sequence++; },
    change(purpose, role, id) {
      if (state.busy || state.loading || state.needsRefresh || !state.draft
        || purpose !== "test" || !["primary_profile_id", "verifier_profile_id", "evidence_editor_profile_id", "evidence_editor_enabled"].includes(role)) return;
      publish({ draft: { ...state.draft, [purpose]: { ...state.draft[purpose], [role]: role === "evidence_editor_enabled" ? id === true : id || null,
        ...(role === "evidence_editor_enabled" && id !== true ? { evidence_editor_profile_id: null } : {}) } }, notice: "", error: "" });
    } };
}

export const diagnosticLabels = {
  primary_evidence_rejected: "1차 판정의 원문 근거 확인 실패",
  verifier_evidence_rejected: "추가 검증의 원문 근거 확인 실패",
  primary_model_inconclusive: "1차 모델의 판단 보류",
  verifier_model_inconclusive: "검증 모델의 판단 보류",
  verifier_failed: "추가 검증 실행 실패",
  verdict_disagreement: "두 판정의 불일치",
  input_integrity_limited: "근거 구간의 누락·구조 문제",
};
