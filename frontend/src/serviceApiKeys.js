const knownScopes = ["ingest", "review"];
const forbiddenCharacters = /[\p{Cc}\p{Cf}\p{Cs}]/u;

export function serviceKeyNameError(name) {
  if (typeof name !== "string" || !name.trim() || Array.from(name.trim()).length > 120 || forbiddenCharacters.test(name)) return "키 이름은 제어문자 없이 1~120자로 입력하세요.";
  return "";
}

export function validateServiceKeyDraft(draft) {
  const nameError = serviceKeyNameError(draft.name);
  if (nameError) return nameError;
  if (!/^[A-Za-z0-9][A-Za-z0-9._-]{0,119}$/.test(draft.source_system) || draft.source_system.toLowerCase() === "admin-ui" || draft.source_system.toLowerCase().startsWith("waf-internal-")) return "Source System은 1~120자의 영문·숫자·._-로 입력하세요. admin-ui와 waf-internal-로 시작하는 값은 대소문자와 관계없이 예약되어 있습니다.";
  if (!Array.isArray(draft.scopes) || !draft.scopes.length || draft.scopes.some((scope) => !knownScopes.includes(scope)) || new Set(draft.scopes).size !== draft.scopes.length) return "ingest(접수·조회), review(리뷰 등록) 중 필요한 권한을 하나 이상 선택하세요.";
  return "";
}

// Never copy response fields such as api_key into list/edit state.
export function serviceKeyMetadata(item) {
  const value = {};
  for (const field of ["id", "name", "key_prefix", "source_system", "created_at", "last_used_at", "revoked_at"]) value[field] = item[field] ?? null;
  value.scopes = Array.isArray(item.scopes) ? item.scopes.filter(scope => knownScopes.includes(scope)) : [];
  return value;
}

export function serviceKeyError(error, { issuing = false } = {}) {
  const messages = {
    service_api_key_name_exists: "같은 이름의 API Key가 이미 있습니다. 목록을 확인하고 다른 이름을 사용하세요.",
    service_api_key_not_found: "이 API Key를 찾을 수 없습니다. 목록을 새로고침해 주세요.",
    service_api_key_revoked: "이미 폐기된 API Key는 이름을 변경하거나 다시 활성화할 수 없습니다.",
  };
  if (Object.hasOwn(messages, error?.message)) return messages[error.message];
  if (error?.status === 401) return "로그인 세션을 확인하고 다시 시도하세요.";
  if (error?.status === 403) return "서비스 API Key 관리는 관리자만 사용할 수 있습니다.";
  if (error?.status === 422) return "키 이름·Source System·권한 형식을 확인하세요. 관리자 권한은 발급할 수 없습니다.";
  return issuing ? "발급 응답을 확인하지 못했습니다. 키가 발급되었을 수 있으므로 목록을 새로고침해 확인하세요. 원문을 받지 못한 키는 폐기하고 새로 발급하세요. 자동으로 재발급하지 않습니다."
    : "서비스 API Key 요청을 처리하지 못했습니다. 연결 상태를 확인하고 다시 시도하세요.";
}

export const emptyServiceKeyDraft = () => ({ name: "", source_system: "", scopes: [] });
export const emptyServiceKeysState = () => ({ catalog: null, loading: false, busy: "", needsRefresh: false, error: "", notice: "", issued: null, draft: emptyServiceKeyDraft(), editing: null, editName: "" });

