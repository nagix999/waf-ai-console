import NavigationAction from "./NavigationAction.jsx";
import DataTable, { Table } from "./DataTable.jsx";
import { useEffect, useRef, useState } from "react";
import { api } from "./api.js";
import HelpTooltip from "./HelpTooltip.jsx";
import Dialog from "./Dialog.jsx";
import { FIELD_TYPES, SAMPLE_EVENT, changeFieldType, createSchemaSettingsController, draftPayload, editSchemaField, emptySchemaSettingsState, newSchemaField, schemaDraft, schemaFieldDiff, schemaVersionName } from "./inputSchemaSettings.js";
import "./inputSchemaSettings.css";
import { SettingsSectionHeader, SettingsWorkflow } from "./SettingsWorkspace.jsx";
import { useConsolePreferences } from "./consolePreferences.jsx";

const timestamp = value => value ? new Date(value).toLocaleString("ko-KR") : "미기록";
const fieldTypeNames = { string: "문자열", integer: "정수", number: "숫자", boolean: "참/거짓", object: "객체", array: "배열" };
const constraintNames = { min_length: "최소 글자 수", max_length: "최대 글자 수", minimum: "최솟값", maximum: "최댓값", min_items: "최소 항목 수", max_items: "최대 항목 수" };
export function SchemaFields({ fields = [] }) {
  const [nested, setNested] = useState(null);
  return <><div className="table-wrap schema-field-table"><Table><caption className="sr-only">입력 필드 정의</caption><thead><tr><th scope="col">필드 / 설명</th><th scope="col">형식</th><th scope="col">필수 / 빈 값</th><th scope="col">제한</th></tr></thead><tbody>{fields.map(field => <tr key={field.name}><td><code>{field.name}</code><p>{field.description || "설명 없음"}</p></td><td>{fieldTypeNames[field.type] || field.type}</td><td>{field.required ? "필수" : "선택"}<br />{field.nullable ? "null 허용" : "null 불가"}</td><td>{Object.entries(constraintNames).filter(([key]) => field[key] != null).map(([key, label]) => <div key={key}>{label}: {field[key]}</div>)}{field.enum != null && <div>허용값: <code>{JSON.stringify(field.enum)}</code></div>}{(field.items || field.properties) && <button type="button" className="text-button" onClick={() => setNested(field)}>하위 필드 정의 보기</button>}</td></tr>)}</tbody></Table></div>
    <Dialog open={Boolean(nested)} title="하위 필드 정의" onClose={() => setNested(null)} className="input-schema-settings schema-dialog">{nested && <><h3>{nested.name}</h3><pre className="schema-json">{JSON.stringify(nested.items || nested.properties, null, 2)}</pre></>}</Dialog>
  </>;
}
export function SchemaChangeContent({ change }) {
  return <div className="schema-diff-columns"><div><h4>변경 전</h4><pre>{change.before ? JSON.stringify(change.before, null, 2) : "필드 없음"}</pre></div><div><h4>변경 후</h4><pre>{change.after ? JSON.stringify(change.after, null, 2) : "필드 없음"}</pre></div></div>;
}
export function SchemaDiff({ before, after, beforeLabel = "현재 적용 버전", afterLabel = "선택한 버전" }) {
  const [selected, setSelected] = useState("");
  const changes = schemaFieldDiff(before, after), change = changes.find(item => item.name === selected);
  return <div className="schema-diff" aria-label="필드 정의 변경 비교"><p>{beforeLabel} → {afterLabel}</p>{changes.length ? <div className="schema-change-list">{changes.map(item => <button className="secondary" type="button" key={item.name} onClick={() => setSelected(item.name)}><span>{({ added: "추가", removed: "삭제", changed: "변경" })[item.kind]}</span> <code>{item.name}</code><span>변경 전후 보기</span></button>)}</div> : <p className="muted">필드 정의에 차이가 없습니다.</p>}
    <Dialog open={Boolean(change)} title="필드 변경 전후" onClose={() => setSelected("")} className="input-schema-settings schema-dialog">{change && <><h3>{change.name}</h3><SchemaChangeContent change={change} /></>}</Dialog>
  </div>;
}
function FieldEditor({ field, index, defaults, disabled, onChange, onRemove, onClose }) {
  const baseline = defaults.find(value => value.name === field.name);
  const fieldName = field.name || `새 필드 ${index + 1}`;
  const numericKeys = field.type === "string" ? ["min_length", "max_length"] : ["integer", "number"].includes(field.type) ? ["minimum", "maximum"] : field.type === "array" ? ["min_items", "max_items"] : [];
  return <Dialog open title="필드 편집" onClose={onClose} className="input-schema-settings schema-dialog"><h3>{fieldName}</h3>{baseline && <p className="schema-help">기본 필드의 이름·형식과 최소 필수 조건은 유지합니다.</p>}<fieldset disabled={disabled}>
    <div className="schema-editor-grid"><label>필드명<input value={field.name} maxLength={64} readOnly={Boolean(baseline)} onChange={event => onChange({ ...field, name: event.target.value })} spellCheck={false} /></label><label>형식<select value={field.type} disabled={Boolean(baseline)} onChange={event => onChange(changeFieldType(field, event.target.value))}>{FIELD_TYPES.map(type => <option key={type} value={type}>{fieldTypeNames[type]}</option>)}</select></label>
      <label className="schema-full">설명<textarea rows={2} value={field.description} maxLength={1000} onChange={event => onChange({ ...field, description: event.target.value })} placeholder="필드가 나타내는 값과 수집 기준. 이 설명은 이력에만 기록합니다." /></label>
      <label className="schema-check"><input type="checkbox" checked={field.required} disabled={baseline?.required} onChange={event => onChange({ ...field, required: event.target.checked })} />필수</label><label className="schema-check"><input type="checkbox" checked={field.nullable} disabled={baseline?.nullable === false} onChange={event => onChange({ ...field, nullable: event.target.checked })} />null 허용<HelpTooltip label="null 허용">필드가 존재하지만 값이 null인 입력을 허용합니다. 필수 여부는 필드 자체를 생략할 수 있는지 정하므로 서로 다른 조건입니다.</HelpTooltip></label>
      {numericKeys.map(key => <label key={key}>{constraintNames[key]}<input type="number" step={field.type === "number" ? "any" : "1"} min={key.startsWith("min_") || key.startsWith("max_") ? 0 : undefined} value={field[key] ?? ""} onChange={event => onChange({ ...field, [key]: event.target.value })} placeholder="제한 없음" />{baseline?.[key] != null && <small>기본 경계 {baseline[key]}보다 완화할 수 없습니다.</small>}</label>)}
      {!["object", "array"].includes(field.type) && <label className="schema-full">허용값 · JSON 배열<input value={field._enum || ""} onChange={event => onChange({ ...field, _enum: event.target.value })} placeholder={'예: ["east", "west"] / [1, 2] · 빈칸이면 지정 안 함'} spellCheck={false} /></label>}
      {field.type === "object" && <label className="schema-full">하위 필드 · properties JSON 배열<textarea rows={6} value={field._properties || ""} onChange={event => onChange({ ...field, _properties: event.target.value })} placeholder={'[{"name":"region","description":"지역","type":"string","required":false,"nullable":true}]'} spellCheck={false} /><small>각 하위 필드도 이름·타입·필수·null·제한을 정의합니다. 등록하지 않은 하위 값은 보존합니다.</small></label>}
      {field.type === "array" && <label className="schema-full">배열 항목 · items JSON 객체<textarea rows={5} value={field._items || ""} onChange={event => onChange({ ...field, _items: event.target.value })} placeholder={'{"type":"string","nullable":false,"max_length":120}'} spellCheck={false} /><small>type·nullable·타입별 제한을 지정합니다. 객체 항목은 properties로 하위 필드를 정의합니다.</small></label>}
    </div><button type="button" className="secondary" disabled={Boolean(baseline)} onClick={onRemove}>필드 삭제</button>
  </fieldset><button type="button" className="secondary" onClick={onClose}>편집 내용 유지 · 닫기</button></Dialog>;
}

