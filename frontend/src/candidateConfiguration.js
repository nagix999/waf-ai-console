export function defaultCandidate(agents, prompts, schemas) {
  const roles = agents?.assignments?.test;
  if (!roles || !Array.isArray(agents.profiles) || !Array.isArray(prompts?.items) || !Array.isArray(schemas?.items)) throw new Error("invalid_candidate_catalog");
  return { primary_profile_id: roles.primary_profile_id || "", verifier_profile_id: roles.verifier_profile_id || null,
    evidence_editor_enabled: roles.evidence_editor_enabled === true,
    evidence_editor_profile_id: roles.evidence_editor_enabled ? roles.evidence_editor_profile_id || null : null,
    prompt_policy_version_id: prompts.active_version_id || "", input_schema_version_id: schemas.active_version_id || "" };
}

export function candidateIssue(catalog, value) {
  if (!catalog || !value) return "실행 구성을 불러오는 중입니다.";
  const profiles = catalog.agents.profiles;
  if (!value.primary_profile_id) return "구성 변경에서 1차 판정 모델을 선택하세요.";
  const ids = [value.primary_profile_id, value.verifier_profile_id || value.primary_profile_id,
    ...(value.evidence_editor_enabled ? [value.evidence_editor_profile_id || value.primary_profile_id] : [])];
  if (ids.some(id => !profiles.some(profile => profile.id === id && profile.can_assign === true))) return "현재 설정으로 전체 검증을 통과한 모델을 선택하세요.";
  if (!catalog.prompts.items.some(item => item.id === value.prompt_policy_version_id)
      || !catalog.schemas.items.some(item => item.id === value.input_schema_version_id)) return "저장된 지침과 입력 스키마 버전을 선택하세요.";
  return "";
}

export function candidateUsesExternal(catalog, value) {
  const ids = [value?.primary_profile_id, value?.verifier_profile_id, value?.evidence_editor_enabled ? value?.evidence_editor_profile_id : null];
  return catalog?.agents.profiles.some(profile => ids.includes(profile.id) && profile.provider === "openai") === true;
}

export function officialComparisonAllowed(data) {
  if (data?.comparable === false) return false;
  if (![data?.baseline, data?.candidate].some(run => run?.evaluation_mode === "ground_truth")) return true;
  const [left, right] = [data.baseline, data.candidate];
  if (left?.ground_truth?.published || right?.ground_truth?.published) {
    return left?.evaluation_mode === "ground_truth" && right?.evaluation_mode === "ground_truth"
      && left.ground_truth?.published === true && right.ground_truth?.published === true
      && Boolean(left.ground_truth.comparison_key) && left.ground_truth.comparison_key === right.ground_truth.comparison_key;
  }
  return left?.evaluation_mode === "ground_truth" && right?.evaluation_mode === "ground_truth"
    && Boolean(left.dataset_version_id) && left.dataset_version_id === right.dataset_version_id
    && Boolean(left.ground_truth?.membership_hash) && left.ground_truth.membership_hash === right.ground_truth?.membership_hash
    && Boolean(left.ground_truth?.metrics_version) && left.ground_truth.metrics_version === right.ground_truth?.metrics_version;
}
