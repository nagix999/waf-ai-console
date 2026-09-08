import { useState } from "react";
import Dialog from "./Dialog.jsx";
import TextInspector from "./TextInspector.jsx";
import { executionDuration, formatDate } from "./analysisView.js";
import { stepLabel } from "./inspection.js";

const statuses = { pending: "대기", running: "실행 중", processing: "실행 중", completed: "완료", passed: "통과", failed: "실패", skipped: "건너뜀" };
const status = value => statuses[value] || value || "미기록";
export default function AgentHistory({ runs = [] }) {
  const [selectedRun, setSelectedRun] = useState(null); const [selectedStep, setSelectedStep] = useState(null);
  const [tab, setTab] = useState("output"); const [identityOpen, setIdentityOpen] = useState(false);
  const run = runs.find(item => item.id === selectedRun) || runs[0];
  const steps = run?.steps || [];
  const step = steps.find(item => item.id === selectedStep) || steps[0];
  if (!run) return <p className="empty">아직 실행 이력이 없습니다. 분석을 시작하면 단계별 기록이 표시됩니다.</p>;
  return <section className="agent-history inspection-history">
    <div className="ux-toolbar"><label className="ux-grow">실행 선택<select value={run.id} onChange={event => { setSelectedRun(event.target.value); setSelectedStep(null); setTab("output"); }}>{runs.map((item, index) => <option key={item.id} value={item.id}>실행 {runs.length - index} · {status(item.status)} · {formatDate(item.started_at)}</option>)}</select></label><button type="button" className="secondary" onClick={() => setIdentityOpen(true)}>기술 식별정보</button></div>
    <div className="inspection-stats"><div><span>실행 상태</span><strong className={`status status-${run.status}`}>{status(run.status)}</strong></div><div><span>소요 시간</span><strong>{executionDuration(run)}</strong></div><div><span>시작</span><strong>{formatDate(run.started_at)}</strong></div><div><span>종료</span><strong>{run.completed_at ? formatDate(run.completed_at) : "종료 기록 없음"}</strong></div></div>
    <div className="inspection-agent-grid">
      <nav className="inspection-steps" aria-label="분석 단계">{steps.map((item, index) => <button type="button" key={item.id} aria-current={step?.id === item.id ? "step" : undefined} onClick={() => { setSelectedStep(item.id); setTab("output"); }}><span className="inspection-step-number">{index + 1}</span><span><strong>{stepLabel(item.step_type || item.name)}</strong><small>{status(item.status)} · {executionDuration(item)}</small></span></button>)}</nav>
      {step ? <section className="inspection-step-detail"><div className="panel-head-inline"><h3>{stepLabel(step.step_type || step.name)}</h3></div><p className="step-timing"><span className={`status status-${step.status}`}>{status(step.status)}</span><strong>{executionDuration(step)}</strong><small>재시도 포함</small><small>{formatDate(step.started_at)} → {step.completed_at ? formatDate(step.completed_at) : "종료 기록 없음"}</small></p>
        <div className="tabs" role="tablist" aria-label="단계 자료">{[["output", "처리 결과"], ["input", "분석 입력"], ["metadata", "실행 정보"]].map(([value, label]) => <button type="button" key={value} role="tab" aria-selected={tab === value} onClick={() => setTab(value)}>{label}</button>)}</div>
        <TextInspector key={`${run.id}-${step.id}-${tab}`} label={tab === "output" ? "단계 처리 결과" : tab === "input" ? "단계 분석 입력" : "단계 실행 정보"} value={tab === "metadata" ? { metadata: step.metadata, tool_calls: step.tool_calls } : step[tab]} />
      </section> : <p className="empty">아직 기록된 단계가 없습니다.</p>}
    </div>
    <Dialog open={identityOpen} title="실행 식별정보" onClose={() => setIdentityOpen(false)}><TextInspector label="실행 식별정보" value={{ run_id: run.id, framework_run_id: run.framework_run_id, fingerprint: run.fingerprint, failure_id: run.failure_id, step_id: step?.id, step_name: step?.name }} /></Dialog>
  </section>;
}
