export const MAX_POLICY_CHARS = 4000;
export const PROMPT_REQUEST_TIMEOUT_MS = 30000;

export function promptCharacterCount(value = "") { let count = 0; for (const character of value) count += 1; return count; }

export function createPromptDraft(source = null) {
  return {
    name: source ? `${Array.from(source.name || "프롬프트").slice(0, 110).join("")} 복제` : "",
    policy_text: source?.policy_text || "",
    change_note: "",
    parent_version_id: source?.id || null,
    source_version_number: source?.version_number ?? null,
    source_policy_text: source?.policy_text || "",
  };
}

export function validatePromptDraft(draft, maximum = MAX_POLICY_CHARS) {
  const errors = [];
  for (const [field, label, limit] of [["name", "버전 이름", 120], ["change_note", "변경 설명", 1000], ["policy_text", "판정 지침", maximum]]) {
    const value = draft?.[field];
    if (typeof value !== "string" || !value.trim()) errors.push({ field, message: `${label}을 입력해 주세요.` });
    else if (promptCharacterCount(value) > limit) errors.push({ field, message: `${label}은 ${limit.toLocaleString()}자 이하여야 합니다.` });
    else if (Array.from(value).some((character) => /[\p{Cc}\p{Cf}\p{Cs}]/u.test(character) && !(field === "policy_text" && ["\n", "\t"].includes(character)))) errors.push({ field, message: `${label}에 허용되지 않는 제어 문자가 있습니다.${field === "policy_text" ? " 줄바꿈과 탭만 사용할 수 있습니다." : " 한 줄로 작성해 주세요."}` });
  }
  return errors;
}

export function promptDraftPayload(draft, maximum = MAX_POLICY_CHARS) {
  const errors = validatePromptDraft(draft, maximum);
  if (errors.length) throw new Error("invalid_prompt_draft");
  return {
    name: draft.name.trim(),
    change_note: draft.change_note.trim(),
    policy_text: draft.policy_text,
    ...(draft.parent_version_id ? { parent_version_id: draft.parent_version_id } : {}),
  };
}

export function promptActivationPayload(confirmation, acknowledged) {
  if (!confirmation?.id || !Number.isSafeInteger(confirmation.expected_revision) || confirmation.expected_revision < 1 || acknowledged !== true) {
    throw new Error("prompt_activation_confirmation_required");
  }
  return { expected_revision: confirmation.expected_revision, acknowledge_unverified: true };
}

export function promptErrorMessage(error, operation = "read") {
  if (error?.status === 422 && error?.message === "prompt_policy_context_budget_too_small") return "현재 운영 모델의 입력 공간이 부족합니다. 지침 길이나 모델의 입력·출력 한도를 확인해 주세요.";
  if (error?.status === 401) return "로그인이 만료되었습니다. 다시 로그인해 주세요.";
  if (error?.status === 403) return "프롬프트 설정은 관리자만 사용할 수 있습니다.";
  if (error?.status === 404) return "해당 버전을 찾을 수 없습니다. 목록을 새로고침해 주세요.";
  if (error?.status === 409) return operation === "activate"
    ? "다른 요청이 운영 버전을 먼저 변경했습니다. 최신 목록을 확인한 뒤 다시 선택하고 동의해 주세요."
    : "다른 요청이 새 버전을 먼저 저장했습니다. 최신 목록을 확인한 뒤 저장해 주세요.";
  if (error?.status === 422 || error?.status === 400) return "입력 형식이나 길이를 확인해 주세요. 저장된 버전은 수정할 수 없습니다.";
  if (operation !== "read") return "요청의 처리 결과를 확인하지 못했습니다. 중복 요청 전에 목록을 새로고침해 저장·운영 상태를 확인해 주세요.";
  return "프롬프트 정보를 불러오지 못했습니다. 새로고침으로 다시 확인해 주세요.";
}