export default function InputSchemaSettings() {
  const { locale } = useConsolePreferences(); const w = (ko, en) => locale === "en" ? en : ko;
  const [state, setState] = useState(emptySchemaSettingsState);
  const [draft, setDraft] = useState(null), [sample, setSample] = useState("");
  const [editingField, setEditingField] = useState(null), [discardOpen, setDiscardOpen] = useState(false);
  const [view, setView] = useState(""), [draftCompareOpen, setDraftCompareOpen] = useState(false);
  const controller = useRef(null), draftName = useRef(null);
  useEffect(() => { const instance = createSchemaSettingsController({ api, onChange: setState }); controller.current = instance; void instance.refresh(); return () => { instance.dispose(); controller.current = null; }; }, []);
  const { catalog, selected, active } = state;
  const versions = catalog?.items || [], busy = Boolean(state.busy), blocked = busy || state.loading || state.needsRefresh || !catalog;
  let draftFields = null, draftError = "";
  if (draft) { try { draftFields = draftPayload({ ...draft, name: draft.name || "미지정", change_note: draft.change_note || "미지정" }).fields; } catch (error) { draftError = error.message; } }
  const fields = draft?.fields || selected?.fields || [];
  function editField(index) {
    controller.current?.clearValidation();
    if (!draft) setDraft(schemaDraft(selected, catalog?.default_fields));
    setEditingField(index);
  }
  function addField() {
    const next = draft || schemaDraft(selected, catalog?.default_fields);
    controller.current?.clearValidation();
    setDraft({ ...next, fields: [...next.fields, editSchemaField(newSchemaField())] });
    setEditingField(next.fields.length);
  }
  async function save(event) {
    event.preventDefault();
    if (blocked || draftError) return;
    const saved = await controller.current?.save(draft);
    if (saved) { setDraft(null); setEditingField(null); setDraftCompareOpen(false); }
  }
  return <div className="page-stack input-schema-settings settings-workspace">
    <SettingsSectionHeader title={w("입력 스키마", "Input Schema")} actions={<><button type="button" className="secondary" disabled={busy || state.loading || Boolean(draft)} onClick={() => controller.current?.refresh()}>{w("새로고침", "Refresh")}</button><button type="button" className="secondary" disabled={!catalog} onClick={() => setView("history")}>{w("적용 이력", "Application history")}</button><button type="button" className="primary" disabled={blocked || !selected || fields.length >= 64} onClick={addField}>+ {w("필드 추가", "Add field")}</button></>} />
    {state.error && <div className="error" role="alert">{state.error}</div>}{state.notice && <div className="notice" role="status">{state.notice}</div>}
    <section className="panel settings-schema-fields">
      <div className="panel-head"><div><h2>{w("입력 필드", "Input fields")}</h2><p className="ux-muted">{fields.length}{w("개 필드", " fields")} · {draft ? w("저장 전 초안", "Unsaved draft") : w("저장된 버전", "Saved version")}</p></div><label className="settings-version-picker">{w("버전", "Version")}<select value={selected?.id || ""} disabled={blocked || Boolean(draft)} onChange={event => controller.current?.select(event.target.value)}>{!selected && <option value="">{w("불러오는 중…", "Loading…")}</option>}{versions.map(version => <option key={version.id} value={version.id}>{schemaVersionName(version)}{version.id === catalog?.active_version_id ? w(" · 운영 적용 중", " · Production") : ""}</option>)}</select></label></div>
      {state.loading && <p role="status">{w("스키마 정의를 불러오는 중…", "Loading schema…")}</p>}
      {selected && <><div className="settings-schema-meta"><span className="status purpose-production">{w("운영 적용 중", "Production")}</span><strong>{schemaVersionName(active)}</strong><button type="button" className="text-button" onClick={() => setView("compare")}>{w("변경 비교", "Compare versions")}</button><button type="button" className="text-button" onClick={() => setView("technical")}>{w("기술정보", "Technical details")}</button></div>
      {selected.change_note && !draft && <p className="schema-help settings-card-padding">{selected.change_note}</p>}
      <DataTable label={w("입력 필드 정의", "Input field definitions")} data={fields} getRowId={(field) => field.name || "new-field"} columns={[
        { id: "name", header: w("필드명", "Field"), minWidth: 170, render: field => <><code>{field.name || w("새 필드", "New field")}</code>{catalog?.default_fields?.some(item => item.name === field.name) && <small>{w("기본 필드", "Built-in")}</small>}</> },
        { id: "description", header: w("설명", "Description"), minWidth: 230, render: field => field.description || "—" },
        { id: "type", header: w("형식", "Type"), minWidth: 90, render: field => <span className="settings-type-badge">{field.type}</span> },
        { id: "required", header: w("필수", "Required"), minWidth: 85, render: field => field.required ? w("필수", "Yes") : w("선택", "No") },
        { id: "nullable", header: w("null 허용", "Nullable"), minWidth: 95, render: field => field.nullable ? w("허용", "Yes") : w("불가", "No") },
        { id: "constraints", header: w("제한", "Constraints"), minWidth: 170, render: field => <>{Object.entries(constraintNames).filter(([key]) => field[key] != null && field[key] !== "").map(([key, label]) => <div key={key}>{label}: {field[key]}</div>)}{(field._enum || field.enum) && <div>{w("허용값", "Allowed")}: <code>{field._enum || JSON.stringify(field.enum)}</code></div>}{(field.items || field.properties || field._items || field._properties) && <small>{w("하위 정의 있음", "Nested definition")}</small>}</> },
        { id: "actions", header: w("관리", "Actions"), minWidth: 85, width: 85, render: field => <button type="button" className="text-button" disabled={blocked} aria-label={(field.name || w("새 필드", "New field")) + " " + w("필드 편집", "edit field")} onClick={() => editField(fields.indexOf(field))}>{w("편집", "Edit")}</button> },
      ]} empty={w("입력 필드가 없습니다.", "No input fields.")} />
      <p className="schema-help settings-card-padding">기본 11개 필드의 이름·타입·최소 필수 조건은 보호합니다. 추가 필드는 자유롭게 정의할 수 있지만 서버 제어값과 정답 필드는 등록할 수 없습니다.<HelpTooltip label="필드 정의 한도">하위 필드를 포함해 최대 64개, 깊이 4단계, 정의 64 KiB, 허용값 50개입니다. 정규식·외부 참조·실행 코드는 지원하지 않으며 서버에서 최종 검사합니다.</HelpTooltip></p></>}
      {draft && <form className="settings-schema-version-form" onSubmit={save}><div className="settings-inline-fields"><label>버전 이름<input ref={draftName} value={draft.name} maxLength={120} required disabled={busy} onChange={event => setDraft({ ...draft, name: event.target.value })} placeholder="새 스키마 버전 이름" /></label><label>변경 설명<input value={draft.change_note} maxLength={1000} required disabled={busy} onChange={event => setDraft({ ...draft, change_note: event.target.value })} placeholder="변경한 필드와 이유" /></label></div>
        <p className="schema-safety-note">필드 설명은 공개 API 문서와 실행 이력에 기록하며 LLM 입력에는 추가하지 않습니다. 비밀값·실제 로그·개인정보를 넣지 마세요.</p>
        {draftError && <p className="error" role="alert">{draftError}</p>}
        <div className="schema-actions"><button type="submit" className="primary" disabled={blocked || Boolean(draftError)}>{state.busy === "save" ? "저장 중…" : "새 버전 저장"}</button><button type="button" className="secondary" onClick={() => setDraftCompareOpen(true)}>초안 변경 비교</button><button type="button" className="text-button" disabled={busy} onClick={() => setDiscardOpen(true)}>초안 삭제</button></div>
      </form>}
    </section>
    <div className="settings-schema-bottom"><section className="panel settings-schema-validation"><div className="panel-head"><h2>{w("샘플 검증", "Validate sample")}</h2><span>{selected ? schemaVersionName(selected) : "—"}</span></div><div className="schema-validation">
      <p className="schema-help">실제 원문·Cookie·API 키가 없는 예시 JSON 한 건을 입력하세요. 접수 기준만 확인하며 LLM 호출·분석 접수·샘플 저장은 하지 않습니다.</p>
      <label>{w("검증할 샘플", "Sample JSON")}<textarea rows={8} value={sample} placeholder={SAMPLE_EVENT} disabled={busy} spellCheck={false} onChange={event => { controller.current?.clearValidation(); setSample(event.target.value); }} /></label>
      {draft && <p className="notice">초안을 새 버전으로 저장한 뒤 검증하세요. 저장 전 필드와 이전 버전의 검증 결과를 혼동하지 않도록 검증을 중지했습니다.</p>}
      <button type="button" className="secondary" disabled={blocked || !selected || Boolean(draft) || !sample.trim()} onClick={() => controller.current?.validate(sample)}>{state.busy === "validate" ? "검증 중…" : "샘플 검증"}</button>
      {state.validation && !draft && <div role="status" className={state.validation.valid ? "notice" : "error"}>{state.validation.valid ? "샘플이 접수 기준을 통과했습니다. 판정 품질은 공식 테스트에서 확인하세요." : "샘플이 접수 기준을 통과하지 못했습니다."}{state.validation.issues?.map((issue, index) => <p key={index}><code>{issue.field}</code> · {issue.message} <small>({issue.type})</small></p>)}</div>}
      <p className="schema-help">숫자·boolean·객체·배열 추가 필드는 JSON 업로드를 사용하세요. CSV는 기존 포트만 숫자로 변환합니다.</p>
    </div></section><aside className="settings-context-stack"><section className="panel settings-context-card"><h3>운영 반영 절차</h3><SettingsWorkflow /><p>저장은 운영을 변경하지 않습니다. 공식 테스트 → 운영 반영 검토 → 운영에 반영을 거쳐 API와 새 접수에 적용합니다.</p><NavigationAction href="#evaluate/tests/new">공식 테스트 만들기</NavigationAction></section><section className="panel settings-context-card"><h3>{w("API와 같은 기준", "Shared API contract")}</h3><p>{w("운영에 반영한 스키마는 새 요청 검증과 API 정의서에 함께 적용됩니다. 기존 분석의 당시 스키마는 유지합니다.", "The applied schema governs new requests and the API reference. Existing analyses retain their original schema.")}</p><NavigationAction href="#connect/production-api">{w("운영 API 정의서", "API reference")}</NavigationAction></section></aside></div>
    {editingField !== null && draft?.fields[editingField] && <FieldEditor field={draft.fields[editingField]} index={editingField} defaults={catalog?.default_fields || []} disabled={busy} onClose={() => setEditingField(null)} onChange={value => { controller.current?.clearValidation(); setDraft({ ...draft, fields: draft.fields.map((item, position) => position === editingField ? value : item) }); }} onRemove={() => { setDraft({ ...draft, fields: draft.fields.filter((_, position) => position !== editingField) }); setEditingField(null); }} />}
    <Dialog open={draftCompareOpen && Boolean(draft)} title="스키마 초안 변경 비교" onClose={() => setDraftCompareOpen(false)} className="input-schema-settings schema-dialog">{draftFields ? <SchemaDiff before={selected?.fields} after={draftFields} beforeLabel="복제 원본" afterLabel="초안" /> : <p className="error">{draftError}</p>}</Dialog>
    <Dialog open={discardOpen && Boolean(draft)} title="스키마 초안 삭제" onClose={() => setDiscardOpen(false)} className="input-schema-settings schema-dialog"><p>저장하지 않은 필드 정의와 초안 입력을 버립니다. 삭제한 초안은 복구할 수 없으며 저장된 스키마와현재 접수 기준은 바뀌지 않습니다.</p><div className="schema-actions"><button type="button" className="secondary" onClick={() => setDiscardOpen(false)}>계속 작성</button><button type="button" className="primary" disabled={busy} onClick={() => { if (!busy) { setDraft(null); setEditingField(null); setDraftCompareOpen(false); setDiscardOpen(false); } }}>초안 삭제 확인</button></div></Dialog>
    <Dialog open={view === "history"} title="스키마 적용 이력" onClose={() => setView("")} className="input-schema-settings schema-dialog"><p className="schema-help">최근 100건</p><div className="table-wrap"><Table><thead><tr><th scope="col">시각</th><th scope="col">이전 버전</th><th scope="col">적용 버전</th><th scope="col">관리자</th></tr></thead><tbody>{state.history.map(item => <tr key={item.id}><td>{timestamp(item.created_at)}</td><td>{schemaVersionName(versions.find(version => version.id === item.previous_version_id))}</td><td>{schemaVersionName(versions.find(version => version.id === item.active_version_id))}</td><td>{item.actor_id}</td></tr>)}</tbody></Table>{!state.history.length && <p className="muted">{state.loading ? "불러오는 중…" : "표시할 적용 이력이 없습니다."}</p>}</div></Dialog>
    <Dialog open={view === "compare" && Boolean(selected)} title="현재 적용 버전과 비교" onClose={() => setView("")} className="input-schema-settings schema-dialog">{selected && <SchemaDiff before={active?.fields} after={selected.fields} beforeLabel={schemaVersionName(active)} afterLabel={schemaVersionName(selected)} />}</Dialog>
    <Dialog open={view === "technical" && Boolean(selected)} title="스키마 기술정보" onClose={() => setView("")} className="input-schema-settings schema-dialog">{selected && <dl className="schema-identifiers"><dt>버전 ID</dt><dd>{selected.id}</dd><dt>필드 정의 지문</dt><dd>{selected.content_hash}</dd><dt>등록자 / 시각</dt><dd>{selected.created_by} · {timestamp(selected.created_at)}</dd><dt>복제 원본</dt><dd>{selected.parent_id || "기본 버전"}</dd></dl>}</Dialog>
  </div>;
}

