import { useEffect, useRef, useState } from "react";
import Dialog from "./Dialog.jsx";
import { Icon } from "./Icon.jsx";
import TextInspector from "./TextInspector.jsx";
import DetailTabs from "./DetailTabs.jsx";
import { executionDuration, formatDate } from "./analysisView.js";
import { useR5Words } from "./R5Evaluation.jsx";
import { stepLabel } from "./inspection.js";
import useWorkspaceHeight from "./useWorkspaceHeight.js";
import { outputValidationHistory } from "./outputValidationHistory.js";
import "./agentExecution.css";

const statuses = { pending: "대기", running: "실행 중", processing: "실행 중", completed: "완료", passed: "통과", failed: "실패", skipped: "건너뜀" };
const stopped = item => item?.failure_id === "test_run_stopped" || item?.metadata?.error_code === "test_run_stopped";
const tone = item => stopped(item) ? "canceled" : item.status;
const status = value => statuses[value] || value || "미기록";
export default function AgentHistory({ runs = [], active = true, selection, onSelectionChange, compact = false, finalResult, configuration, initialJson = false }) {
  const w = useR5Words(); const state = item => stopped(item) ? w("테스트 중지", "Test stopped") : w(status(item.status), ({ pending: "Pending", running: "Running", processing: "Running", completed: "Completed", passed: "Passed", failed: "Failed", skipped: "Skipped" })[item.status] || item.status || "Not recorded");
  const [selectedRun, setSelectedRun] = useState(null); const [selectedStep, setSelectedStep] = useState(initialJson ? "result" : null);
  const [tab, setTab] = useState("output"); const [identityOpen, setIdentityOpen] = useState(false);
  const grid = useRef(null); useWorkspaceHeight(grid, active);
  useEffect(() => { if (!active) setIdentityOpen(false); }, [active]);
  const controlled = typeof onSelectionChange === "function";
  const runId = controlled ? selection?.runId : selection?.runId ?? selectedRun;
  const run = runs.find(item => item.id === runId) || runs[0];
  const steps = run?.steps || [];
  const stepId = controlled ? selection?.stepId ?? (initialJson ? "result" : null) : selection?.stepId ?? selectedStep;
  const step = steps.find(item => item.id === stepId) || steps[0];
  const resultSelected = finalResult !== undefined && (stepId === "result" || !step);
  const view = resultSelected ? "output" : ["output", "input", "metadata"].includes(selection?.view) ? selection.view : controlled ? "output" : tab;
  function change(next) {
    const value = { runId: run?.id, stepId: resultSelected ? "result" : step?.id, view, ...next };
    setSelectedRun(value.runId); setSelectedStep(value.stepId); setTab(value.view); onSelectionChange?.(value);
  }
  function navigate(event) {
    const buttons = [...event.currentTarget.querySelectorAll("button[data-execution-step]")], index = buttons.indexOf(document.activeElement);
    if (index < 0) return;
    const next = event.key === "ArrowDown" ? Math.min(index + 1, buttons.length - 1) : event.key === "ArrowUp" ? Math.max(index - 1, 0) : event.key === "Home" ? 0 : event.key === "End" ? buttons.length - 1 : null;
    if (next !== null && buttons[next]) { event.preventDefault(); buttons[next].focus(); }
  }
  if (!run && finalResult === undefined) return <p className="empty">{w("아직 실행 이력이 없습니다. 분석을 시작하면 단계별 기록이 표시됩니다.", "No execution history is recorded yet. Steps appear when analysis starts.")}</p>;
  return <section className="agent-history inspection-history">
    <div ref={grid} className="inspection-agent-grid execution-workspace">
      <nav className="inspection-steps execution-navigation" aria-label={w("분석 단계", "Analysis steps")} onKeyDown={navigate}><header className="execution-navigation-heading"><h3>{w("실행 단계", "Execution steps")} <span>({steps.length})</span></h3><p>{w("단계를 선택해 기록을 확인합니다.", "Select a step to view its records.")}</p></header>
        {run && <div className="inspection-run-toolbar"><label>{w("실행 선택", "Select run")}<select value={run.id} onChange={event => { const next = runs.find(item => item.id === event.target.value); change({ runId: next.id, stepId: next.steps?.[0]?.id, view: "output" }); }}>{runs.map((item, index) => <option key={item.id} value={item.id}>{w("실행", "Run")} {runs.length - index} / {state(item)} / {formatDate(item.started_at)}</option>)}</select></label><span className="inspection-run-duration">{w("실행 시간", "Run duration")} <strong>{executionDuration(run)}</strong></span><button type="button" className="text-button" onClick={() => setIdentityOpen(true)}>{w("실행 식별자", "Run identifiers")}</button></div>}
        {steps.map((item, index) => <button type="button" data-execution-step key={item.id} aria-current={!resultSelected && step?.id === item.id ? "step" : undefined} onClick={() => change({ stepId: item.id, view: "output" })}><span className={`inspection-step-number step-tone-${tone(item)}`}><Icon name={stopped(item) ? "clock" : ["completed", "passed"].includes(item.status) ? "check" : item.status === "failed" ? "close" : ["running", "processing"].includes(item.status) ? "refresh" : "clock"} size={14} /></span><span><strong>{index + 1}. {stepLabel(item.step_type || item.name, item.metadata)}</strong>{(item.metadata?.model_profile || item.metadata?.model_name) && <small>{item.metadata.model_profile || item.metadata.model_name}</small>}<small className="execution-step-footer"><span>{state(item)}</span><span>{executionDuration(item)}</span></small></span><Icon name="arrow" size={13} /></button>)}
        {!steps.length && <p className="execution-no-steps muted">{w("기록된 단계가 없습니다.", "No execution steps recorded.")}</p>}
        {finalResult !== undefined && <button type="button" className="execution-final-result" data-execution-step aria-current={resultSelected ? "step" : undefined} onClick={() => change({ stepId: "result", view: "output" })}><Icon name="code" size={16} /><span><strong>{w("최종 결과 JSON", "Final result JSON")}</strong><small>{w("API에 전달되는 최종 결과", "Final result delivered by the API")}</small></span><Icon name="arrow" size={13} /></button>}
        {configuration && <details className="execution-configuration"><summary>{w("실행 당시 구성", "Configuration at execution")}</summary>{configuration}</details>}
      </nav>
      {resultSelected ? <section className="inspection-step-detail execution-detail execution-final-detail"><header className="execution-detail-heading"><span className="execution-eyebrow">{w("API 결과", "API result")}</span><h3>{w("최종 결과 JSON", "Final result JSON")}</h3><p>{w("단계별 모델 출력과 구분한 최종 API 결과입니다.", "The final API result, separate from each model's output.")}</p></header><div className="execution-record-body"><TextInspector jsonText label={w("결과 JSON", "Result JSON")} value={finalResult} empty={w("최종 결과가 기록되지 않았습니다.", "No final result is recorded.")} /></div></section>
      : step ? <section className="inspection-step-detail execution-detail" key={`${run.id}-${step.id}`}><div className="execution-detail-heading"><span className="execution-eyebrow">{w("선택한 단계", "Selected step")}</span><div className="panel-head-inline"><h3>{stepLabel(step.step_type || step.name, step.metadata)}</h3></div><p className="step-timing"><span className={`status status-${tone(step)}`}>{state(step)}</span><strong>{executionDuration(step)}</strong><small>{w("재시도 포함", "Includes retries")}</small><small>{formatDate(step.started_at)} → {step.completed_at ? formatDate(step.completed_at) : w("종료 기록 없음", "End not recorded")}</small></p>
        {compact ? <details className="execution-step-observations"><summary>{w("모델 사용량과 재시도", "Model usage and retries")}</summary><StepMetrics metadata={step.metadata} /><RepairSummary metadata={step.metadata} /></details> : <><StepMetrics metadata={step.metadata} /><RepairSummary metadata={step.metadata} /></>}
        </div><div className="execution-record-body"><DetailTabs label={w("단계 자료", "Step records")} items={[["output", w("처리 결과", "Output")], ["input", w("분석 입력", "Input")], ["metadata", w("실행 정보", "Metadata")]]} value={view} onChange={view => change({ view })}>{key => view === key && <><p className="execution-record-note muted">{key === "output" ? w("이 단계에서 저장한 처리 결과입니다.", "The output saved for this execution step.") : key === "input" ? w("이 단계에 전달한 입력 기록입니다.", "The input recorded for this execution step.") : w("모델과 사용량, 재시도 및 도구 호출의 저장 기록입니다.", "Recorded model, usage, retries and tool calls.")}</p>{key === "output" && <OutputValidationHistory output={step.output} />}<TextInspector jsonText key={`${run.id}-${step.id}-${key}`} label={key === "output" ? w("단계 처리 결과", "Step output") : key === "input" ? w("단계 분석 입력", "Step input") : w("단계 실행 정보", "Step metadata")} value={key === "metadata" ? { metadata: step.metadata, tool_calls: step.tool_calls } : step[key]} empty={w("이 단계의 자료가 기록되지 않았습니다.", "No record was saved for this step.")} /></>}</DetailTabs></div>
      </section> : <p className="empty">{w("아직 기록된 단계가 없습니다.", "No steps are recorded yet.")}</p>}
    </div>
    {run && <Dialog open={identityOpen} title={w("실행 식별자", "Run identifiers")} onClose={() => setIdentityOpen(false)}><dl className="inspection-run-dates"><div><dt>{w("시작", "Start")}</dt><dd>{formatDate(run.started_at)}</dd></div><div><dt>{w("종료", "End")}</dt><dd>{run.completed_at ? formatDate(run.completed_at) : w("종료 기록 없음", "End not recorded")}</dd></div></dl><TextInspector label={w("실행 식별자", "Run identifiers")} value={{ run_id: run.id, framework_run_id: run.framework_run_id, fingerprint: run.fingerprint, failure_id: run.failure_id, step_id: step?.id, step_name: step?.name }} /></Dialog>}
  </section>;
}

