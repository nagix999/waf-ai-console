import { useEffect, useRef, useState } from "react";
import { api } from "./api.js";
import Dialog from "./Dialog.jsx";
import { ConsolePopover } from "./ConsoleShell.jsx";
import { useWords } from "./LifecycleViews.jsx";
import { canStopTest, isTestStopped, stopError } from "./testStop.js";
import { formatDate } from "./analysisView.js";

export default function StopTestRun({ run, menu = false, onStopped, onUnauthorized }) {
  const w = useWords();
  const [open, setOpen] = useState(false), [fresh, setFresh] = useState(null);
  const [busy, setBusy] = useState(false), [error, setError] = useState("");
  const submitting = useRef(false);
  useEffect(() => {
    if (!open) return;
    const controller = new AbortController();
    setFresh(null); setError("");
    // No category/status/saved-evaluation filters: show the whole current run.
    api.testRun(run.id, { limit: 1, reference_basis: "initial" }, { signal: controller.signal, cache: "no-store" })
      .then(value => { if (!controller.signal.aborted) setFresh(value); })
      .catch(exc => { if (!controller.signal.aborted) { setError(stopError(exc, w)); if (exc.status === 401) onUnauthorized?.(); } });
    return () => controller.abort();
  }, [open, run.id]);
  async function stop() {
    if (submitting.current || !canStopTest(fresh)) return;
    submitting.current = true; setBusy(true); setError("");
    try {
      const result = await api.stopTestRun(run.id);
      if (result?.id !== run.id || !isTestStopped(result)) throw new Error("invalid_stop_response");
      setOpen(false); onStopped?.();
    } catch (exc) {
      setError(stopError(exc, w));
      if (exc.status === 401) onUnauthorized?.();
    } finally { submitting.current = false; setBusy(false); }
  }
  if (!canStopTest(run) && !open) return null;
  return <>
    {menu ? <ConsolePopover label={w(`${run.name} 작업`, `Actions for ${run.name}`)} trigger="⋯">{close => <button type="button" onClick={() => { close(); setOpen(true); }}>{w("테스트 중지", "Stop test")}</button>}</ConsolePopover>
      : <button type="button" className="secondary" onClick={() => setOpen(true)}>{w("테스트 중지", "Stop test")}</button>}
    <Dialog open={open} title={w("테스트 중지", "Stop test")} onClose={() => { if (!busy) setOpen(false); }}>
      <p><strong>{run.name}</strong></p>
      {fresh ? <>
        <dl className="test-stop-counts"><div><dt>{w("완료 · 결과 보존", "Completed · preserved")}</dt><dd>{fresh.completed}</dd></div><div><dt>{w("대기·처리 중 · 중지 대상", "Queued / processing · to stop")}</dt><dd>{fresh.pending + fresh.processing}</dd></div></dl>
        {!canStopTest(fresh) && <p className="notice">{isTestStopped(fresh) ? w("이미 중지된 테스트입니다.", "This test has already stopped.") : w("이미 실행이 끝났습니다. 창을 닫고 새로고침하세요.", "This test has finished. Close and refresh.")}</p>}
        <p>{w("모델에 보낸 요청은 즉시 취소되지 않을 수 있지만, 중지된 문항의 응답은 완료 결과로 저장하지 않습니다.", "In-flight model requests may continue, but their responses will not be saved as completed results.")}</p>
        <p className="ux-muted">{w("확인 중에도 완료 건수는 바뀔 수 있습니다. 이 테스트는 다시 시작할 수 없으며 공식 평가·운영 반영에 사용할 수 없습니다. 다시 실행하려면 새 테스트를 만드세요.", "Counts may change while you confirm. This test cannot resume or qualify for Production. To run again, create a new test.")}</p>
      </> : !error && <p role="status">{w("현재 상태를 확인하는 중…", "Checking current status…")}</p>}
      {error && <p className="error" role="alert">{error}</p>}
      <div className="ux-toolbar"><button type="button" className="secondary" autoFocus disabled={busy} onClick={() => setOpen(false)}>{error || (fresh && !canStopTest(fresh)) ? w("닫기", "Close") : w("계속 실행", "Keep running")}</button><button type="button" className="primary danger test-stop-confirm" disabled={!canStopTest(fresh) || busy || Boolean(error)} onClick={stop}>{busy ? w("중지하는 중…", "Stopping…") : w("테스트 중지", "Stop test")}</button></div>
    </Dialog>
  </>;
}

export function TestStoppedNotice({ run }) {
  const w = useWords();
  if (!isTestStopped(run)) return null;
  return <aside className="notice test-stopped-notice" role="status"><strong>{w("중지됨", "Stopped")} · {formatDate(run.stopped_at)}</strong>
    <p>{run.evaluation_id ? w("선택한 과거 평가 기록은 그대로 보존됩니다. 이 테스트는 현재 중지되어 운영 반영에 사용할 수 없습니다.", "The selected historical evaluation is preserved. This test is now stopped and cannot qualify for Production.") : w("이 테스트는 중지되었습니다. 아래 지표는 중지 전에 완료된 문항에 대한 참고 결과이며 전체 테스트 결과가 아닙니다.", "This test was stopped. Metrics below cover completed cases only, not the full test.")}</p>
    {!run.evaluation_id && <small>{w("완료 · 보존", "Completed · preserved")} {run.completed} · {w("중지된 문항", "Stopped cases")} {run.canceled ?? 0} · {w("실패·평가 분모에서 제외", "Excluded from failures and evaluation denominators")}</small>}
  </aside>;
}