export function createServiceKeysController({ api, onChange }) {
  let state = emptyServiceKeysState(); let disposed = false; let generation = 0; let request;
  const publish = patch => { if (!disposed) { state = { ...state, ...patch }; onChange(state); } };
  const blocked = () => disposed || state.busy || state.loading || state.needsRefresh || !state.catalog;
  async function refresh({ preserveError = false } = {}) {
    if (disposed) return false;
    const ticket = ++generation; request?.abort(); request = new AbortController();
    publish({ loading: true, ...(preserveError ? {} : { error: "" }) });
    try {
      const response = await api.serviceApiKeys({ signal: request.signal });
      if (disposed || ticket !== generation) return false;
      if (!Array.isArray(response?.items)) throw new Error("invalid_service_keys_response");
      const catalog = { items: response.items.map(serviceKeyMetadata) };
      publish({ catalog, loading: false, needsRefresh: false }); return true;
    } catch (error) {
      if (!disposed && ticket === generation) publish({ loading: false, needsRefresh: true, error: serviceKeyError(error) });
      return false;
    }
  }
  function update(field, value) { if (!state.busy && ["name", "source_system", "scopes"].includes(field)) publish({ draft: { ...state.draft, [field]: value } }); }
  async function issue() {
    if (blocked() || state.issued) return false;
    const error = validateServiceKeyDraft(state.draft);
    if (error) { publish({ error }); return false; }
    const payload = { name: state.draft.name.trim(), source_system: state.draft.source_system, scopes: [...state.draft.scopes] };
    publish({ busy: "issue", error: "", notice: "" });
    try {
      const result = await api.createServiceApiKey(payload);
      if (disposed) return false;
      if (!result?.item?.id || typeof result.api_key !== "string" || !result.api_key) throw new Error("missing_key_response");
      publish({ issued: { item: serviceKeyMetadata(result.item), api_key: result.api_key }, draft: emptyServiceKeyDraft(), notice: "API Key를 발급했습니다. 원문은 지금 한 번만 표시됩니다." });
      await refresh(); return true;
    } catch (error) {
      if (!disposed) publish({ error: serviceKeyError(error, { issuing: true }), needsRefresh: true });
      return false;
    } finally { publish({ busy: "" }); }
  }
  function closeIssued() { publish({ issued: null, notice: "키 원문 표시를 닫았습니다. 이후 다시 조회할 수 없습니다." }); }
  function edit(item) { if (!state.busy && !item.revoked_at) publish({ editing: serviceKeyMetadata(item), editName: item.name, error: "", notice: "" }); }
  function updateName(name) { if (!state.busy) publish({ editName: name }); }
  function cancelEdit() { if (!state.busy) publish({ editing: null, editName: "" }); }
  async function rename() {
    if (blocked() || !state.editing) return false;
    const error = serviceKeyNameError(state.editName);
    if (error) { publish({ error }); return false; }
    publish({ busy: "rename", error: "", notice: "" });
    try {
      await api.renameServiceApiKey(state.editing.id, { name: state.editName.trim() });
      if (disposed) return false;
      publish({ editing: null, editName: "", notice: "키 이름을 변경했습니다. 키 원문·Source System·권한은 바뀌지 않았습니다." });
      await refresh(); return true;
    } catch (error) {
      if (!disposed) { publish({ error: serviceKeyError(error) }); if ([404, 409].includes(error.status)) await refresh({ preserveError: true }); }
      return false;
    } finally { publish({ busy: "" }); }
  }
  async function revoke(item) {
    if (blocked() || item.revoked_at) return false;
    publish({ busy: `revoke-${item.id}`, error: "", notice: "" });
    try {
      await api.revokeServiceApiKey(item.id);
      if (disposed) return false;
      publish({ notice: "API Key를 영구 폐기했습니다. 다시 활성화할 수 없으며 필요하면 새 키를 발급해야 합니다.", ...(state.issued?.item.id === item.id ? { issued: null } : {}), ...(state.editing?.id === item.id ? { editing: null, editName: "" } : {}) });
      await refresh(); return true;
    } catch (error) { if (!disposed) publish({ error: serviceKeyError(error) }); return false; }
    finally { publish({ busy: "" }); }
  }
  return { refresh, update, issue, closeIssued, edit, updateName, cancelEdit, rename, revoke, getState: () => state, dispose() { disposed = true; generation += 1; request?.abort(); state = { ...state, issued: null }; } };
}
