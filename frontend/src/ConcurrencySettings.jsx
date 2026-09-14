import { useEffect, useRef, useState } from "react";
import { api } from "./api.js";
import Dialog from "./Dialog.jsx";
import { concurrencyDraft, concurrencyIssue, createConcurrencyController, emptyConcurrencyState } from "./concurrencySettings.js";

export default function ConcurrencySettings() {
  const [state, setState] = useState(emptyConcurrencyState), [confirm, setConfirm] = useState(false);
  const controller = useRef(null);
  useEffect(() => {
    const instance = createConcurrencyController({ api, onChange: setState });
    controller.current = instance; void instance.refresh();
    return () => { instance.dispose(); controller.current = null; };
  }, []);
  const { catalog, draft } = state;
  const disabled = state.loading || state.busy || state.needsRefresh;
  const issue = concurrencyIssue(draft);
  const changed = catalog && JSON.stringify(concurrencyDraft(catalog)) !== JSON.stringify(draft);
  const change = (field, event, key) => controller.current?.change(field, event.target.value === "" ? "" : Number(event.target.value), key);
  return <section className="panel agent-settings-panel">
    <div className="panel-head"><div><h2>동시 처리</h2><p className="ux-muted">worker·vLLM 서버를 추가로 띄우지 않고 여러 분석을 함께 처리합니다. 같은 DB의 모든 worker에 적용됩니다.</p></div>
      <button className="secondary" disabled={state.busy || state.loading} onClick={() => { setConfirm(false); void controller.current?.refresh(); }}>새로고침</button></div>
    {state.error && <p className="error" role="alert">{state.error}</p>}{state.notice && <p className="notice" role="status">{state.notice}</p>}
    {!draft ? <p role="status">{state.loading ? "설정을 확인하는 중…" : "설정을 다시 조회해 주세요."}</p> : <>
      <h3>동시 분석 수</h3><div className="agent-role-grid">{[["production", "프로덕션"], ["test", "테스트"]].map(([key, label]) => <fieldset key={key} disabled={disabled}><legend>{label}</legend>
        <label>최대 분석 수<input type="number" min="1" max="32" step="1" value={draft[key]} onChange={event => change(key, event)} /></label>
        {Number.isInteger(catalog.active?.[key]) && <small className="ux-muted">조회 시 처리 중 {catalog.active[key]}건 · LLM 호출 대기 포함</small>}
      </fieldset>)}</div>
      <p className="ux-muted">단건·배치 파일 분석에 적용됩니다. 초과한 작업은 대기열에 남습니다. 별도 150건 모델 검증의 문항 처리 순서는 유지합니다.</p>
      <h3>서버별 동시 호출 수</h3><p className="ux-muted">같은 주소·포트의 프로필은 상한을 공유합니다. 운영·테스트·모델 검증 호출을 합산하며, 한 분석의 단계는 순서대로 진행합니다.</p>
      {!draft.servers.length ? <p>LLM 프로필을 등록하면 서버가 표시됩니다.</p> : <div className="agent-role-grid">{draft.servers.map(row => <fieldset key={row.server_key} disabled={disabled}>
        <legend>{row.server_key}</legend><p className="ux-muted">{catalog.servers.find(server => server.server_key === row.server_key)?.profiles.map(profile => profile.name).join(", ")}</p>
        <label>최대 호출 수<input aria-label={`${row.server_key} 최대 호출 수`} type="number" min="1" max="64" step="1" value={row.max_calls} onChange={event => change("server", event, row.server_key)} /></label>
      </fieldset>)}</div>}
      <p className="ux-muted">값을 낮춰도 진행 중인 작업은 중단하지 않습니다. 완료되어 여유가 생기면 다음 작업을 시작합니다. 전체 모델 검증의 동시 요청 수는 이 상한 이하여야 합니다.</p>
      {issue && <p className="notice">{issue}</p>}<button className="primary" disabled={disabled || Boolean(issue) || !changed} onClick={() => setConfirm(true)}>설정 저장</button>
    </>}
    <Dialog open={confirm} title="동시 처리 변경" onClose={() => { if (!state.busy) setConfirm(false); }}>
      {draft && <><p>동시 분석: 프로덕션 {draft.production}건 · 테스트 {draft.test}건</p><ul>{draft.servers.map(row => <li key={row.server_key}>{row.server_key}: 최대 {row.max_calls}회</li>)}</ul>
        <p>대기 중인 분석에도 적용됩니다. 수를 높이면 GPU 메모리 사용량·요청량이 늘 수 있으며, 처리 속도가 반드시 빨라지지는 않습니다. OpenAI 사용 시 비용이 더 빠르게 발생할 수 있습니다.</p>
        {state.error && <p className="error" role="alert">{state.error}</p>}<div className="action-row"><button className="secondary" disabled={state.busy} onClick={() => setConfirm(false)}>취소</button>
          <button className="primary" disabled={disabled || Boolean(issue)} onClick={async () => { if (await controller.current?.save()) setConfirm(false); }}>{state.busy ? "저장 중…" : "적용"}</button></div></>}
    </Dialog>
  </section>;
}
