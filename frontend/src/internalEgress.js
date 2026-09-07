export const INTERNAL_EGRESS_DESCRIPTION_LIMIT = 500;

// Browser URL parsing alone accepts abbreviated/octal IPv4 addresses. Validate the
// literal first, then use URL only for IPv6 syntax and canonicalization.
export function canonicalPrivateIP(value) {
  if (typeof value !== "string" || value !== value.trim()) return null;
  if (value.includes(":")) {
    if (!/^[0-9a-f:.]+$/i.test(value)) return null;
    try {
      const host = new URL(`http://[${value}]/`).hostname.slice(1, -1);
      const first = Number.parseInt(host.split(":")[0], 16);
      return first >= 0xfc00 && first <= 0xfdff ? host : null;
    } catch { return null; }
  }
  if (!/^(?:0|[1-9]\d{0,2})(?:\.(?:0|[1-9]\d{0,2})){3}$/.test(value)) return null;
  const octets = value.split(".").map(Number);
  if (octets.some((part) => part > 255)) return null;
  return octets[0] === 10 || (octets[0] === 172 && octets[1] >= 16 && octets[1] <= 31)
    || (octets[0] === 192 && octets[1] === 168) ? octets.join(".") : null;
}

export function validInternalPort(value) {
  return /^(?:[1-9]\d{0,4})$/.test(String(value)) && Number(value) <= 65535;
}