// Exact line comparison only: never a model-based comparison or HTML renderer.
// Work/memory are bounded even for thousands of empty lines in a 4000-char policy.
export function promptLineDiff(before = "", after = "", maximumLines = 200) {
  if (before === after) return { changed: false, mode: "lines", rows: [] };
  if (before.length > MAX_POLICY_CHARS * 2 || after.length > MAX_POLICY_CHARS * 2 || promptCharacterCount(before) > MAX_POLICY_CHARS || promptCharacterCount(after) > MAX_POLICY_CHARS) return { changed: true, mode: "limit", rows: [] };
  const left = before.split("\n");
  const right = after.split("\n");
  if (left.length > maximumLines || right.length > maximumLines) return {
    changed: true, mode: "whole", rows: [
      { kind: "removed", text: before, oldLine: null, newLine: null },
      { kind: "added", text: after, oldLine: null, newLine: null },
    ],
  };
  const width = right.length + 1;
  const lengths = new Uint16Array((left.length + 1) * width);
  for (let i = left.length - 1; i >= 0; i -= 1) for (let j = right.length - 1; j >= 0; j -= 1) {
    lengths[i * width + j] = left[i] === right[j]
      ? lengths[(i + 1) * width + j + 1] + 1
      : Math.max(lengths[(i + 1) * width + j], lengths[i * width + j + 1]);
  }
  const rows = [];
  let i = 0; let j = 0;
  while (i < left.length || j < right.length) {
    if (i < left.length && j < right.length && left[i] === right[j]) {
      rows.push({ kind: "same", text: left[i], oldLine: i + 1, newLine: j + 1 }); i += 1; j += 1;
    } else if (i < left.length && (j === right.length || lengths[(i + 1) * width + j] >= lengths[i * width + j + 1])) {
      rows.push({ kind: "removed", text: left[i], oldLine: ++i, newLine: null });
    } else {
      rows.push({ kind: "added", text: right[j], oldLine: null, newLine: ++j });
    }
  }
  return { changed: true, mode: "lines", rows };
}

export function visiblePromptText(value = "") {
  return value.replace(/[\u0000-\u0008\u000b-\u001f\u007f]/g, (character) => character === "\r"
    ? "\\r" : `\\u${character.charCodeAt(0).toString(16).padStart(4, "0")}`);
}

export function emptyPromptSettingsState() {
  return {
    catalog: null, selectedId: "", selected: null, comparisonId: "", comparison: null,
    loading: false, selectedLoading: false, comparisonLoading: false,
    error: "", selectedError: "", comparisonError: "", notice: "", busy: "",
    confirmation: null, acknowledged: false, needsRefresh: false,
  };
}