export function OutputValidationHistory({ output }) {
  const w = useR5Words(), history = outputValidationHistory(output);
  if (!history.length) return null;
  const missing = w("미기록", "Not recorded");
  const stages = { 1: w("초기 분석", "Initial analysis"), 2: w("출처 교정", "Source correction"), 3: w("판정 재검토", "Decision review") };
  const supports = { attack_request: w("공격 요청 근거", "Attack-request evidence"), normal_request: w("정상 요청 근거", "Normal-request evidence"), context: w("참고 내용", "Context") };
  return <section className="execution-validation-history" aria-label={w("검증을 통과하지 못한 출력", "Output that failed validation")}>
    <h4>{w("검증을 통과하지 못한 출력", "Output that failed validation")}</h4>
    <p className="execution-validation-note">{w("오류 확인용 기록이며 최종 판정이나 검증된 근거가 아닙니다. 근거 번호는 해당 시도 안에서만 유효합니다.", "Diagnostic records, not a final verdict or validated evidence. Evidence IDs apply only within each attempt.")}</p>
    {history.map((item, index) => <details className="execution-validation-attempt" key={index} open={index === 0}>
      <summary>{item.groundingAttempt ? stages[item.groundingAttempt] : w("호출 구분 미기록", "Call stage not recorded")} <span>/</span> {item.attempt === null ? w("시도 번호 미기록", "Attempt not recorded") : w(`시도 ${item.attempt}`, `Attempt ${item.attempt}`)} <small>{w(`${item.evidence.length}개 근거 기록`, `${item.evidence.length} evidence record${item.evidence.length === 1 ? "" : "s"}`)}</small></summary>
      {item.issues.length > 0 && <ul className="execution-validation-issues" aria-label={w("검증 오류", "Validation errors")}>{item.issues.map((issue, n) => <li key={n}><code>{issue.field || missing}</code><span> — </span><code>{issue.type || missing}</code></li>)}</ul>}
      {item.evidence.length ? <ol className="execution-rejected-evidence">{item.evidence.map((value, n) => <li key={n}>
        <div className="execution-rejected-evidence-head"><strong><code>{value.evidenceId || missing}</code></strong>{!value.evidenceId && value.position !== null && <span>{w(`목록 위치 ${value.position + 1}`, `List position ${value.position + 1}`)}</span>}<span>{w("출처", "Source")} <code>{value.sourceId || missing}</code></span><span className={`execution-rejected-support support-${["attack_request", "normal_request", "context"].includes(value.supports) ? value.supports : "unknown"}`}>{Object.hasOwn(supports, value.supports) ? supports[value.supports] : value.supports || missing}</span></div>
        <p className="execution-rejected-interpretation">{value.interpretation === null ? w("해석 문장이 기록되지 않았습니다.", "No interpretation was recorded.") : value.interpretation === "" ? w("해석 문장이 비어 있습니다.", "The interpretation is empty.") : value.interpretation}</p>
        <p className="execution-rejected-links">{w("이 해석에 필요한 근거", "Evidence needed for this interpretation")}: <code>{value.relatedIds === null ? missing : value.relatedIds.length ? value.relatedIds.join(", ") : w("없음", "None")}</code></p>
        {(value.interpretationTruncated || value.interpretationDisplayTruncated || value.linksOmitted || value.omittedFields.length > 0) && <p className="execution-validation-limited">{value.interpretationTruncated && <span>{w("해석 문장 일부만 저장됨", "Interpretation partly saved")}</span>}{value.interpretationDisplayTruncated && <span>{w("긴 해석은 일부만 표시함", "Long interpretation partly shown")}</span>}{value.linksOmitted && <span>{w("연결 일부 생략됨", "Some references omitted")}</span>}{value.omittedFields.length > 0 && <span>{w("저장하지 못한 필드", "Fields not saved")}: <code>{value.omittedFields.join(", ")}</code></span>}</p>}
      </li>)}</ol> : <p className="execution-validation-note">{w("이 시도에는 확인할 수 있는 근거 기록이 없습니다.", "No evidence records are available for this attempt.")}</p>}
      {item.limited && <p className="execution-validation-limited">{w("저장 또는 표시 한도, 잘못된 값으로 일부 내용이 생략됐습니다.", "Some content was omitted due to storage or display limits, or invalid values.")}{item.omittedEvidenceCount > 0 && ` ${w(`근거 ${item.omittedEvidenceCount}개 미저장`, `${item.omittedEvidenceCount} evidence records not saved`)}`}</p>}
    </details>)}
  </section>;
}

