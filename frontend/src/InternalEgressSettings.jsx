import { useEffect, useRef, useState } from "react";
import { api } from "./api.js";
import { createInternalEgressController, emptyInternalEgressState, internalTargetAddress, sameInternalTarget } from "./internalEgress.js";
import "./internalEgressSettings.css";

const profileStatus = { production: "Production", verified: "Verified", draft: "Draft", disabled: "비활성" };

export function InternalEgressView({ state, controller, addressRef }) {
  const busy = Boolean(state.busy);
  const blocked = busy || state.loading || state.needsRefresh || !state.items;
  const latest = state.items?.find((item) => item.id === state.editing?.id);
  const inUse = Boolean(latest?.in_use_profiles?.length);
  const targetChanged = latest && !sameInternalTarget(state.draft, latest);
  const update = (field) => (event) => controller?.update(field, event.target.value);
  function remove(item) {
    if (window.confirm(`${internalTargetAddress(item)} 허용 대상을 삭제할까요? 이 IP와 포트로 vLLM 요청을 보낼 수 없게 됩니다. 필요하면 나중에 다시 등록할 수 있습니다.`)) void controller?.remove(item);
  }

  return <div className="internal-egress-settings">
    <section className="panel egress-overview">
      <div className="egress-heading"><div><h2>Internal Egress</h2><p>앱에서 vLLM 요청을 보낼 수 있는 내부 IP와 포트를 관리합니다. OpenAI 설정에는 영향을 주지 않습니다.</p></div>
        <button type="button" className="secondary" disabled={busy || state.loading} onClick={() => controller?.refresh()}>{state.loading ? "조회 중…" : "목록 새로고침"}</button></div>
      <p className="egress-policy">허용 범위: RFC1918 IPv4 (10/8, 172.16/12, 192.168/16) · ULA IPv6 (fc00::/7). 개별 IP와 포트만 등록하며 hostname·CIDR·loopback·link-local·공인 IP는 허용하지 않습니다.</p>
      <p className="egress-help">등록만으로 OS·방화벽을 열거나 연결 성공을 확인하지 않습니다. 이 화면은 모델을 호출하지 않습니다.</p>
      <details className="egress-migration"><summary>기존 vLLM 프로필 전환 안내</summary><p>기존 환경변수 허용 목록은 사용하지 않습니다. 처음에는 이 목록이 비어 있으므로 실제 내부 IP와 포트를 등록하세요. LLM 프로필의 <code>vllm.internal</code> 같은 hostname도 등록한 IP 주소로 바꿔야 합니다. 사용 중인 프로필은 먼저 비활성화한 뒤 편집하고 다시 검증해야 합니다.</p></details>
    </section>

    {state.error && <div className="error" role="alert">{state.error}</div>}
    {state.notice && <div className="notice" role="status">{state.notice}</div>}

    <div className="egress-layout">
      <section className="panel egress-list" aria-label="내부 허용 대상 목록">
        <div className="panel-head"><h2>내부 허용 대상</h2><span>{state.items ? `${state.items.length}개` : "미조회"}</span></div>
        {!state.items ? <p className="egress-empty" role="status">{state.loading ? "허용 대상을 불러오는 중…" : "목록을 확인하지 못했습니다. 새로고침해 주세요."}</p>
          : !state.items.length ? <p className="egress-empty">등록된 내부 대상이 없습니다. vLLM을 사용하려면 먼저 IP와 포트를 등록하세요.</p>
            : <div className="egress-targets">{state.items.map((item) => <article key={item.id} className={state.editing?.id === item.id ? "egress-selected" : ""}>
              <div className="egress-target-heading"><strong>{internalTargetAddress(item)}</strong><span className="egress-use">{item.in_use_profiles.length ? `사용 중 · ${item.in_use_profiles.length}개 프로필` : "연결된 활성 프로필 없음"}</span></div>
              <p className="egress-description">{item.description || "설명 없음"}</p>
              {item.in_use_profiles.length > 0 && <ul className="egress-profiles" aria-label="사용 중인 vLLM 프로필">{item.in_use_profiles.map((profile) => <li key={profile.id}><span>{profile.name}</span><small>{profileStatus[profile.status] || profile.status}</small></li>)}</ul>}
              <div className="egress-actions"><button type="button" className="secondary small" disabled={blocked} onClick={() => controller?.edit(item)} aria-label={`${internalTargetAddress(item)} 편집`}>편집</button><button type="button" className="secondary small" disabled={blocked || item.in_use_profiles.length > 0} onClick={() => remove(item)} aria-label={`${internalTargetAddress(item)} 삭제`}>삭제</button><small>revision {item.revision}</small></div>
              {item.in_use_profiles.length > 0 && <p className="egress-help">설명만 수정할 수 있습니다. IP·포트 변경이나 삭제는 연결된 프로필을 모두 비활성화한 뒤 가능합니다.</p>}
            </article>)}</div>}
        {state.needsRefresh && state.items && <p className="egress-empty">이전 조회 목록입니다. 최신 목록 조회에 성공할 때까지 변경할 수 없습니다.</p>}
      </section>

      <form className="panel egress-editor" onSubmit={(event) => { event.preventDefault(); void controller?.save(); }} noValidate>
        <div className="egress-heading"><div><h2>{state.editing ? "내부 대상 편집" : "내부 대상 추가"}</h2><p>{state.editing ? "다른 관리자의 변경 여부를 확인한 뒤 저장합니다." : "같은 IP라도 포트가 다르면 별도 대상으로 등록합니다."}</p></div></div>
        <fieldset disabled={busy}>
          <label>내부 IP<input ref={addressRef} value={state.draft.ip_address} onChange={update("ip_address")} readOnly={inUse} autoComplete="off" spellCheck={false} placeholder="10.0.0.10 또는 fd00::10" aria-describedby="egress-address-help" /></label>
          <label>포트<input type="number" value={state.draft.port} onChange={update("port")} readOnly={inUse} min="1" max="65535" step="1" inputMode="numeric" /></label>
          <p id="egress-address-help" className="egress-help">IP만 입력하세요. URL·포트·대괄호·CIDR은 IP 입력란에 포함하지 않습니다.</p>
          <label>설명<input value={state.draft.description} onChange={update("description")} maxLength={500} placeholder="용도 또는 서버 구분 · 선택 입력" /><small>최대 500자</small></label>
        </fieldset>
        {inUse && <p className="egress-warning">사용 중인 대상은 설명만 수정할 수 있습니다.{targetChanged && " 작성 중인 IP·포트가 최신 대상과 다릅니다."}</p>}
        {inUse && targetChanged && <button type="button" className="secondary" disabled={blocked} onClick={() => controller?.restoreTarget()}>IP·포트를 최신 값으로 되돌리기</button>}
        {state.conflict && <section className="egress-conflict" aria-label="대상 변경 검토"><h3>최신 내용 확인</h3>{latest ? <><p>서버의 현재 값: <strong>{internalTargetAddress(latest)}</strong> · revision {latest.revision}</p><p>현재 설명: {latest.description || "설명 없음"}</p><p>작성 중인 입력은 위에 그대로 남아 있습니다. 현재 값과 비교하고 저장할 내용을 확인하세요.</p><button type="button" className="secondary" disabled={blocked} onClick={() => controller?.acceptLatest()}>최신 버전 기준으로 계속 편집</button></> : <p>이 대상은 현재 목록에 없습니다. 입력을 확인한 뒤 편집을 취소하거나 필요한 내용을 복사해 새 대상으로 등록하세요.</p>}</section>}
        <div className="egress-actions"><button type="submit" className="primary" disabled={blocked || state.conflict || (inUse && targetChanged)}>{state.busy === "save" ? "저장 중…" : state.editing ? "수정 저장" : "내부 대상 등록"}</button>{state.editing && <button type="button" className="secondary" disabled={busy} onClick={() => controller?.cancel()}>편집 취소</button>}</div>
        <p className="egress-help">설정 변경은 이후 요청에 적용됩니다. 기존 분석 결과는 변경하지 않습니다.</p>
      </form>
    </div>
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