export function createPromptSettingsController({ api, onChange, setTimer = setTimeout, clearTimer = clearTimeout }) {
  let state = emptyPromptSettingsState();
  let disposed = false;
  let catalogGeneration = 0; let selectionGeneration = 0; let comparisonGeneration = 0;
  const pending = new Set();
  const cache = new Map();
  const update = (values) => { if (!disposed) { state = { ...state, ...values }; onChange(state); } };
  const unavailable = () => disposed || !state.catalog || Boolean(state.busy) || state.loading || state.needsRefresh;

  async function request(call) {
    const controller = new AbortController();
    let timer;
    let onAbort;
    pending.add(controller);
    try {
      return await Promise.race([
        call({ signal: controller.signal }),
        new Promise((_, reject) => { onAbort = () => reject(new Error("prompt_request_aborted")); controller.signal.addEventListener("abort", onAbort, { once: true }); }),
        new Promise((_, reject) => { timer = setTimer(() => { controller.abort(); reject(new Error("prompt_request_timeout")); }, PROMPT_REQUEST_TIMEOUT_MS); }),
      ]);
    } finally { clearTimer(timer); if (onAbort) controller.signal.removeEventListener("abort", onAbort); pending.delete(controller); }
  }

  async function select(id) {
    if (disposed || state.busy) return;
    const generation = ++selectionGeneration;
    update({ selectedId: id, selected: cache.get(id) || null, selectedError: "", selectedLoading: Boolean(id && !cache.has(id)), confirmation: null, acknowledged: false });
    if (!id || cache.has(id)) return;
    try {
      const detail = await request((options) => api.promptPolicy(id, options));
      if (disposed || generation !== selectionGeneration) return;
      cache.set(id, detail); update({ selected: detail });
    } catch (error) { if (generation === selectionGeneration) update({ selectedError: promptErrorMessage(error) }); }
    finally { if (generation === selectionGeneration) update({ selectedLoading: false }); }
  }

  async function compare(id) {
    const generation = ++comparisonGeneration;
    update({ comparisonId: id, comparison: cache.get(id) || null, comparisonError: "", comparisonLoading: Boolean(id && !cache.has(id)) });
    if (disposed || !id || cache.has(id)) return;
    try {
      const detail = await request((options) => api.promptPolicy(id, options));
      if (disposed || generation !== comparisonGeneration) return;
      cache.set(id, detail); update({ comparison: detail });
    } catch (error) { if (generation === comparisonGeneration) update({ comparisonError: promptErrorMessage(error) }); }
    finally { if (generation === comparisonGeneration) update({ comparisonLoading: false }); }
  }

  async function refresh(preferredId = state.selectedId) {
    if (disposed || state.busy) return false;
    const generation = ++catalogGeneration;
    update({ loading: true, error: "", confirmation: null, acknowledged: false });
    try {
      const catalog = await request((options) => api.promptPolicies(options));
      if (disposed || generation !== catalogGeneration) return false;
      update({ catalog, needsRefresh: false, loading: false });
      const id = catalog.items.some((item) => item.id === preferredId) ? preferredId : catalog.active_version_id || catalog.items[0]?.id || "";
      await select(id);
      return true;
    } catch (error) {
      if (generation === catalogGeneration) update({ error: promptErrorMessage(error), needsRefresh: true });
      return false;
    } finally { if (generation === catalogGeneration) update({ loading: false }); }
  }

  async function save(draft) {
    if (unavailable()) return false;
    const errors = validatePromptDraft(draft, state.catalog?.max_policy_chars || MAX_POLICY_CHARS);
    if (errors.length) { update({ error: errors[0].message }); return false; }
    update({ busy: "save", error: "", notice: "", confirmation: null, acknowledged: false });
    try {
      const detail = await request((options) => api.createPromptPolicy(promptDraftPayload(draft, state.catalog?.max_policy_chars || MAX_POLICY_CHARS), options));
      if (disposed) return false;
      cache.set(detail.id, detail);
      update({ busy: "", selectedId: detail.id, selected: detail, notice: `v${detail.version_number}을 저장했습니다. 운영 적용은 별도이며 모델 품질은 미검증입니다.` });
      await refresh(detail.id);
      return true;
    } catch (error) {
      update({ busy: "", error: promptErrorMessage(error, "save"), needsRefresh: ![400, 422].includes(error?.status) });
      return false;
    }
  }

  function beginActivation() {
    if (unavailable() || !state.selected || state.selected.id === state.catalog?.active_version_id) return;
    update({ confirmation: {
      id: state.selected.id, name: state.selected.name, version_number: state.selected.version_number,
      expected_revision: state.catalog.revision, from_id: state.catalog.active_version_id,
    }, acknowledged: false, error: "", notice: "" });
  }

  async function activate() {
    if (unavailable() || !state.confirmation || !state.acknowledged) return false;
    const target = state.confirmation;
    const body = promptActivationPayload(target, state.acknowledged);
    update({ busy: "activate", error: "", notice: "" });
    try {
      const result = await request((options) => api.activatePromptPolicy(target.id, body, options));
      if (disposed) return false;
      update({ busy: "", confirmation: null, acknowledged: false, catalog: { ...state.catalog, ...result }, notice: `운영 프롬프트를 v${target.version_number}으로 변경했습니다. 기존 분석 결과는 그대로 유지됩니다.` });
      await refresh(target.id);
      return true;
    } catch (error) {
      update({ busy: "", confirmation: null, acknowledged: false, error: promptErrorMessage(error, "activate"), needsRefresh: ![400, 422].includes(error?.status) });
      // Re-read only: never retry a state-changing request automatically.
      if (error?.status === 409) {
        await refresh(target.id);
        update({ error: promptErrorMessage(error, "activate") });
      }
      return false;
    }
  }

  return {
    refresh, select, compare, save, beginActivation, activate,
    acknowledge(value) { if (state.confirmation && !state.busy) update({ acknowledged: value === true }); },
    cancelActivation() { if (!state.busy) update({ confirmation: null, acknowledged: false }); },
    dispose() { disposed = true; catalogGeneration += 1; selectionGeneration += 1; comparisonGeneration += 1; for (const controller of pending) controller.abort(); },
  };
}
