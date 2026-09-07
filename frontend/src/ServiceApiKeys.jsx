import { useEffect, useRef, useState } from "react";
import { api } from "./api.js";
import { formatDate } from "./analysisView.js";
import { createServiceKeysController, emptyServiceKeysState } from "./serviceApiKeys.js";
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
  return <section className="panel service-key-issued" aria-labelledby="issued-key-title"><h2 id="issued-key-title">발급 완료 · 원문은 한 번만 표시됩니다</h2><p><strong>{issued.item.name}</strong> · {issued.item.source_system}</p><p>새로고침·탭 이동·이 화면을 닫은 뒤에는 다시 조회할 수 없습니다. 키를 잃으면 폐기하고 새로 발급하세요.</p>
    <label htmlFor="issued-service-api-key">발급된 API Key 원문</label><textarea id="issued-service-api-key" ref={input} value={issued.api_key} readOnly rows={3} autoComplete="off" spellCheck={false} />
    <div className="service-key-actions"><button type="button" className="secondary" onClick={copy}>키 복사</button><button type="button" className="primary" onClick={onClose}>안전하게 보관했습니다 · 원문 닫기</button></div><p role="status">{message}</p>
  </section>;
}

export function ServiceApiKeysView({ state, controller }) {
  const busy = Boolean(state.busy); const blocked = busy || state.loading || state.needsRefresh || !state.catalog;
  const update = field => event => controller?.update(field, event.target.value);
  function scope(scope, checked) { controller?.update("scopes", checked ? [...state.draft.scopes, scope] : state.draft.scopes.filter(value => value !== scope)); }
  function revoke(item) {
    if (window.confirm(`'${item.name}' API Key를 영구 폐기할까요? Source System: ${item.source_system}. 이 키를 사용하는 연동이 중단되며 다시 활성화할 수 없습니다.`)) void controller?.revoke(item);
  }
  return <div className="service-api-keys">
    <section className="panel service-key-overview"><div className="service-key-heading"><div><h2>서비스 API Key</h2><p>운영 수집기·분석가 시스템에서 이 서비스에 접근할 때 사용하는 키입니다. LLM 제공자에 접속하는 API Key와 다릅니다.</p><p>서비스 연동에는 이 화면에서 발급해 DB에 등록한 키만 사용합니다.</p></div><button type="button" className="secondary" disabled={busy || state.loading} onClick={() => controller?.refresh()}>{state.loading ? "조회 중…" : "키 목록 새로고침"}</button></div><p className="service-key-warning">만료일이 없습니다. 폐기 전까지 사용할 수 있으므로 키를 비밀 저장소에 보관하고 불필요한 키는 폐기하세요. 폐기하면 다시 활성화할 수 없습니다. 원문은 발급 응답에서만 표시하며 서버에는 해시로 저장합니다.</p></section>
    {state.error && <div className="error" role="alert">{state.error}</div>}{state.notice && <div className="notice" role="status">{state.notice}</div>}
    {state.issued && <IssuedServiceKey key={state.issued.item.id} issued={state.issued} onClose={() => controller?.closeIssued()} />}
    <div className="service-key-layout">
      <section className="panel service-key-list" aria-label="발급된 서비스 API Key 목록"><div className="panel-head"><h2>발급된 키</h2><span>{state.catalog ? `${state.catalog.items.length}개` : "미조회"}</span></div>
        {!state.catalog ? <p className="service-key-empty">{state.loading ? "키 목록을 불러오는 중…" : "목록을 확인하지 못했습니다. 새로고침해 주세요."}</p> : !state.catalog.items.length ? <p className="service-key-empty">발급된 서비스 API Key가 없습니다.</p> : state.catalog.items.map(item => <article key={item.id}>
          <div className="service-key-heading"><div><strong>{item.name}</strong><code>{item.key_prefix}…</code></div><span className={`status ${item.revoked_at ? "status-disabled" : "status-completed"}`}>{item.revoked_at ? "폐기됨" : "사용 가능"}</span></div>
          <dl><dt>Source System</dt><dd>{item.source_system}</dd><dt>권한</dt><dd>{item.scopes.join(" · ")}</dd><dt>발급</dt><dd>{formatDate(item.created_at)}</dd><dt>최근 인증 성공</dt><dd>{item.last_used_at ? formatDate(item.last_used_at) : "사용 기록 없음"}<small>최대 60초 간격으로 갱신</small></dd>{item.revoked_at && <><dt>폐기</dt><dd>{formatDate(item.revoked_at)}</dd></>}</dl>
          <div className="service-key-actions"><button type="button" className="secondary small" disabled={blocked || Boolean(item.revoked_at)} onClick={() => controller?.edit(item)} aria-label={`${item.name} 이름 변경`}>이름 변경</button><button type="button" className="secondary small" disabled={blocked || Boolean(item.revoked_at)} onClick={() => revoke(item)} aria-label={`${item.name} 영구 폐기`}>영구 폐기</button></div>
        </article>)}
        {state.needsRefresh && state.catalog && <p className="service-key-empty">마지막 조회 목록입니다. 변경 전 최신 목록을 다시 확인하세요.</p>}
      </section>
      <div className="service-key-forms">
        {state.editing && <form className="panel service-key-form" onSubmit={event => { event.preventDefault(); void controller?.rename(); }} noValidate><h2>API Key 이름 변경</h2><p>Source System과 권한은 발급 후 바꿀 수 없습니다. 변경이 필요하면 새 키를 발급하세요.</p><label>새 키 이름<input value={state.editName} disabled={busy} onChange={event => controller?.updateName(event.target.value)} maxLength={120} /></label><div className="service-key-actions"><button className="primary" disabled={blocked}>이름 저장</button><button type="button" className="secondary" disabled={busy} onClick={() => controller?.cancelEdit()}>이름 변경 취소</button></div></form>}
        <form className="panel service-key-form" onSubmit={event => { event.preventDefault(); void controller?.issue(); }} noValidate><h2>새 서비스 API Key 발급</h2><fieldset disabled={busy || Boolean(state.issued)}><label>키 이름<input value={state.draft.name} onChange={update("name")} maxLength={120} autoComplete="off" placeholder="연동 서비스 또는 사용 목적" /></label><label>Source System<input value={state.draft.source_system} onChange={update("source_system")} maxLength={120} autoComplete="off" spellCheck={false} placeholder="waf-collector" /><small>연동 데이터의 소유 범위 · 발급 후 변경 불가</small></label><fieldset className="service-key-scopes"><legend>권한 · 하나 이상 선택</legend>{[["ingest", "ingest · 이벤트 접수와 해당 source 결과 조회"], ["review", "review · 해당 source의 분석가 리뷰 등록"]].map(([value, label]) => <label key={value}><input type="checkbox" checked={state.draft.scopes.includes(value)} onChange={event => scope(value, event.target.checked)} /><span>{label}</span></label>)}</fieldset></fieldset><p>같은 Source System의 키는 동일한 데이터 범위를 공유합니다. 관리자 권한은 발급할 수 없습니다.</p><p>Source System은 영문·숫자로 시작하는 1~120자의 영문·숫자·._-만 허용합니다. admin-ui와 waf-internal-로 시작하는 값은 내부 예약어입니다.</p><div className="service-key-actions"><button className="primary" disabled={blocked || Boolean(state.issued)}>{state.busy === "issue" ? "발급 중…" : "API Key 발급"}</button></div>{state.issued && <p>현재 발급된 키를 보관하고 원문 표시를 닫은 뒤 다음 키를 발급하세요.</p>}</form>
      </div>
    </div>
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
