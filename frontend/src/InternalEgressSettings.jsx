import { useEffect, useId, useRef, useState } from "react";
import { api } from "./api.js";
import { createInternalEgressController, emptyInternalEgressState, internalTargetAddress, sameInternalTarget } from "./internalEgress.js";
import HelpTooltip from "./HelpTooltip.jsx";
import Dialog from "./Dialog.jsx";
import "./internalEgressSettings.css";

const profileStatus = { production: "운영 사용", verified: "검증 완료", draft: "미검증", disabled: "사용 중지" };

export function InternalEgressView({ state, controller, addressRef }) {
  const addressId = useId();
  const [editorOpen, setEditorOpen] = useState(false); const [deleting, setDeleting] = useState(null); const [technical, setTechnical] = useState(null);
  const [discardOpen, setDiscardOpen] = useState(false);
  const busy = Boolean(state.busy);
  const blocked = busy || state.loading || state.needsRefresh || !state.items;
  const latest = state.items?.find((item) => item.id === state.editing?.id);
  const inUse = Boolean(latest?.in_use_profiles?.length);
  const targetChanged = latest && !sameInternalTarget(state.draft, latest);
  const update = (field) => (event) => controller?.update(field, event.target.value);

  return <div className="internal-egress-settings">
    <section className="panel egress-overview">
      <div className="egress-heading"><div><h2>내부 연결 허용</h2><p>vLLM에서 사용할 서버 주소를 등록합니다. OpenAI 설정에는 영향이 없으며 방화벽 설정·연결 검증을 대신하지 않습니다.</p></div><div className="egress-actions">
        <button type="button" className="secondary" disabled={busy || state.loading} onClick={() => controller?.refresh()}>새로고침</button><button type="button" className="primary" disabled={blocked} onClick={() => setEditorOpen(true)}>{state.editing ? "편집 계속" : "내부 대상 추가"}</button></div></div>
    </section>

    {state.error && <div className="error" role="alert">{state.error}</div>}
    {state.notice && <div className="notice" role="status">{state.notice}</div>}

    <div className="egress-layout egress-list-first">
      <section className="panel egress-list" aria-label="내부 허용 대상 목록">
        <div className="panel-head"><h2>내부 허용 대상</h2><span>{state.items ? `${state.items.length}개` : "미조회"}</span></div>
        {!state.items ? <p className="egress-empty" role="status">{state.loading ? "허용 대상을 불러오는 중…" : "목록을 확인하지 못했습니다. 새로고침해 주세요."}</p>
          : !state.items.length ? <p className="egress-empty">등록된 내부 대상이 없습니다. vLLM을 사용하려면 먼저 IP와 포트를 등록하세요.</p>
            : <div className="egress-targets">{state.items.map((item) => <article key={item.id} className={state.editing?.id === item.id ? "egress-selected" : ""}>
              <div className="egress-target-heading"><strong>{internalTargetAddress(item)}</strong><span className="egress-use">{item.in_use_profiles.length ? `사용 중 · ${item.in_use_profiles.length}개 프로필` : "연결된 활성 프로필 없음"}</span></div>
              <p className="egress-description">{item.description || "설명 없음"}</p>
              {item.in_use_profiles.length > 0 && <ul className="egress-profiles" aria-label="사용 중인 vLLM 프로필">{item.in_use_profiles.map((profile) => <li key={profile.id}><span>{profile.name}</span><small>{profileStatus[profile.status] || profile.status}</small></li>)}</ul>}
              <div className="egress-actions"><button type="button" className="secondary small" disabled={blocked} onClick={() => { if (state.editing?.id !== item.id) controller?.edit(item); setEditorOpen(true); }} aria-label={`${internalTargetAddress(item)} 편집`}>편집</button><button type="button" className="secondary small" disabled={blocked || item.in_use_profiles.length > 0} onClick={() => setDeleting(item)} aria-label={`${internalTargetAddress(item)} 삭제`}>삭제</button><button type="button" className="text-button" onClick={() => setTechnical(item)}>기술정보</button>{item.in_use_profiles.length > 0 && <span className="egress-description">설명만 수정 가능 · 주소 변경·삭제는 연결 프로필을 모두 비활성화한 뒤 가능합니다.</span>}</div>
            </article>)}</div>}
        {state.needsRefresh && state.items && <p className="egress-empty">이전 조회 목록입니다. 최신 목록 조회에 성공할 때까지 변경할 수 없습니다.</p>}
      </section>
    </div>
    <Dialog open={editorOpen} title={state.editing ? "내부 대상 편집" : "내부 대상 추가"} onClose={() => { if (!busy) setEditorOpen(false); }} className="internal-egress-settings egress-dialog">
      <form className="egress-editor" onSubmit={async (event) => { event.preventDefault(); if (await controller?.save()) setEditorOpen(false); }} noValidate>
        <fieldset disabled={busy}>
          <label htmlFor={addressId}><span>내부 IP<HelpTooltip label="내부 IP">RFC1918 IPv4와 ULA IPv6의 개별 주소만 허용합니다. URL·포트·대괄호·CIDR을 입력하지 마세요. 호스트명·공인 IP·loopback·link-local은 허용하지 않습니다.</HelpTooltip></span><input id={addressId} ref={addressRef} value={state.draft.ip_address} onChange={update("ip_address")} readOnly={inUse} autoComplete="off" spellCheck={false} placeholder="10.0.0.10 또는 fd00::10" /></label>
          <label>포트<input type="number" value={state.draft.port} onChange={update("port")} readOnly={inUse} min="1" max="65535" step="1" inputMode="numeric" /></label>
          <label>설명 · 선택<input value={state.draft.description} onChange={update("description")} maxLength={500} placeholder="용도 또는 서버 구분" /></label>
        </fieldset>
        {inUse && <p className="egress-warning">사용 중인 대상은 설명만 수정할 수 있습니다.{targetChanged && " 작성 중인 IP·포트가 최신 대상과 다릅니다."}</p>}
        {inUse && targetChanged && <button type="button" className="secondary" disabled={blocked} onClick={() => controller?.restoreTarget()}>IP·포트를 최신 값으로 되돌리기</button>}
        {state.conflict && <section className="egress-conflict" aria-label="대상 변경 검토"><h3>최신 내용 확인</h3>{latest ? <><p>서버의 현재 값: <strong>{internalTargetAddress(latest)}</strong> · 설정 버전 {latest.revision}</p><p>현재 설명: {latest.description || "설명 없음"}</p><p>작성 중인 입력은 위에 그대로 남아 있습니다. 현재 값과 비교하고 저장할 내용을 확인하세요.</p><button type="button" className="secondary" disabled={blocked} onClick={() => controller?.acceptLatest()}>최신 버전 기준으로 계속 편집</button></> : <p>이 대상은 현재 목록에 없습니다. 입력을 확인한 뒤 편집을 취소하거나 필요한 내용을 복사해 새 대상으로 등록하세요.</p>}</section>}
        {state.error && <p className="error" role="alert">{state.error}</p>}<div className="egress-actions"><button type="submit" className="primary" disabled={blocked || state.conflict || (inUse && targetChanged)}>{state.busy === "save" ? "저장 중…" : state.editing ? "수정 저장" : "내부 대상 등록"}</button><button type="button" className="secondary" disabled={busy} onClick={() => setEditorOpen(false)}>닫기 · 입력 유지</button>{state.editing && <button type="button" className="text-button" disabled={busy} onClick={() => setDiscardOpen(true)}>편집 취소</button>}</div>
      </form>
    </Dialog>
    <Dialog open={discardOpen && editorOpen && Boolean(state.editing)} title="내부 대상 편집 취소" onClose={() => setDiscardOpen(false)} className="internal-egress-settings egress-dialog"><p>저장하지 않은 편집 내용을 버립니다. 등록된 내부 IP·포트와 설명은 바뀌지 않습니다.</p><div className="egress-actions"><button type="button" className="secondary" onClick={() => setDiscardOpen(false)}>계속 편집</button><button type="button" className="primary" disabled={busy} onClick={() => { if (!busy) { controller?.cancel(); setEditorOpen(false); setDiscardOpen(false); } }}>편집 취소 확인</button></div></Dialog>
    <Dialog open={Boolean(deleting)} title="내부 허용 대상 삭제" onClose={() => { if (!busy) setDeleting(null); }} className="internal-egress-settings egress-dialog">{deleting && <><p><strong>{internalTargetAddress(deleting)}</strong> 허용 대상을 삭제합니다.</p><p className="egress-warning">이 IP·포트로 이후 vLLM 요청을 보낼 수 없게 됩니다. 이미 전송된 요청은 취소되지 않으며 기존 분석 결과는 보존합니다.</p>{state.error && <p className="error" role="alert">{state.error}</p>}<div className="egress-actions"><button type="button" className="secondary" disabled={busy} onClick={() => setDeleting(null)}>취소</button><button type="button" className="primary" disabled={blocked} onClick={async () => { if (await controller?.remove(deleting)) setDeleting(null); }}>허용 대상 삭제</button></div></>}</Dialog>
    <Dialog open={Boolean(technical)} title="내부 대상 기술정보" onClose={() => setTechnical(null)} className="internal-egress-settings egress-dialog">{technical && <dl><dt>대상 ID</dt><dd>{technical.id}</dd><dt>설정 버전</dt><dd>{technical.revision}</dd></dl>}</Dialog>
  </div>;
}

export default function InternalEgressSettings() {
  const [state, setState] = useState(emptyInternalEgressState);
  const controller = useRef(null);
  const addressRef = useRef(null);
  useEffect(() => {
    const instance = createInternalEgressController({ api, onChange: setState });
    controller.current = instance;
    void instance.refresh();
    return () => { instance.dispose(); if (controller.current === instance) controller.current = null; };
  }, []);
  useEffect(() => { if (state.editing) addressRef.current?.focus(); }, [state.editing?.id]);
  return <InternalEgressView state={state} controller={controller.current} addressRef={addressRef} />;
}
