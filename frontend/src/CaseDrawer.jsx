import NavigationAction from "./NavigationAction.jsx";
import { useEffect, useState } from "react";
import { api } from "./api.js";
import Dialog from "./Dialog.jsx";
import DetailTabs from "./DetailTabs.jsx";
import TextInspector from "./TextInspector.jsx";
import { AnalystEvidence, DecisionIssues } from "./AnalystEvidence.jsx";
import { analystSummary, finalValue } from "./analystView.js";
import { executionDuration, formatDuration } from "./analysisView.js";
import { stepLabel } from "./inspection.js";
import { useWords } from "./LifecycleViews.jsx";
import { startVisiblePolling } from "./visiblePolling.js";

export default function CaseDrawer({ id, onClose, onOpen, groundTruthSource, onGroundTruth, onImport }) {
  const w = useWords(); const [detail, setDetail] = useState(null), [tab, setTab] = useState("result"), [extra, setExtra] = useState(null), [error, setError] = useState(false);
  useEffect(() => { setDetail(null); setError(false); setTab("result"); if (!id) return; return startVisiblePolling(async signal => { const value = await api.analysis(id, { signal }); if (!signal.aborted) { setDetail(value); setError(false); } return ["pending", "processing"].includes(value.status); }, { onError: () => setError(true) }); }, [id]);
  useEffect(() => { setExtra(null); if (!id || !["input", "trace"].includes(tab)) return; const controller = new AbortController(); const read = tab === "input" ? api.rawEvent : api.agentRuns; read(id, { signal: controller.signal }).then(value => { if (!controller.signal.aborted) setExtra({ tab, value }); }).catch(() => { if (!controller.signal.aborted) setError(true); }); return () => controller.abort(); }, [id, tab]);
  const verdict = detail && finalValue(detail, "verdict");
  const names = { true_positive: w("정탐", "True positive"), false_positive: w("오탐", "False positive"), inconclusive: w("보류", "Inconclusive"), failed: w("실행 실패", "Failed"), pending: w("대기", "Pending"), processing: w("분석 중", "Processing"), completed: w("완료", "Completed") };
  return <Dialog open={Boolean(id)} title={w("문항 결과", "Case result")} className="r3-case-drawer" onClose={onClose}>
    <div className="ux-toolbar"><button className="text-button" onClick={() => onOpen(id)}>{w("크게 보기", "Open full analysis")} ↗</button>{groundTruthSource && onGroundTruth && <NavigationAction onClick={() => onGroundTruth(groundTruthSource.dataset_id, groundTruthSource.stable_case_id)}>{w("정답 데이터에서 열기", "Open in Ground Truth")}</NavigationAction>}</div>
    {onImport && <button className="text-button" onClick={onImport}>{w("이 사례를 정답 데이터에 추가", "Add This Case to Ground Truth")}</button>}
    {error && <p className="error" role="alert">{w("결과를 조회하지 못했습니다. 다시 열어 주세요.", "Could not load the result. Please reopen it.")}</p>}
    {!detail && !error && <p role="status">{w("조회 중…", "Loading…")}</p>}
    {detail && <><div className={`r3-drawer-verdict ${verdict}`}><strong>{names[verdict] || names[detail.status] || w("상태 미확인", "Unknown")}</strong><span>{formatDuration(detail.total_elapsed_ms, "—")}</span></div>
      <DetailTabs label={w("문항 확인", "Inspect case")} value={tab} onChange={setTab} items={[["result", w("결과", "Result")], ["evidence", w("근거", "Evidence")], ["input", w("입력", "Input")], ["trace", w("실행 요약", "Trace")]]}>{key => <>
        {key === "result" && <><p className="r3-case-summary">{analystSummary(detail)}</p><DecisionIssues detail={detail} /></>}
        {key === "evidence" && (detail.result ? <AnalystEvidence result={detail.result} /> : <p>{w("저장된 근거가 없습니다.", "No evidence has been recorded.")}</p>)}
        {key === "input" && <><p className="v5-context">{w("원문 조회는 감사 이력에 남습니다.", "Source access is audited.")}</p>{extra?.tab === key ? <TextInspector label={w("입력 요약과 원문", "Input and source")} value={extra.value} /> : !error && <p>{w("조회 중…", "Loading…")}</p>}</>}
        {key === "trace" && <><p className="v5-context">{w("단계별 입출력은 전체 분석 상세에서 확인하세요.", "Open full analysis to inspect step inputs and outputs.")}</p>{extra?.tab === key ? <div className="r3-trace-summary">{(Array.isArray(extra.value) ? extra.value : extra.value.items || extra.value.runs || []).map((run, index) => <div key={run.id || index}><strong>{run.status}</strong>{run.steps?.map((step, i) => <p key={step.id || i}><span>{stepLabel(step.step_type || step.name, step.metadata)}</span><span>{step.status} · {executionDuration(step)}</span></p>)}</div>)}</div> : !error && <p>{w("조회 중…", "Loading…")}</p>}</>}
      </>}</DetailTabs></>}
  </Dialog>;
}
