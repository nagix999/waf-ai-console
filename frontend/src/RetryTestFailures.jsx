import { useEffect, useRef, useState } from "react";
import { api } from "./api.js";
import Dialog from "./Dialog.jsx";
import { retryError } from "./analysisRetry.js";
import { newTestRequestKey } from "./testRuns.js";
import { testRetryNotice, testRetryPayload } from "./testRetry.js";

export function TestRetryConfirmation({ info, approved, busy, onApproval, onSubmit }) {
  return <>
    <p>이 테스트 전체에서 실패한 항목만 다시 분석합니다. 현재 페이지와 필터에 관계없이 적용됩니다.</p>
    <p><strong>재실행 가능 {info.eligible_count}건</strong> · 실패 {info.failed_count}건</p>
    <p className="ux-muted">실패 당시 모델·지침을 사용합니다. 기존 실패 이력은 남기고, 재실행 결과로 문항별 결과와 평가 지표를 갱신합니다. 총 문항 수와 저장한 평가 기록은 유지됩니다.</p>
    {Object.entries(info.blocked_counts || {}).map(([reason, count]) => <p className="notice" key={reason}>{count}건 제외 · {retryError(reason)}</p>)}
    {info.eligible_count > 0 ? <>
      <p className="notice">모델을 다시 호출하므로 처리 자원과 비용이 발생할 수 있습니다.{info.external_calls && " 원문 또는 원문을 포함한 분석 내용이 마스킹 없이 OpenAI로 다시 전송될 수 있습니다."}</p>
      <label className="checkbox-row"><input type="checkbox" checked={approved} disabled={busy} onChange={event => onApproval(event.target.checked)} />재실행 대상과 모델 호출 비용을 확인했습니다.</label>
      <button type="button" className="primary" disabled={!approved || busy} onClick={onSubmit}>{busy ? "접수 중…" : `${info.eligible_count}건 재실행`}</button>
    </> : <p role="status">지금 재실행할 수 있는 실패 항목이 없습니다. 접수 거부 항목은 파일이나 입력 내용을 수정해야 합니다.</p>}
  </>;
}

export default function RetryTestFailures({ run, onSubmitted }) {
  const [open, setOpen] = useState(false), [info, setInfo] = useState(null), [error, setError] = useState("");
  const [approved, setApproved] = useState(false), [busy, setBusy] = useState(false), [reload, setReload] = useState(0);
  const requestKey = useRef(null), submitting = useRef(false), mounted = useRef(true);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
  useEffect(() => {
    if (!open) return;
    let active = true; const controller = new AbortController(); setInfo(null); setError(""); setApproved(false);
    api.testRetryEligibility(run.id, { signal: controller.signal }).then(value => {
      if (!active) return;
      if (value?.test_run_id !== run.id || !Array.isArray(value.eligible_ids) || value.eligible_count !== value.eligible_ids.length) throw new Error("invalid_retry_preview");
      setInfo(value);
    }).catch(err => { if (active) setError(err.status === 401 ? "로그인이 만료되었습니다. 다시 로그인하세요." : "재실행 대상을 확인하지 못했습니다."); });
    return () => { active = false; controller.abort(); };
  }, [open, run.id, reload]);
  async function submit() {
    if (!info || !approved || submitting.current) return;
    submitting.current = true; setBusy(true); setError(""); requestKey.current ||= newTestRequestKey();
    try {
      const result = await api.retryTestFailures(run.id, testRetryPayload(info, requestKey.current, approved));
      if (result?.test_run_id !== run.id || !Number.isInteger(result.enqueued)) throw new Error("invalid_retry_response");
      if (mounted.current) { setOpen(false); onSubmitted(testRetryNotice(result)); }
    } catch (err) { if (mounted.current) setError(err.status ? retryError(err.message) : "접수 응답을 확인하지 못했습니다. 다시 누르면 같은 요청으로 확인하므로 중복 접수되지 않습니다."); }
    finally { submitting.current = false; if (mounted.current) setBusy(false); }
  }
  return <>
    <button type="button" className="secondary" onClick={() => { requestKey.current = null; setOpen(true); }}>실패 항목 모두 재실행</button>
    <Dialog open={open} title="실패 항목 모두 재실행" onClose={() => { if (!busy) setOpen(false); }}>
      {!info && !error && <p role="status">실패 항목과 당시 실행 설정을 확인하는 중…</p>}
      {info && <TestRetryConfirmation info={info} approved={approved} busy={busy} onApproval={setApproved} onSubmit={submit} />}
      {error && <p className="error" role="alert">{error}</p>}
      {error && <button type="button" className="secondary" disabled={busy} onClick={() => { requestKey.current = null; setReload(value => value + 1); }}>대상 다시 확인</button>}
    </Dialog>
  </>;
}