export function StepMetrics({ metadata = {} }) {
  const w = useR5Words(); metadata = metadata || {};
  if (!metadata.model_profile && !metadata.model_name && !metadata.usage) return null;
  const tokens = key => Number.isSafeInteger(metadata.usage?.[key]) && metadata.usage[key] >= 0 ? metadata.usage[key].toLocaleString() : w("미기록", "Not recorded");
  return <div className="inspection-step-metrics"><dl>{[
    [w("모델", "Model"), metadata.model_profile || metadata.model_name || w("미기록", "Not recorded")],
    [w("공급자", "Provider"), metadata.llm_provider || metadata.provider || w("미기록", "Not recorded")],
    [w("입력 토큰", "Input tokens"), tokens("input_tokens")], [w("출력 토큰", "Output tokens"), tokens("output_tokens")],
  ].map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{value}</dd></div>)}</dl><p className="ux-muted">{w("토큰은 마지막 호출의 기록이며, 재시도를 포함한 단계 전체 사용량은 아닙니다.", "Tokens describe the last call, not the entire step including retries.")}</p></div>;
}

export function RepairSummary({ metadata }) {
  const w = useR5Words();
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
    <div><dt>{w("출력 형식 재시도", "Output format retries")}</dt><dd>{formatRetries === null ? w("미기록", "Not recorded") : w(`${formatRetries}회`, String(formatRetries))}</dd></div>
    <div><dt>{w("근거 인용 교정", "Evidence citation repairs")}</dt><dd>{Number.isInteger(grounding?.attempt_count) && grounding.attempt_count > 0 ? w(`${grounding.attempt_count - 1}회`, String(grounding.attempt_count - 1)) : w("미기록", "Not recorded")}</dd></div>
    {grounding?.attempted && <div><dt>{w("교정 결과", "Repair result")}</dt><dd>{grounding.recovered ? w("원문 대조 통과", "Source verification passed") : w("교정 미완료", "Repair incomplete")}</dd></div>}
  </dl>;
}
