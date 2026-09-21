import { useEffect, useState } from "react";
import Dialog from "./Dialog.jsx";
import { Icon } from "./Icon.jsx";
import TextInspector from "./TextInspector.jsx";
import DetailTabs from "./DetailTabs.jsx";
import { executionDuration, formatDate } from "./analysisView.js";
import { stepLabel } from "./inspection.js";

const statuses = { pending: "대기", running: "실행 중", processing: "실행 중", completed: "완료", passed: "통과", failed: "실패", skipped: "건너뜀" };
const status = value => statuses[value] || value || "미기록";
export default function AgentHistory({ runs = [], active = true }) {
  const [selectedRun, setSelectedRun] = useState(null); const [selectedStep, setSelectedStep] = useState(null);
  const [tab, setTab] = useState("output"); const [identityOpen, setIdentityOpen] = useState(false);
  useEffect(() => { if (!active) setIdentityOpen(false); }, [active]);
  const run = runs.find(item => item.id === selectedRun) || runs[0];
  const steps = run?.steps || [];
  const step = steps.find(item => item.id === selectedStep) || steps[0];
  if (!run) return <p className="empty">아직 실행 이력이 없습니다. 분석을 시작하면 단계별 기록이 표시됩니다.</p>;
  return <section className="agent-history inspection-history">
    <div className="ux-toolbar inspection-run-toolbar"><label className="ux-grow">실행 선택<select value={run.id} onChange={event => { setSelectedRun(event.target.value); setSelectedStep(null); setTab("output"); }}>{runs.map((item, index) => <option key={item.id} value={item.id}>실행 {runs.length - index} · {status(item.status)} · {formatDate(item.started_at)}</option>)}</select></label><span className="inspection-run-duration">실행 시간 <strong>{executionDuration(run)}</strong></span><button type="button" className="secondary" onClick={() => setIdentityOpen(true)}>실행 식별자</button></div>
    <div className="inspection-agent-grid">
      <nav className="inspection-steps" aria-label="분석 단계"><h3>실행 단계 <span>({steps.length})</span></h3>{steps.map((item, index) => <button type="button" key={item.id} aria-current={step?.id === item.id ? "step" : undefined} onClick={() => { setSelectedStep(item.id); setTab("output"); }}><span className={`inspection-step-number step-tone-${item.status}`}><Icon name={["completed", "passed"].includes(item.status) ? "check" : item.status === "failed" ? "close" : ["running", "processing"].includes(item.status) ? "refresh" : "clock"} size={14} /></span><span><strong>{index + 1}. {stepLabel(item.step_type || item.name, item.metadata)}</strong><small>{status(item.status)} · {executionDuration(item)}</small></span></button>)}</nav>
      {step ? <section className="inspection-step-detail"><div className="panel-head-inline"><h3>{stepLabel(step.step_type || step.name, step.metadata)}</h3></div><p className="step-timing"><span className={`status status-${step.status}`}>{status(step.status)}</span><strong>{executionDuration(step)}</strong><small>재시도 포함</small><small>{formatDate(step.started_at)} → {step.completed_at ? formatDate(step.completed_at) : "종료 기록 없음"}</small></p>
        <StepMetrics metadata={step.metadata} />
        <RepairSummary metadata={step.metadata} />
        <DetailTabs label="단계 자료" items={[["output", "처리 결과"], ["input", "분석 입력"], ["metadata", "실행 정보"]]} value={tab} onChange={setTab}>{key => tab === key && <TextInspector jsonText key={`${run.id}-${step.id}-${key}`} label={key === "output" ? "단계 처리 결과" : key === "input" ? "단계 분석 입력" : "단계 실행 정보"} value={key === "metadata" ? { metadata: step.metadata, tool_calls: step.tool_calls } : step[key]} />}</DetailTabs>
      </section> : <p className="empty">아직 기록된 단계가 없습니다.</p>}
    </div>
    <Dialog open={identityOpen} title="실행 식별자" onClose={() => setIdentityOpen(false)}><dl className="inspection-run-dates"><div><dt>시작</dt><dd>{formatDate(run.started_at)}</dd></div><div><dt>종료</dt><dd>{run.completed_at ? formatDate(run.completed_at) : "종료 기록 없음"}</dd></div></dl><TextInspector label="실행 식별자" value={{ run_id: run.id, framework_run_id: run.framework_run_id, fingerprint: run.fingerprint, failure_id: run.failure_id, step_id: step?.id, step_name: step?.name }} /></Dialog>
  </section>;
}

export function StepMetrics({ metadata = {} }) {
  if (!metadata.model_profile && !metadata.model_name && !metadata.usage) return null;
  const tokens = key => Number.isSafeInteger(metadata.usage?.[key]) && metadata.usage[key] >= 0 ? metadata.usage[key].toLocaleString() : "미기록";
  return <div className="inspection-step-metrics"><dl>{[
    ["모델", metadata.model_profile || metadata.model_name || "미기록"],
    ["공급자", metadata.llm_provider || metadata.provider || "미기록"],
    ["입력 토큰", tokens("input_tokens")], ["출력 토큰", tokens("output_tokens")],
  ].map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{value}</dd></div>)}</dl><p className="ux-muted">토큰은 마지막 호출의 기록이며, 재시도를 포함한 단계 전체 사용량은 아닙니다.</p></div>;
}

export function RepairSummary({ metadata }) {
  const grounding = metadata?.evidence_grounding_retry;
  // A citation repair contains separate calls, each with its own format retry
  // record. Do not report only the last call's count as the whole step count.
  const calls = Array.isArray(grounding?.attempts) && grounding.attempts.length
    ? grounding.attempts.map(attempt => attempt.output_validation_retry)
    : [metadata?.output_validation_retry];
  const known = calls.length && calls.every(call => Number.isInteger(call?.attempt_count) && call.attempt_count > 0);
  const formatRetries = known ? calls.reduce((sum, call) => sum + call.attempt_count - 1, 0) : null;
  if (!grounding && !metadata?.output_validation_retry) return null;
  return <dl className="inspection-repair-summary">
    <div><dt>출력 형식 재시도</dt><dd>{formatRetries === null ? "미기록" : `${formatRetries}회`}</dd></div>
    <div><dt>근거 인용 교정</dt><dd>{Number.isInteger(grounding?.attempt_count) && grounding.attempt_count > 0 ? `${grounding.attempt_count - 1}회` : "미기록"}</dd></div>
    {grounding?.attempted && <div><dt>교정 결과</dt><dd>{grounding.recovered ? "원문 대조 통과" : "교정 미완료"}</dd></div>}
  </dl>;
}
