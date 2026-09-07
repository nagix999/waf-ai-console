import { providerOf } from "./llmProfiles.js";

const fingerprintValid = value => typeof value === "string" && /^[a-f0-9]{64}$/.test(value);
export const roleAssigned = profile => profile?.status === "production" || profile?.is_test === true;
export function modelAssignmentPayload(fingerprint) {
  if (!fingerprintValid(fingerprint)) throw new Error("invalid_model_profile_fingerprint");
  return { expected_profile_fingerprint: fingerprint };
}

export function assignmentBlockReason(profile, { loading = false, error = "", targetError = "", active = false } = {}) {
  if (loading) return "프로필 지정 상태를 확인하는 중입니다.";
  if (error) return "최신 지정 상태를 조회하지 못했습니다. 새로고침 후 다시 확인하세요.";
  if (profile?.status === "disabled") return "비활성 프로필입니다. 활성화하고 전체 검증 상태를 확인하세요.";
  if (active) return "프로필 검증이 진행 중입니다. 검증 종료 후 지정하세요.";
  if (targetError) return targetError;
  if (providerOf(profile) === "openai" && !profile?.external_data_approved) return "OpenAI 외부 전송 승인이 필요합니다.";
  if (!fingerprintValid(profile?.profile_fingerprint)) return "설정 식별 정보를 확인할 수 없습니다. 목록을 다시 조회하세요.";
  if (profile?.can_assign !== true) return profile?.assignment_block_reason === "profile_not_verified"
    ? "현재 프로필이 검증되지 않았습니다. 전체 검증을 먼저 통과해야 합니다."
    : "현재 설정과 일치하는 전체 검증 통과 이력이 필요합니다. 빠른 테스트만으로 지정할 수 없습니다.";
  return "";
}

export function testModelAvailability({ profiles, loading, error, agentMode }) {
  if (agentMode === "stub") return { blocked: "", profile: null, message: "명시적인 stub 모드입니다. Test 모델 지정 여부와 관계없이 모의 분석하며 실제 LLM은 호출하지 않습니다." };
  if (loading) return { blocked: "Test 모델 지정 상태를 확인하는 중입니다.", profile: null };
  if (error || !Array.isArray(profiles)) return { blocked: "Test 모델 지정 상태를 조회하지 못했습니다. 새로고침으로 다시 확인하세요.", profile: null };
  const candidates = profiles.filter(profile => profile.is_test === true);
  if (candidates.length !== 1) return { blocked: candidates.length ? "Test 모델 지정 정보가 올바르지 않습니다. 설정을 확인하세요." : "Test 모델이 지정되지 않았습니다. 설정 → LLM 프로필에서 전체 검증을 통과한 프로필을 Test로 지정하세요. Production으로 자동 대체하지 않습니다.", profile: null };
  const profile = candidates[0];
  return { blocked: assignmentBlockReason(profile), profile, message: "접수 당시 Test 모델과 프롬프트를 고정합니다. 지정 변경은 새 테스트에만 적용하며 Production 모델은 바꾸지 않습니다." };
}

export const emptyAssignmentState = () => ({ target: null, busy: false, error: "", needsReconfirm: false });

export function createAssignmentController({ api, onChange, onCommitted, onRefresh }) {
  let state = emptyAssignmentState(); let disposed = false;
  const publish = patch => { if (!disposed) { state = { ...state, ...patch }; onChange(state); } };
  function open(profile, action, profiles = []) {
    if (disposed || state.busy || !["production", "test", "unassign_test", "disable"].includes(action)) return;
    if (["production", "test"].includes(action) && assignmentBlockReason(profile)) return;
    if (action === "unassign_test" && profile.is_test !== true) return;
    const previous = profiles.find(item => action === "test" ? item.is_test === true : action === "production" ? item.status === "production" : false);
    publish({ target: { id: profile.id, name: profile.name, model_name: profile.model_name, provider: providerOf(profile), profile_fingerprint: profile.profile_fingerprint, is_test: profile.is_test === true, is_production: profile.status === "production", action, previous_name: previous?.id !== profile.id ? previous?.name : null }, error: "", needsReconfirm: false });
  }
  async function submit() {
    if (disposed || state.busy || !state.target || state.needsReconfirm) return false;
    const target = state.target;
    if (target.action !== "disable" && !fingerprintValid(target.profile_fingerprint)) { publish({ error: "설정 식별 정보가 없습니다. 취소 후 새로고침하세요.", needsReconfirm: true }); return false; }
    publish({ busy: true, error: "" });
    try {
      const actions = { production: () => api.promoteModelProfile(target.id, target.profile_fingerprint), test: () => api.assignTestModelProfile(target.id, target.profile_fingerprint), unassign_test: () => api.unassignTestModelProfile(target.id, target.profile_fingerprint), disable: () => api.disableModelProfile(target.id) };
      await actions[target.action](); if (disposed) return false;
      publish({ target: null, busy: false }); onCommitted?.(target); return true;
    } catch (error) {
      if (!disposed) {
        const messages = { model_profile_changed_reconfirm: "확인 후 프로필 설정이 변경되어 적용하지 않았습니다. 취소 후 최신 프로필을 다시 확인하세요.", matching_full_test_required: "현재 설정의 전체 검증 통과 이력이 없습니다. 다시 검증한 뒤 지정하세요.", test_profile_not_assigned: "Test 지정 상태가 변경되었습니다. 최신 목록을 확인하세요.", profile_not_verified: "현재 프로필은 지정할 수 없는 상태입니다. 전체 검증과 활성 상태를 확인하세요.", vllm_target_not_allowed: "내부 대상 허용 설정이 변경되었습니다. Internal Egress를 확인하세요." };
        publish({ busy: false, needsReconfirm: true, error: Object.hasOwn(messages, error?.message) ? messages[error.message] : error?.status ? "지정을 적용하지 못했습니다. 취소 후 최신 상태를 확인하세요." : "응답을 확인하지 못했습니다. 자동 재요청하지 않습니다. 취소 후 현재 지정 상태를 확인하세요." }); onRefresh?.();
      }
      return false;
    }
  }
  return { open, submit, close() { if (!state.busy) publish(emptyAssignmentState()); }, getState: () => state, dispose() { disposed = true; } };
}