export function InputSchemaMetadata({ metadata }) {
  const [open, setOpen] = useState(false); const [version, setVersion] = useState(null); const [error, setError] = useState(""); const [attempt, setAttempt] = useState(0);
  useEffect(() => { setOpen(false); setVersion(null); setError(""); }, [metadata?.version_id]);
  useEffect(() => {
    if (!open || !metadata?.version_id) return;
    const request = new AbortController(); let current = true; setError(""); setVersion(null);
    api.inputSchema(metadata.version_id, { signal: request.signal }).then(value => { if (metadata.content_hash && value.content_hash !== metadata.content_hash) throw new Error("schema_hash_mismatch"); if (current) setVersion(value); }).catch(() => { if (current) setError("당시 필드 정의를 조회하지 못했거나 기록된 지문과 일치하지 않습니다."); });
    return () => { current = false; request.abort(); };
  }, [open, metadata?.version_id, metadata?.content_hash, attempt]);
  return <section className="schema-analysis-metadata"><h3>입력 스키마 · 접수 당시 기준</h3>{metadata ? <><p>v{metadata.version_number ?? "미기록"} · {metadata.field_count ?? "미기록"}개 필드 · {metadata.selection_origin === "ingest" ? "접수 시 고정" : metadata.selection_origin === "legacy_default" ? "이전 기본 기준" : metadata.selection_origin || "고정 시점 미기록"}</p><p className="schema-help">필드 정보는 실행 이력에만 기록하며, 설명을 LLM 입력에 추가하지 않습니다.</p><dl><dt>버전 ID</dt><dd>{metadata.version_id || "미기록"}</dd><dt>필드 정의 지문</dt><dd>{metadata.content_hash || "미기록"}</dd></dl>{metadata.version_id && <button type="button" className="secondary" aria-expanded={open} onClick={() => setOpen(value => !value)}>{open ? "당시 필드 정의 닫기" : "당시 필드 정의 보기"}</button>}{open && (error ? <div className="error" role="alert">{error}<button type="button" className="secondary" onClick={() => setAttempt(value => value + 1)}>다시 조회</button></div> : version ? <SchemaFields fields={version.fields} /> : <p role="status">필드 정의를 불러오는 중…</p>)}</> : <p>스키마 버전 미기록 · 이전 분석의 당시 정의를 현재 설정으로 추정하지 않습니다.</p>}</section>;
}
