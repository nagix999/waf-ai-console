import { internalEgressError, vllmTargetError } from "./internalEgress.js";

export const OPENAI_BASE_URL = "https://api.openai.com/v1";
export const EXTERNAL_DATA_APPROVAL = "payload·Cookie 등 마스킹하지 않은 분석 입력이 OpenAI로 전송되며 API 비용이 발생할 수 있음을 확인하고 승인합니다.";

export function providerOf(profile) {
  return profile?.provider || "vllm";
}

export function providerLabel(profile) {
  const provider = providerOf(profile);
  return provider === "openai" ? "OpenAI" : provider === "vllm" ? "vLLM" : "알 수 없는 공급자";
}

export function modelProfileError(error) {
  const assignmentErrors = {
    production_profile_is_immutable: "Production으로 지정된 프로필은 수정할 수 없습니다. 다른 프로필로 교체하거나 비활성화 후 수정하세요.",
    test_profile_is_immutable: "Test로 지정된 프로필은 수정할 수 없습니다. Test 지정 해제 후 수정하세요.",
    matching_full_test_required: "현재 설정과 일치하는 전체 검증 통과 이력이 필요합니다. 빠른 테스트만으로는 지정할 수 없습니다.",
    model_profile_changed_reconfirm: "프로필 설정이 변경되었습니다. 최신 목록을 조회하고 지정 대상을 다시 확인하세요.",
    test_model_profile_required: "Test 모델이 지정되지 않았습니다. 전체 검증을 통과한 프로필을 Test로 지정하세요.",
  };
  if (Object.hasOwn(assignmentErrors, error?.message)) return assignmentErrors[error.message];
  return ["vllm_target_not_allowed", "vllm_base_url_must_use_ip_address", "internal_egress_ip_must_be_private"].includes(error?.message)
    ? internalEgressError(error) : error?.message || "프로필 요청을 처리하지 못했습니다.";
}

export function newProfileForm() {
  return {
    name: "", provider: "vllm", base_url: "",
    model_name: "google/gemma-4-26B-A4B-it", api_key: "",
    timeout_seconds: 120, context_window: 32768, max_output_tokens: 3072,
    test_concurrency: 3, tls_verify: true, external_data_approved: false,
    key_reset_required: false,
  };
}

// Keep only editable settings; never populate credentials from an API response.
export function editProfileForm(profile) {
  const form = newProfileForm();
  for (const field of ["name", "base_url", "model_name", "timeout_seconds", "context_window", "max_output_tokens", "test_concurrency", "tls_verify"]) {
    if (profile[field] !== undefined) form[field] = profile[field];
  }
  form.provider = providerOf(profile);
  form.external_data_approved = form.provider === "openai" && profile.external_data_approved === true;
  if (form.provider === "openai") { form.base_url = OPENAI_BASE_URL; form.tls_verify = true; }
  return form;
}

export function changeProfileProvider(form, provider) {
  if (!["vllm", "openai"].includes(provider) || provider === form.provider) return form;
  const defaults = newProfileForm();
  return {
    ...form, provider, base_url: provider === "openai" ? OPENAI_BASE_URL : defaults.base_url,
    model_name: provider === "openai" ? "" : defaults.model_name,
    api_key: "", tls_verify: true, external_data_approved: false, key_reset_required: true,
  };
}

export function profileRequiresKey(form, originalProfile) {
  return form.provider === "openai" && (!originalProfile || providerOf(originalProfile) !== form.provider
    || !originalProfile.has_api_key || form.key_reset_required);
}

export function validateProfileForm(form, originalProfile, internalTargets) {
  if (!["vllm", "openai"].includes(form.provider)) return "LLM 공급자를 선택하세요.";
  if (!form.name.trim() || !form.model_name.trim()) return "프로필 이름과 모델 이름을 입력하세요.";
  if (!/^[A-Za-z0-9][A-Za-z0-9_.-]*$/.test(form.name)) return "프로필 이름은 영문·숫자로 시작하고 영문·숫자·밑줄·점·하이픈만 사용할 수 있습니다.";
  if (form.provider === "openai" && !form.external_data_approved) return "OpenAI 외부 전송 및 API 비용 안내를 확인하고 승인하세요.";
  if (profileRequiresKey(form, originalProfile) && !form.api_key.trim()) return "이 OpenAI 프로필에 사용할 새 API Key를 입력하세요.";
  if (form.api_key && !form.api_key.trim()) return "API Key에는 공백만 입력할 수 없습니다.";
  if (Number(form.max_output_tokens) >= Number(form.context_window)) return "최대 출력 토큰은 Context window보다 작아야 합니다.";
  if (form.provider === "vllm") return vllmTargetError(form.base_url, internalTargets);
  return "";
}

export function profilePayload(form, originalProfile) {
  const openai = form.provider === "openai";
  const payload = {
    name: form.name, provider: form.provider, base_url: openai ? OPENAI_BASE_URL : form.base_url,
    model_name: form.model_name, api_key: form.api_key,
    timeout_seconds: Number(form.timeout_seconds), context_window: Number(form.context_window),
    max_output_tokens: Number(form.max_output_tokens), test_concurrency: Number(form.test_concurrency),
    tls_verify: openai ? true : form.tls_verify, external_data_approved: openai && form.external_data_approved === true,
  };
  if (originalProfile && providerOf(originalProfile) === form.provider && !form.key_reset_required && !form.api_key) delete payload.api_key;
  return payload;
}
