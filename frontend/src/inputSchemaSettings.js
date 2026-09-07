export const FIELD_TYPES = ["string", "integer", "number", "boolean", "object", "array"];
export const SAMPLE_EVENT = JSON.stringify({ event_id: "schema-sample-001", company_name: "synthetic-company", src_ip: "192.0.2.10", dest_ip: "10.0.0.20", payload: "GET /health HTTP/1.1\r\nHost: synthetic.example\r\n\r\n", waf_vendor: "synthetic", waf_action: "A" }, null, 2);
const clone = value => JSON.parse(JSON.stringify(value));
export const schemaVersionName = value => value ? `v${value.version_number} · ${value.name}` : "미기록";
export const newSchemaField = () => ({ name: "", description: "", type: "string", required: false, nullable: true });
export function editSchemaField(field) {
  return { ...clone(field), _enum: field.enum ? JSON.stringify(field.enum) : "", _items: field.items ? JSON.stringify(field.items, null, 2) : "", _properties: field.properties ? JSON.stringify(field.properties, null, 2) : "" };
}
export function schemaDraft(version, defaults = []) {
  return { name: "", change_note: "", parent_id: version?.id || null, fields: (version?.fields || defaults).map(editSchemaField) };
}
export function changeFieldType(field, type) {
  const { name, description, required, nullable } = field;
  return editSchemaField({ name, description, required, nullable, type });
}
export function draftPayload(draft) {
  if (!draft.name.trim()) throw new Error("버전 이름을 입력하세요.");
  if (!draft.change_note.trim()) throw new Error("변경 설명을 입력하세요.");
  const names = new Set();
  const fields = draft.fields.map(field => {
    const name = field.name.trim();
    if (!/^[A-Za-z][A-Za-z0-9_]{0,63}$/.test(name)) throw new Error("필드명은 영문으로 시작하는 영문·숫자·밑줄 1~64자로 입력하세요.");
    if (names.has(name)) throw new Error(`중복된 필드명: ${name}`);
    names.add(name);
    const result = { name, description: field.description || "", type: field.type, required: Boolean(field.required), nullable: Boolean(field.nullable) };
    const keys = field.type === "string" ? ["min_length", "max_length"] : ["integer", "number"].includes(field.type) ? ["minimum", "maximum"] : field.type === "array" ? ["min_items", "max_items"] : [];
    for (const key of keys) if (field[key] !== "" && field[key] != null) {
      const value = Number(field[key]);
      if (!Number.isFinite(value) || ((key.includes("length") || key.includes("items") || field.type === "integer") && !Number.isSafeInteger(value))) throw new Error(`${name}: 제한 값의 숫자 형식을 확인하세요.`);
      result[key] = value;
    }
    const pair = field.type === "string" ? ["min_length", "max_length"] : field.type === "array" ? ["min_items", "max_items"] : ["minimum", "maximum"];
    if (result[pair[0]] != null && result[pair[1]] != null && result[pair[0]] > result[pair[1]]) throw new Error(`${name}: 최솟값이 최댓값보다 큽니다.`);
    for (const [rawKey, outputKey, applicable] of [["_enum", "enum", !["object", "array"].includes(field.type)], ["_items", "items", field.type === "array"], ["_properties", "properties", field.type === "object"]]) {
      if (!applicable || !field[rawKey]?.trim()) continue;
      try { result[outputKey] = JSON.parse(field[rawKey]); } catch { throw new Error(`${name}: ${outputKey} JSON 형식을 확인하세요.`); }
      if (["enum", "properties"].includes(outputKey) && !Array.isArray(result[outputKey])) throw new Error(`${name}: ${outputKey}는 JSON 배열이어야 합니다.`);
      if (outputKey === "items" && (!result.items || typeof result.items !== "object" || Array.isArray(result.items))) throw new Error(`${name}: items는 JSON 객체여야 합니다.`);
    }
    return result;
  });
  return { name: draft.name.trim(), change_note: draft.change_note.trim(), parent_id: draft.parent_id, fields };
}
function canonical(value) {
  if (Array.isArray(value)) return value.map(canonical);
  if (value && typeof value === "object") return Object.fromEntries(Object.keys(value).sort().filter(key => value[key] != null).map(key => [key, canonical(value[key])]));
  return value;
}
export function schemaFieldDiff(before = [], after = []) {
  const oldFields = new Map(before.map(field => [field.name, field]));
  const newFields = new Map(after.map(field => [field.name, field]));
  return [...new Set([...oldFields.keys(), ...newFields.keys()])].flatMap(name => {
    const previous = oldFields.get(name); const next = newFields.get(name);
    if (JSON.stringify(canonical(previous)) === JSON.stringify(canonical(next))) return [];
    return [{ name, kind: !previous ? "added" : !next ? "removed" : "changed", before: previous || null, after: next || null }];
  });
}
export function parseSchemaSample(text) {
  if (text.length > 2_200_000) throw new Error("샘플이 너무 큽니다. 짧은 합성 이벤트 한 건으로 검증하세요.");
  let event;
  try { event = JSON.parse(text); } catch { throw new Error("샘플의 JSON 형식을 확인하세요."); }
  if (!event || typeof event !== "object" || Array.isArray(event)) throw new Error("샘플은 이벤트 한 건의 JSON 객체여야 합니다.");
  return event;
}
export const emptySchemaSettingsState = () => ({ catalog: null, selected: null, active: null, history: [], loading: false, busy: "", error: "", notice: "", validation: null, acknowledged: false, needsRefresh: false });
export function schemaSaveError(error) {
  const messages = {
    input_schema_reserved_field: "서버 제어값이나 정답 필드는 스키마에 등록할 수 없습니다.",
    input_schema_builtin_contract_protected: "기본 필드의 이름·타입·내부 필수 조건·null 금지 조건은 변경할 수 없습니다.",
    input_schema_builtin_constraint_protected: "기본 필드의 길이·포트 범위·허용값은 기본 경계보다 완화할 수 없습니다.",
    input_schema_builtin_field_required: "기본 11개 필드를 모두 유지해야 합니다.",
  };
  return messages[error.message] || (error.status === 422 ? "필드 정의를 저장하지 못했습니다. 제한 값·하위 필드·허용값의 타입과 개수 및 중복·예약 필드명을 확인하세요." : `저장하지 못했거나 응답을 확인하지 못했습니다 (${error.status || "연결 오류"}). 목록을 새로고침해 저장 여부를 확인하세요.`);
}
export function createSchemaSettingsController({ api, onChange, now = () => Date.now(), schedule = setTimeout, cancel = clearTimeout }) {
  let state = emptySchemaSettingsState(); let disposed = false; let generation = 0; let validationGeneration = 0; let timer;
  const publish = patch => { if (!disposed) { state = { ...state, ...patch }; onChange(state); } };
  function clearValidation() { validationGeneration++; cancel(timer); publish({ validation: null, acknowledged: false }); }
  async function refresh(selectedId) {
    if (disposed || state.busy) return;
    const seq = ++generation; clearValidation(); publish({ loading: true, error: "", needsRefresh: true });
    try {
      const [catalog, history] = await Promise.all([api.inputSchemas(), api.inputSchemaHistory()]);
      const id = selectedId || state.selected?.id || catalog.active_version_id;
      const [selected, active] = await Promise.all([id ? api.inputSchema(id) : null, catalog.active_version_id ? api.inputSchema(catalog.active_version_id) : null]);
      if (disposed || seq !== generation) return;
      publish({ catalog, selected, active, history: history.items || [], loading: false, needsRefresh: false });
    } catch { if (seq === generation) publish({ loading: false, error: "입력 스키마를 조회하지 못했습니다. 목록을 다시 불러와 주세요.", needsRefresh: true }); }
  }
  async function select(id) {
    if (disposed || state.busy || state.needsRefresh || state.loading) return;
    const seq = ++generation; clearValidation(); publish({ loading: true, selected: null, error: "", notice: "" });
    try { const selected = await api.inputSchema(id); if (seq === generation) publish({ selected, loading: false }); }
    catch { if (seq === generation) publish({ loading: false, error: "선택한 버전을 조회하지 못했습니다. 목록을 다시 불러와 주세요.", needsRefresh: true }); }
  }
  async function save(draft) {
    if (disposed || state.busy || state.loading || state.needsRefresh) return null;
    let payload; try { payload = draftPayload(draft); } catch (error) { publish({ error: error.message }); return null; }
    clearValidation(); publish({ busy: "save", error: "", notice: "" });
    try {
      const saved = await api.createInputSchema(payload);
      publish({ busy: "", notice: "새 버전을 저장했습니다. 새 버전으로 접수하려면 샘플 검증 후 운영 적용하세요." });
      await refresh(saved.id); return saved;
    } catch (error) { publish({ busy: "", error: schemaSaveError(error), needsRefresh: ![400, 422].includes(error.status) }); return null; }
  }
  async function validate(sample) {
    if (disposed || state.busy || state.loading || state.needsRefresh || !state.selected) return;
    clearValidation(); let event;
    try { event = parseSchemaSample(sample); } catch (error) { publish({ error: error.message }); return; }
    const id = state.selected.id; const revision = state.catalog.revision; const validationSeq = validationGeneration;
    publish({ busy: "validate", error: "", notice: "" });
    try {
      const result = await api.validateInputSchema(id, event);
      if (disposed) return;
      if (validationSeq !== validationGeneration) { publish({ busy: "" }); return; }
      const seconds = Number(result.expires_in_seconds);
      const valid = result.valid === true && typeof result.validation_token === "string" && result.validation_token && Number.isFinite(seconds) && seconds > 0;
      const validation = { ...result, valid: Boolean(valid), version_id: id, revision, expires_at: now() + Math.min(seconds || 0, 300) * 1000 };
      publish({ busy: "", validation, acknowledged: false });
      if (valid) timer = schedule(() => { clearValidation(); publish({ notice: "검증 유효시간이 지났습니다. 적용 전 샘플을 다시 검증하세요." }); }, Math.min(seconds, 300) * 1000);
    } catch { publish({ busy: "", error: "샘플 검증을 완료하지 못했습니다. 다시 검증해 주세요." }); }
  }
  function canActivate() {
    const check = state.validation;
    return !disposed && !state.busy && !state.loading && !state.needsRefresh && state.acknowledged && check?.valid && check.version_id === state.selected?.id && check.revision === state.catalog?.revision && check.expires_at > now() && state.selected.id !== state.catalog.active_version_id;
  }
  async function activate() {
    if (!canActivate()) return false;
    const id = state.selected.id; const payload = { expected_revision: state.validation.revision, validation_token: state.validation.validation_token };
    clearValidation(); publish({ busy: "activate", error: "", notice: "" });
    try {
      await api.activateInputSchema(id, payload);
      publish({ busy: "", notice: "선택한 버전을 운영에 적용했습니다. 새 접수와 Production API 정의서에 반영됩니다. 기존 분석·테스트는 변경하지 않습니다." });
      await refresh(id); return true;
    } catch (error) {
      publish({ busy: "", needsRefresh: true, error: error.status === 409 ? "다른 관리 작업으로 적용 기준이 바뀌었습니다. 새로고침 후 변경 비교와 샘플 검증을 다시 진행하세요." : "적용 응답을 확인하지 못했습니다. 재시도 전에 새로고침해 현재 적용 버전을 확인하세요." }); return false;
    }
  }
  return { refresh, select, save, validate, activate, canActivate, clearValidation, acknowledge: value => publish({ acknowledged: Boolean(value) }), dispose: () => { disposed = true; generation++; cancel(timer); } };
}
