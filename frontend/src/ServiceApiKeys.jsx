import { useEffect, useId, useRef, useState } from "react";
import { api } from "./api.js";
import { formatDate } from "./analysisView.js";
import { createServiceKeysController, emptyServiceKeysState } from "./serviceApiKeys.js";
import HelpTooltip from "./HelpTooltip.jsx";
import Dialog from "./Dialog.jsx";
import MoreActions from "./MoreActions.jsx";
import DataTable from "./DataTable.jsx";
import Pagination from "./Pagination.jsx";
import ServiceKeyDelete from "./ServiceKeyDelete.jsx";
import RegistryFilters from "./RegistryFilters.jsx";
import "./serviceApiKeys.css";
import { SettingsSectionHeader, SettingsSummary } from "./SettingsWorkspace.jsx";
import NavigationAction from "./NavigationAction.jsx";
import { useConsolePreferences } from "./consolePreferences.jsx";

export function IssuedServiceKey({ issued, onClose }) {
  const [message, setMessage] = useState("");
  const input = useRef(null);
  const active = useRef(true);
  useEffect(() => { active.current = true; input.current?.focus(); return () => { active.current = false; }; }, []);
  async function copy() {
    try {
      if (!navigator.clipboard?.writeText) throw new Error("clipboard_unavailable");
      await navigator.clipboard.writeText(issued.api_key);
      if (active.current) setMessage("키를 복사했습니다. 안전한 비밀 저장소에 보관하세요.");
    } catch { if (active.current) { setMessage("자동 복사를 사용할 수 없습니다. 아래 원문을 선택해 직접 복사하세요."); input.current?.focus(); input.current?.select(); } }
  }
  return <section className="panel service-key-issued" aria-labelledby="issued-key-title"><h2 id="issued-key-title">발급 완료 · 원문은 한 번만 표시됩니다</h2><p><strong>{issued.item.name}</strong> · {issued.item.source_system}</p><p>새로고침·탭 이동·이 화면을 닫은 뒤에는 다시 조회할 수 없습니다. 키를 잃으면 삭제하고 새로 발급하세요.</p>
    <label htmlFor="issued-service-api-key">발급된 API Key 원문</label><textarea id="issued-service-api-key" ref={input} value={issued.api_key} readOnly rows={3} autoComplete="off" spellCheck={false} />
    <div className="service-key-actions"><button type="button" className="secondary" onClick={copy}>키 복사</button><button type="button" className="primary" onClick={onClose}>안전하게 보관했습니다 · 원문 닫기</button></div><p role="status">{message}</p>
  </section>;
}