export function internalEndpoint(value) {
  if (typeof value !== "string" || value !== value.trim() || /[\s\\]/.test(value)) return null;
  const match = /^https?:\/\/(\[[^\]]+\]|[^/:?#@]+)(?::(\d+))?(\/[^?#]*)?$/.exec(value);
  if (!match) return null;
  if (!["", "/", "/v1", "/v1/"].includes(match[3] || "")) return null;
  const rawHost = match[1].startsWith("[") ? match[1].slice(1, -1) : match[1];
  const ip = canonicalPrivateIP(rawHost);
  const port = match[2] || (value.startsWith("https:") ? "443" : "80");
  if (!ip || !validInternalPort(port)) return null;
  return { ip_address: ip, port: Number(port) };
}

export function allowedInternalTarget(value, items) {
  const endpoint = internalEndpoint(value);
  if (!endpoint) return null;
  return items?.find((item) => canonicalPrivateIP(item.ip_address) === endpoint.ip_address && Number(item.port) === endpoint.port) || null;
}

export function internalTargetAddress(target) {
  return `${target.ip_address.includes(":") ? `[${target.ip_address}]` : target.ip_address}:${target.port}`;
}

export function internalTargetURL(target, currentURL = "") {
  return `${currentURL.startsWith("https://") ? "https" : "http"}://${internalTargetAddress(target)}/v1`;
}

export function vllmTargetError(baseURL, state) {
  if (!state || state.loading) return "Internal Egress 허용 대상 목록을 불러오는 중입니다.";
  if (state.error || !Array.isArray(state.items)) return "Internal Egress 허용 대상 목록을 확인하지 못했습니다. 다시 조회한 뒤 저장하세요.";
  if (!state.items.length) return "Internal Egress에 내부 IP와 포트를 먼저 등록하세요.";
  if (!internalEndpoint(baseURL)) return "vLLM Base URL은 hostname 대신 내부 IP 주소를 사용하고 경로는 비우거나 /v1로 입력하세요. 예: http://10.0.0.10:8000/v1";
  if (!allowedInternalTarget(baseURL, state.items)) return "Base URL의 IP와 포트가 Internal Egress에 등록되어 있지 않습니다.";
  return "";
}

export function internalEgressError(error) {
  const messages = {
    internal_egress_target_exists: "같은 IP와 포트가 이미 등록되어 있습니다.",
    internal_egress_target_in_use: "사용 중인 대상입니다. 연결된 vLLM 프로필을 모두 비활성화한 뒤 IP·포트를 변경하거나 삭제하세요. 설명은 수정할 수 있습니다.",
    internal_egress_changed: "다른 관리자가 대상을 변경했습니다. 입력은 유지했습니다. 최신 내용을 검토한 뒤 다시 저장하세요.",
    internal_egress_not_found: "이 대상이 삭제되었거나 더 이상 존재하지 않습니다. 입력은 유지했습니다. 목록을 확인하세요.",
    internal_egress_ip_must_be_private: "RFC1918 IPv4 또는 ULA IPv6 내부 IP만 등록할 수 있습니다. hostname·CIDR·loopback·link-local·공인 IP는 허용되지 않습니다.",
    vllm_target_not_allowed: "vLLM의 IP와 포트가 Internal Egress에 등록되어 있지 않습니다. 허용 대상을 다시 확인하세요.",
    vllm_base_url_must_use_ip_address: "vLLM은 hostname 대신 IP 주소를 사용해야 합니다. 기존 vllm.internal 주소를 실제 내부 IP로 변경하고 Internal Egress에 등록하세요.",
  };
  return (Object.hasOwn(messages, error?.message) ? messages[error.message] : null) || (error?.status === 401 ? "로그인 세션을 확인하고 다시 시도하세요."
    : error?.status === 403 ? "Internal Egress 설정은 관리자만 사용할 수 있습니다."
      : error?.status === 422 ? "IP·포트·설명 형식을 확인하세요. 포트는 1~65535 정수, 설명은 최대 500자입니다."
        : "Internal Egress 요청을 처리하지 못했습니다. 연결 상태를 확인하고 다시 시도하세요.");
}

export function validateInternalEgressDraft(draft) {
  if (!canonicalPrivateIP(draft.ip_address.trim())) return "RFC1918 IPv4 또는 ULA IPv6 내부 IP 주소를 입력하세요. hostname·CIDR·loopback·link-local·공인 IP는 허용되지 않습니다.";
  if (!validInternalPort(draft.port)) return "포트는 1~65535 사이의 정수로 입력하세요.";
  if (Array.from(draft.description.trim()).length > INTERNAL_EGRESS_DESCRIPTION_LIMIT) return "설명은 500자 이하로 입력하세요.";
  if (/[\p{Cc}\p{Cf}\p{Cs}]/u.test(draft.description)) return "설명에는 줄바꿈이나 제어문자를 넣을 수 없습니다.";
  return "";
}

export const emptyInternalEgressDraft = () => ({ ip_address: "", port: "8000", description: "" });
export const emptyInternalEgressState = () => ({ items: null, loading: false, busy: "", error: "", notice: "", editing: null, draft: emptyInternalEgressDraft(), conflict: false, needsRefresh: false });

export function sameInternalTarget(draft, item) {
  return canonicalPrivateIP(draft.ip_address.trim()) === canonicalPrivateIP(item.ip_address) && Number(draft.port) === Number(item.port);
}

// Mutations never retry automatically. A refresh does not replace the edit draft,
// and a changed revision requires an explicit acknowledgement before saving.
export function createInternalEgressController({ api, onChange }) {
  let state = emptyInternalEgressState();
  let disposed = false;
  let generation = 0;
  let request = null;
  const publish = (patch) => { if (!disposed) { state = { ...state, ...patch }; onChange(state); } };
  async function refresh({ preserveError = false } = {}) {
    if (disposed) return false;
    const ticket = ++generation;
    request?.abort(); request = new AbortController();
    publish({ loading: true, ...(preserveError ? {} : { error: "" }) });
    try {
      const items = await api.internalEgress({ signal: request.signal });
      if (disposed || ticket !== generation) return false;
      if (!Array.isArray(items)) throw new Error("invalid_internal_egress_response");
      const latest = state.editing && items.find((item) => item.id === state.editing.id);
      publish({ items, loading: false, needsRefresh: false, conflict: state.conflict || Boolean(state.editing && (!latest || latest.revision !== state.editing.revision)) });
      return true;
    } catch (error) {
      if (!disposed && ticket === generation) publish({ loading: false, needsRefresh: true, error: internalEgressError(error) });
      return false;
    }
  }
  function edit(item) {
    if (state.busy) return;
    publish({ editing: { ...item }, draft: { ip_address: item.ip_address, port: String(item.port), description: item.description || "" }, conflict: false, error: "", notice: "" });
  }
  function cancel() {
    if (!state.busy) publish({ editing: null, draft: emptyInternalEgressDraft(), conflict: false, error: "", notice: "" });
  }
  function update(field, value) {
    if (!state.busy && ["ip_address", "port", "description"].includes(field)) publish({ draft: { ...state.draft, [field]: value } });
  }
  function acceptLatest() {
    if (state.busy || state.loading || state.needsRefresh) return;
    const latest = state.items?.find((item) => item.id === state.editing?.id);
    if (latest) publish({ editing: { ...latest }, conflict: false, error: "", notice: "최신 내용을 기준으로 편집을 계속합니다. 입력 내용은 아직 저장하지 않았습니다." });
  }
  function restoreTarget() {
    const latest = state.items?.find((item) => item.id === state.editing?.id);
    if (!state.busy && latest) publish({ draft: { ...state.draft, ip_address: latest.ip_address, port: String(latest.port) } });
  }
  async function failedMutation(error) {
    publish({ error: internalEgressError(error), conflict: state.conflict || (Boolean(state.editing) && ["internal_egress_changed", "internal_egress_not_found"].includes(error.message)) });
    if (error.status === 409 || error.status === 404) await refresh({ preserveError: true });
  }
  async function save() {
    if (disposed || state.busy || state.loading || state.needsRefresh || !state.items || state.conflict) return false;
    const invalid = validateInternalEgressDraft(state.draft);
    if (invalid) { publish({ error: invalid }); return false; }
    const latest = state.items.find((item) => item.id === state.editing?.id);
    if (latest?.in_use_profiles?.length && !sameInternalTarget(state.draft, latest)) {
      publish({ error: internalEgressError(new Error("internal_egress_target_in_use")) }); return false;
    }
    const payload = { ip_address: canonicalPrivateIP(state.draft.ip_address.trim()), port: Number(state.draft.port), description: state.draft.description.trim() };
    const editing = state.editing;
    publish({ busy: "save", error: "", notice: "" });
    try {
      if (editing) await api.updateInternalEgress(editing.id, { ...payload, expected_revision: editing.revision });
      else await api.createInternalEgress(payload);
      if (disposed) return false;
      publish({ editing: null, draft: emptyInternalEgressDraft(), conflict: false, notice: editing ? "내부 대상을 수정했습니다. 모델은 호출하지 않았습니다." : "내부 대상을 등록했습니다. 모델은 호출하지 않았습니다." });
      await refresh(); return true;
    } catch (error) { if (!disposed) await failedMutation(error); return false; }
    finally { publish({ busy: "" }); }
  }
  async function remove(item) {
    if (disposed || state.busy || state.loading || state.needsRefresh || item.in_use_profiles?.length) return false;
    publish({ busy: `delete-${item.id}`, error: "", notice: "" });
    try {
      await api.deleteInternalEgress(item.id, item.revision);
      if (disposed) return false;
      publish({ notice: "내부 대상을 삭제했습니다. 이 화면에서 같은 IP·포트를 다시 등록할 수 있습니다.", ...(state.editing?.id === item.id ? { editing: null, draft: emptyInternalEgressDraft(), conflict: false } : {}) });
      await refresh(); return true;
    } catch (error) { if (!disposed) await failedMutation(error); return false; }
    finally { publish({ busy: "" }); }
  }
  return { refresh, edit, cancel, update, acceptLatest, restoreTarget, save, remove, getState: () => state, dispose() { disposed = true; generation += 1; request?.abort(); } };
}
