import { useEffect, useId, useRef, useState } from "react";
import { api } from "./api.js";
import { formatDate } from "./analysisView.js";
import { createServiceKeysController, emptyServiceKeysState } from "./serviceApiKeys.js";
import HelpTooltip from "./HelpTooltip.jsx";
import Dialog from "./Dialog.jsx";
import "./serviceApiKeys.css";

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
  const sourceId = useId();
  const [creating, setCreating] = useState(false);
  const [editing, setEditing] = useState(false);
  const [deleting, setDeleting] = useState(null);
  const [technical, setTechnical] = useState(null);
  const busy = Boolean(state.busy); const blocked = busy || state.loading || state.needsRefresh || !state.catalog;
  const update = field => event => controller?.update(field, event.target.value);
  function scope(scope, checked) { controller?.update("scopes", checked ? [...state.draft.scopes, scope] : state.draft.scopes.filter(value => value !== scope)); }
  return <div className="service-api-keys">
    <section className="panel service-key-overview"><div className="service-key-heading"><div><h2>서비스 API 키</h2><p>수집기·분석가 시스템의 접근 키를 발급합니다. LLM 제공자 인증 키와는 별개입니다.</p></div><div className="service-key-actions"><button type="button" className="secondary" disabled={busy || state.loading} onClick={() => controller?.refresh()}>새로고침</button><button type="button" className="primary" disabled={blocked || Boolean(state.issued)} onClick={() => setCreating(true)}>키 발급</button></div></div></section>
    {state.error && <div className="error" role="alert">{state.error}</div>}{state.notice && <div className="notice" role="status">{state.notice}</div>}
    <Dialog open={Boolean(state.issued)} title="API 키 발급 완료" onClose={() => controller?.closeIssued()} className="service-api-keys service-key-dialog">{state.issued && <IssuedServiceKey key={state.issued.item.id} issued={state.issued} onClose={() => controller?.closeIssued()} />}</Dialog>
    <div className="service-key-layout service-key-list-first">
      <section className="panel service-key-list" aria-label="발급된 서비스 API Key 목록"><div className="panel-head"><h2>발급된 키</h2><span>{state.catalog ? `${state.catalog.items.length}개` : "미조회"}</span></div>
        {!state.catalog ? <p className="service-key-empty">{state.loading ? "키 목록을 불러오는 중…" : "목록을 확인하지 못했습니다. 새로고침해 주세요."}</p> : !state.catalog.items.length ? <p className="service-key-empty">발급된 서비스 API Key가 없습니다.</p> : state.catalog.items.map(item => <article key={item.id}>
          <div className="service-key-heading"><strong>{item.name}</strong><span className={`status ${item.revoked_at ? "status-disabled" : "status-completed"}`}>{item.revoked_at ? "사용 중지" : "사용 가능"}</span></div>
          <dl><dt>연동 시스템</dt><dd>{item.source_system}</dd><dt>권한</dt><dd>{item.scopes.map(value => value === "ingest" ? "분석 접수·조회" : "분석가 판정 등록").join(" · ")}</dd><dt>최근 인증</dt><dd>{item.last_used_at ? formatDate(item.last_used_at) : "사용 기록 없음"}</dd></dl>
          <div className="service-key-actions"><button type="button" className="secondary small" disabled={blocked || Boolean(item.revoked_at)} onClick={() => { if (state.editing?.id !== item.id) controller?.edit(item); setEditing(true); }} aria-label={`${item.name} 이름 변경`}>이름 변경</button><button type="button" className="secondary small" onClick={() => setTechnical(item)}>기술정보</button><button type="button" className="secondary small" disabled={blocked} onClick={() => setDeleting(item)} aria-label={`${item.name} 삭제`}>삭제</button></div>
        </article>)}
        {state.needsRefresh && state.catalog && <p className="service-key-empty">마지막 조회 목록입니다. 변경 전 최신 목록을 다시 확인하세요.</p>}
      </section>
    </div>
    <Dialog open={creating && !state.issued} title="새 API 키 발급" onClose={() => { if (!busy) setCreating(false); }} className="service-api-keys service-key-dialog"><form className="service-key-form" onSubmit={async event => { event.preventDefault(); if (await controller?.issue()) setCreating(false); }} noValidate><p className="service-key-warning">만료일이 없습니다. 원문은 발급 직후 한 번만 표시되므로 안전하게 보관하세요.</p><fieldset disabled={busy}><label>키 이름<input value={state.draft.name} onChange={update("name")} maxLength={120} autoComplete="off" placeholder="연동 서비스 또는 사용 목적" /></label><label htmlFor={sourceId}><span>연동 시스템<HelpTooltip label="연동 시스템">API의 source_system 값입니다. 같은 값을 사용하는 키들은 동일한 데이터 범위를 공유하며 발급 후 변경할 수 없습니다. 영문·숫자로 시작하는 1~120자의 영문·숫자·._-를 사용하세요. admin-ui와 waf-internal-로 시작하는 값은 예약어입니다.</HelpTooltip></span><input id={sourceId} value={state.draft.source_system} onChange={update("source_system")} maxLength={120} autoComplete="off" spellCheck={false} placeholder="waf-collector" /></label><fieldset className="service-key-scopes"><legend>권한 · 하나 이상 선택</legend><p className="service-key-empty">선택한 연동 시스템만 접근 · 관리자 권한 제외</p>{[["ingest", "분석 접수·조회"], ["review", "분석가 판정 등록"]].map(([value, label]) => <label key={value}><input type="checkbox" checked={state.draft.scopes.includes(value)} onChange={event => scope(value, event.target.checked)} /><span>{label}</span></label>)}</fieldset></fieldset>{state.error && <p className="error" role="alert">{state.error}</p>}<div className="service-key-actions"><button type="button" className="secondary" disabled={busy} onClick={() => setCreating(false)}>닫기</button><button className="primary" disabled={blocked}>{state.busy === "issue" ? "발급 중…" : "API 키 발급"}</button></div></form></Dialog>
    <Dialog open={editing && Boolean(state.editing)} title="키 이름 변경" onClose={() => { if (!busy) setEditing(false); }} className="service-api-keys service-key-dialog">{state.editing && <form className="service-key-form" onSubmit={async event => { event.preventDefault(); if (await controller?.rename()) setEditing(false); }}><label>새 키 이름<input value={state.editName} disabled={busy} onChange={event => controller?.updateName(event.target.value)} maxLength={120} /></label>{state.error && <p className="error" role="alert">{state.error}</p>}<div className="service-key-actions"><button type="button" className="secondary" disabled={busy} onClick={() => setEditing(false)}>닫기</button><button className="primary" disabled={blocked}>이름 저장</button></div></form>}</Dialog>
    <Dialog open={Boolean(deleting)} title="API 키 삭제" onClose={() => { if (!busy) setDeleting(null); }} className="service-api-keys service-key-dialog">{deleting && <><p><strong>{deleting.name}</strong> · {deleting.source_system}</p><p className="service-key-warning">이 키로 더 이상 API를 호출할 수 없으며 목록과 키별 대시보드에서 사라집니다. 분석 결과와 감사 이력은 보존됩니다. 삭제는 되돌릴 수 없습니다.</p>{state.error && <p className="error" role="alert">{state.error}</p>}<div className="service-key-actions"><button type="button" className="secondary" disabled={busy} onClick={() => setDeleting(null)}>취소</button><button type="button" className="primary" disabled={blocked} onClick={async () => { if (await controller?.remove(deleting)) setDeleting(null); }}>{busy ? "삭제 중…" : "API 키 삭제"}</button></div></>}</Dialog>
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
