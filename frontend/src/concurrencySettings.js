export const emptyConcurrencyState = () => ({ catalog: null, draft: null, loading: true, busy: false, needsRefresh: false, error: "", notice: "" });
const integer = (value, maximum) => Number.isInteger(value) && value >= 1 && value <= maximum;
export function validConcurrencyCatalog(value) {
  return Boolean(value && /^[a-f0-9]{64}$/.test(value.state_token) && integer(value.production, 32) && integer(value.test, 32)
    && Array.isArray(value.servers) && new Set(value.servers.map(row => row?.server_key)).size === value.servers.length
    && value.servers.every(row => typeof row.server_key === "string" && row.server_key && integer(row.max_calls, 64)
      && Array.isArray(row.profiles) && row.profiles.every(profile => typeof profile.name === "string")));
}
export function concurrencyDraft(catalog) {
  return { production: catalog.production, test: catalog.test,
    servers: catalog.servers.map(({ server_key, max_calls }) => ({ server_key, max_calls })) };
}
export function concurrencyIssue(draft) {
  if (!draft) return "설정을 먼저 조회하세요.";
  if (!integer(draft.production, 32) || !integer(draft.test, 32)) return "동시 분석 수는 1~32건으로 입력하세요.";
  if (!draft.servers.every(row => integer(row.max_calls, 64))) return "서버별 동시 호출 수는 1~64건으로 입력하세요.";
  return "";
}
export function createConcurrencyController({ api, onChange }) {
  let state = emptyConcurrencyState(), sequence = 0, disposed = false;
  const publish = patch => { if (!disposed) { state = { ...state, ...patch }; onChange(state); } };
  async function refresh() {
    if (disposed || state.busy) return;
    const ticket = ++sequence;
    publish({ loading: true, error: "", notice: "" });
    try {
      const catalog = await api.concurrencySettings();
      if (disposed || ticket !== sequence) return;
      if (!validConcurrencyCatalog(catalog)) throw new Error("invalid_concurrency_settings");
      publish({ catalog, draft: concurrencyDraft(catalog), loading: false, needsRefresh: false });
    } catch { if (ticket === sequence) publish({ loading: false, needsRefresh: true, error: "동시 처리 설정을 불러오지 못했습니다. 새로고침해 주세요." }); }
  }
  return { refresh, getState: () => state, dispose() { disposed = true; sequence++; },
    change(field, value, serverKey) {
      if (disposed || state.busy || state.loading || state.needsRefresh || !state.draft) return;
      if (field === "server" && state.draft.servers.some(row => row.server_key === serverKey)) {
        publish({ draft: { ...state.draft, servers: state.draft.servers.map(row => row.server_key === serverKey ? { ...row, max_calls: value } : row) }, notice: "" });
      } else if (["production", "test"].includes(field)) publish({ draft: { ...state.draft, [field]: value }, notice: "" });
    },
    async save() {
      if (disposed || state.busy || state.loading || state.needsRefresh || concurrencyIssue(state.draft)) return false;
      publish({ busy: true, error: "", notice: "" });
      try {
        const catalog = await api.updateConcurrencySettings({ expected_state_token: state.catalog.state_token, ...state.draft });
        if (disposed) return false;
        if (!validConcurrencyCatalog(catalog)) throw new Error("invalid_concurrency_settings");
        publish({ catalog, draft: concurrencyDraft(catalog), busy: false, notice: "저장했습니다. 다음 작업 선점·호출부터 적용됩니다." });
        return true;
      } catch (error) {
        publish({ busy: false, needsRefresh: true, error: error.message === "concurrency_configuration_changed"
          ? "설정이나 서버 목록이 바뀌었습니다. 새로고침 후 다시 확인하세요."
          : "저장 결과를 확인하지 못했습니다. 자동 재요청하지 않습니다. 새로고침으로 적용 여부를 확인하세요." });
        return false;
      }
    } };
}