export function ServiceApiKeysView({ state, controller }) {
  const { locale } = useConsolePreferences(); const w = (ko, en) => locale === "en" ? en : ko;
  const sourceId = useId();
  const [creating, setCreating] = useState(false);
  const [editing, setEditing] = useState(false);
  const [deleting, setDeleting] = useState(null);
  const [technical, setTechnical] = useState(null);
  const [offset, setOffset] = useState(0);
  const [search, setSearch] = useState(""), [purpose, setPurpose] = useState("");
  const items = (state.catalog?.items || []).filter(item => (!purpose || (item.purpose || "production") === purpose) && `${item.name} ${item.source_system}`.toLowerCase().includes(search.trim().toLowerCase()));
  const pageOffset = Math.min(offset, Math.max(0, (Math.ceil(items.length / 10) - 1) * 10));
  const busy = Boolean(state.busy); const blocked = busy || state.loading || state.needsRefresh || !state.catalog;
  const activeKeys = state.catalog?.items.filter(item => !item.revoked_at);
  const update = field => event => controller?.update(field, event.target.value);
  function scope(scope, checked) { controller?.update("scopes", checked ? [...state.draft.scopes, scope] : state.draft.scopes.filter(value => value !== scope)); }
  return <div className="service-api-keys settings-workspace">
    <SettingsSectionHeader icon="key" title={w("서비스 API 키", "Service API keys")} description={w("연동 목적과 접근 범위를 구분해 키를 관리합니다. LLM 제공자 인증 키와는 별개입니다.", "Manage integration keys by purpose and access scope. These are separate from LLM provider credentials.")} actions={<><NavigationAction href="#connect/production-api">{w("API 정의서", "API reference")}</NavigationAction><button type="button" className="secondary" disabled={busy || state.loading} onClick={() => controller?.refresh()}>{w("새로고침", "Refresh")}</button><button type="button" className="primary" disabled={blocked || Boolean(state.issued)} onClick={() => setCreating(true)}>{w("키 발급", "Issue key")}</button></>} />
    <SettingsSummary label={w("API 키 현황", "API key summary")} items={[
      { label: w("사용 중인 키", "Active keys"), value: activeKeys?.length, note: w("인증 가능한 키", "Keys available for authentication"), icon: "key" },
      { label: "Production", value: activeKeys?.filter(item => (item.purpose || "production") === "production").length, note: w("운영 분석 전용", "Production analyses only"), tone: "blue", icon: "server" },
      { label: "Test", value: activeKeys?.filter(item => item.purpose === "test").length, note: w("테스트 분석 전용", "Test analyses only"), tone: "teal", icon: "test" },
    ]} />
    {state.error && <div className="error" role="alert">{state.error}</div>}{state.notice && <div className="notice" role="status">{state.notice}</div>}
    <Dialog open={Boolean(state.issued)} title="API 키 발급 완료" onClose={() => controller?.closeIssued()} className="service-api-keys service-key-dialog">{state.issued && <IssuedServiceKey key={state.issued.item.id} issued={state.issued} onClose={() => controller?.closeIssued()} />}</Dialog>
    <div className="service-key-layout service-key-list-first">
      <section className="panel service-key-list" aria-label={w("발급된 서비스 API Key 목록", "Issued service API keys")}><div className="panel-head"><h2>{w("발급된 키", "Issued keys")}</h2><span>{state.catalog ? w(`${state.catalog.items.length}개`, `${state.catalog.items.length} keys`) : w("미조회", "Not loaded")}</span></div>
        <RegistryFilters search={search} onSearch={value => { setSearch(value); setOffset(0); }} placeholder={w("키 이름 · 연동 시스템", "Key name or source system")} filters={[{label:w("용도 필터", "Purpose"), value:purpose, onChange:value => { setPurpose(value); setOffset(0); }, options:[["", w("모든 용도", "All purposes")], ["production", "Production"], ["test", "Test"]]}]} />
        {!state.catalog ? <p className="service-key-empty">{state.loading ? "키 목록을 불러오는 중…" : "목록을 확인하지 못했습니다. 새로고침해 주세요."}</p> : <><DataTable label="발급된 API 키" data={items.slice(pageOffset, pageOffset + 10)} columns={[
          { id: "name", header: w("키 이름", "Key name"), minWidth: 190, render: item => <><strong>{item.name}</strong><small>{item.source_system}</small>{item.revoked_at && <span className="status status-disabled">{w("사용 중지", "Disabled")}</span>}</> },
          { id: "purpose", header: w("용도", "Purpose"), minWidth: 110, render: item => <span className={`status purpose-${item.purpose || "production"}`}>{item.purpose === "test" ? "Test" : "Production"}</span> },
          { id: "scope", header: w("권한", "Scope"), minWidth: 180, render: item => item.scopes.map(value => value === "ingest" ? w("분석 접수·조회", "Request and read analyses") : w("피드백 등록", "Submit feedback")).join(" / ") },
          { id: "used", header: w("최근 인증", "Last authenticated"), minWidth: 150, render: item => item.last_used_at ? formatDate(item.last_used_at) : w("사용 기록 없음", "Never used") },
          { id: "created", header: w("발급일", "Created"), minWidth: 135, render: item => formatDate(item.created_at) },
          { id: "actions", header: w("관리", "Actions"), width: 200, minWidth: 200, render: item => <div className="row-actions"><button type="button" className="text-button" disabled={blocked || Boolean(item.revoked_at)} onClick={() => { if (state.editing?.id !== item.id) controller?.edit(item); setEditing(true); }} aria-label={`${item.name} ${w("이름 변경", "rename")}`}>{w("이름 변경", "Rename")}</button><button type="button" className="text-button" disabled={blocked} onClick={() => setDeleting(item)} aria-label={`${item.name} ${w("삭제", "delete")}`}>{w("삭제", "Delete")}</button><MoreActions label={`${item.name} ${w("관리", "actions")}`}><button type="button" onClick={() => setTechnical(item)}>{w("기술정보", "Technical details")}</button></MoreActions></div> },
        ]} empty="발급된 서비스 API Key가 없습니다." />{items.length > 10 && <Pagination label="API 키 목록 페이지" total={items.length} offset={pageOffset} limit={10} onOffsetChange={setOffset} disabled={busy} unit="개" />}</>}
        {state.needsRefresh && state.catalog && <p className="service-key-empty">마지막 조회 목록입니다. 변경 전 최신 목록을 다시 확인하세요.</p>}
      </section>
    </div>
    <p className="settings-inline-notice">{w("서비스 API 키는 LLM 제공자 인증 키와 별개입니다. 원문과 Agent 실행 이력은 서비스 키로 조회할 수 없습니다.", "Service API keys are separate from LLM provider credentials. Raw events and Agent traces are not accessible with service keys.")}</p>
    <Dialog open={creating && !state.issued} title={w("새 API 키 발급", "Issue API key")} onClose={() => { if (!busy) setCreating(false); }} className="service-api-keys service-key-dialog">
      <form className="service-key-form" onSubmit={async event => { event.preventDefault(); if (await controller?.issue()) setCreating(false); }} noValidate>
        <p className="service-key-warning">{w("만료일이 없습니다. 원문은 발급 직후 한 번만 표시되므로 안전하게 보관하세요.", "Keys do not expire. The secret is shown only once after issuance; store it securely.")}</p>
        <fieldset disabled={busy}><label>{w("용도", "Purpose")}<select aria-label={w("용도", "Purpose")} value={state.draft.purpose || "production"} onChange={update("purpose")}><option value="production">{w("Production · 운영 분석", "Production analyses")}</option><option value="test">{w("Test · 테스트 분석", "Test analyses")}</option></select></label>
          <p className="ux-muted">{w("발급 후 용도는 변경할 수 없습니다. Test 키는 Test 모델을 사용하고 테스트 결과에 저장합니다.", "Purpose cannot be changed after issuance. Test keys use Test models and store results under Tests.")}</p>
          <label>{w("키 이름", "Key name")}<input value={state.draft.name} onChange={update("name")} maxLength={120} autoComplete="off" placeholder={w("연동 서비스 또는 사용 목적", "Integration service or purpose")} /></label>
          <label htmlFor={sourceId}><span>{w("연동 시스템", "Source system")}<HelpTooltip label={w("연동 시스템", "Source system")}>{w("API의 source_system 값입니다. 같은 용도와 연동 시스템을 사용하는 키들은 동일한 데이터 범위를 공유하며 발급 후 변경할 수 없습니다. 영문·숫자로 시작하는 1~120자의 영문·숫자·._-를 사용하세요. admin-ui와 waf-internal-로 시작하는 값은 예약어입니다.", "The API source_system value. Keys with the same purpose and source system share a data scope; this value cannot be changed after issuance. Use 1–120 ASCII letters, digits, periods, underscores or hyphens, starting with a letter or digit. admin-ui and the waf-internal- prefix are reserved.")}</HelpTooltip></span><input id={sourceId} value={state.draft.source_system} onChange={update("source_system")} maxLength={120} autoComplete="off" spellCheck={false} placeholder="waf-collector" /></label>
          <fieldset className="service-key-scopes"><legend>{w("권한 · 하나 이상 선택", "Scope — choose at least one")}</legend><p className="service-key-empty">{w("선택한 연동 시스템만 접근 · 관리자 권한 제외", "Access is limited to the source system; no administrator privileges")}</p>{[["ingest", w("분석 접수·조회", "Request and read analyses")], ["review", w("피드백 등록", "Submit feedback")]].map(([value, label]) => <label key={value}><input type="checkbox" checked={state.draft.scopes.includes(value)} onChange={event => scope(value, event.target.checked)} /><span>{label}</span></label>)}</fieldset>
        </fieldset>
        {state.error && <p className="error" role="alert">{state.error}</p>}<div className="service-key-actions"><button type="button" className="secondary" disabled={busy} onClick={() => setCreating(false)}>{w("닫기", "Close")}</button><button type="submit" className="primary" disabled={blocked}>{state.busy === "issue" ? w("발급 중…", "Issuing…") : w("API 키 발급", "Issue API key")}</button></div>
      </form>
    </Dialog>
    <Dialog open={editing && Boolean(state.editing)} title="키 이름 변경" onClose={() => { if (!busy) setEditing(false); }} className="service-api-keys service-key-dialog">{state.editing && <form className="service-key-form" onSubmit={async event => { event.preventDefault(); if (await controller?.rename()) setEditing(false); }}><label>새 키 이름<input value={state.editName} disabled={busy} onChange={event => controller?.updateName(event.target.value)} maxLength={120} /></label>{state.error && <p className="error" role="alert">{state.error}</p>}<div className="service-key-actions"><button type="button" className="secondary" disabled={busy} onClick={() => setEditing(false)}>닫기</button><button type="submit" className="primary" disabled={blocked}>이름 저장</button></div></form>}</Dialog>
    <Dialog open={Boolean(deleting)} title="API 키 삭제" onClose={() => { if (!busy) setDeleting(null); }} className="service-api-keys service-key-dialog">{deleting && <ServiceKeyDelete key={deleting.id} item={deleting} controller={controller} busy={busy} blocked={blocked} error={state.error} onClose={() => setDeleting(null)} />}</Dialog>
    <Dialog open={Boolean(technical)} title="API 키 기술정보" onClose={() => setTechnical(null)} className="service-api-keys service-key-dialog">{technical && <dl><dt>키 ID</dt><dd>{technical.id}</dd><dt>키 접두부</dt><dd>{technical.key_prefix}…</dd><dt>발급</dt><dd>{formatDate(technical.created_at)}</dd><dt>최근 인증 성공</dt><dd>{technical.last_used_at ? formatDate(technical.last_used_at) : "사용 기록 없음"}<p>최대 60초 간격으로 갱신되며 분석 완료 시각과는 다릅니다.</p></dd>{technical.revoked_at && <><dt>사용 중지</dt><dd>{formatDate(technical.revoked_at)}</dd></>}</dl>}</Dialog>
  </div>;
}

export default function ServiceApiKeys() {
  const [state, setState] = useState(emptyServiceKeysState); const controller = useRef(null);
  useEffect(() => {
    const instance = createServiceKeysController({ api, onChange: setState }); controller.current = instance; void instance.refresh();
    // Do not resurrect the one-time value when a browser restores a cached page.
    const discardIssued = () => { if (instance.getState().issued) instance.closeIssued(); };
    window.addEventListener("pagehide", discardIssued);
    return () => { window.removeEventListener("pagehide", discardIssued); instance.dispose(); if (controller.current === instance) controller.current = null; };
  }, []);
  return <ServiceApiKeysView state={state} controller={controller.current} />;
}
