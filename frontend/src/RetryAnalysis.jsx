import { useEffect, useRef, useState } from "react";
import { api } from "./api.js";
import Dialog from "./Dialog.jsx";
import { retryError, retryPayload } from "./analysisRetry.js";
import { newTestRequestKey } from "./testRuns.js";

export default function RetryAnalysis({ detail, onOpen }) {
  const [open, setOpen] = useState(false); const [info, setInfo] = useState(null);
  const [error, setError] = useState(""); const [approved, setApproved] = useState(false); const [busy, setBusy] = useState(false); const [refresh, setRefresh] = useState(0);
  const requestKey = useRef(null); const submitting = useRef(false); const mounted = useRef(true);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
  useEffect(() => {
    if (!open) return;
    let active = true; const controller = new AbortController(); setInfo(null); setError(""); setApproved(false);
    api.retryEligibility(detail.id, { signal: controller.signal }).then(value => {
      if (!active) return;
      if (value?.analysis_id !== detail.id || typeof value.allowed !== "boolean") { setError("재실행 조건을 확인하지 못했습니다."); return; }
      setInfo(value);
    }).catch(err => { if (active) setError(err.status === 401 ? "로그인이 만료되었습니다. 다시 로그인하세요." : "재실행 조건을 확인하지 못했습니다."); });
    return () => { active = false; controller.abort(); };
  }, [open, detail.id, refresh]);
  async function submit() {
    if (!info?.allowed || !approved || submitting.current) return;
    submitting.current = true; setBusy(true); setError(""); requestKey.current ||= newTestRequestKey();
    try {
      const result = await api.retryAnalysis(detail.id, retryPayload(requestKey.current, approved));
      if (result?.retry_of_analysis_id !== detail.id || !result?.analysis_id) throw new Error("invalid_retry_response");
      if (mounted.current) { setOpen(false); onOpen(result.analysis_id); }
    } catch (err) { if (mounted.current) setError(retryError(err.message)); }
    finally { submitting.current = false; if (mounted.current) setBusy(false); }
  }
  return <div className="retry-actions ux-toolbar">
    {detail.retry_of_analysis_id && <button type="button" className="secondary" onClick={() => onOpen(detail.retry_of_analysis_id)}>이전 실패 보기</button>}
    {detail.retry_analysis_id ? <button type="button" className="primary" onClick={() => onOpen(detail.retry_analysis_id)}>재실행 결과 보기</button> : detail.status === "failed" && <button type="button" className="primary" onClick={() => setOpen(true)}>재실행</button>}
    <Dialog open={open} title="실패한 분석 재실행" onClose={() => { if (!busy) setOpen(false); }}>
      {!info && !error && <p role="status">당시 모델과 지침을 확인하는 중…</p>}
      {info && <><p>기존 실패 이력은 보존하고 새 실행을 연결합니다. 기존 테스트의 평가 지표는 바꾸지 않습니다.</p><dl className="label-metadata"><dt>모델</dt><dd>{info.model_name || info.model_profile || "복원할 수 없음"}</dd><dt>지침</dt><dd>{info.prompt_version || "복원할 수 없음"}</dd></dl>
        {!info.allowed && <p className="notice">{retryError(info.blocked_reason)}</p>}
        {info.existing_retry_id && <button className="primary" type="button" onClick={() => { setOpen(false); onOpen(info.existing_retry_id); }}>재실행 결과 보기</button>}
        {info.allowed && <><p className="notice">모델을 다시 호출하므로 처리 자원과 비용이 발생할 수 있습니다.{info.provider === "openai" && " HTTP 원문과 Cookie가 마스킹 없이 OpenAI로 다시 전송됩니다."}</p><label className="checkbox-row"><input type="checkbox" checked={approved} disabled={busy} onChange={event => setApproved(event.target.checked)} />당시 모델·지침과 호출 비용을 확인했습니다.</label><button type="button" className="primary" disabled={!approved || busy} onClick={submit}>{busy ? "접수 중…" : "재실행 시작"}</button></>}
      </>}
      {error && <p className="error" role="alert">{error}</p>}
      {(error || info && !info.allowed && !info.existing_retry_id) && <button className="secondary" type="button" disabled={busy} onClick={() => setRefresh(value => value + 1)}>조건 다시 확인</button>}
    </Dialog>
  </div>;
}
