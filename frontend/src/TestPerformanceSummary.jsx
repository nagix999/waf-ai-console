import { useEffect, useId, useRef, useState } from "react";
import { api } from "./api.js";
import DataTable from "./DataTable.jsx";
import { useWords } from "./LifecycleViews.jsx";
import { watchTestRunRead } from "./TestRuns.jsx";
import { durationText, performanceFieldLabel, performanceIsRunning, performanceIssueLabel, performanceNumber, performanceSummary, retryFraction, testPerformanceScope, topPerformanceIssues } from "./testPerformance.js";
import "./testPerformance.css";

const roleLabel = (role, w) => role === "primary" ? w("1차 분석", "Primary") : role === "verifier" ? w("추가 검증", "Verifier") : w("이전 근거 정리", "Legacy evidence editor");
const recorded = (metric, w) => `${w("기록", "Recorded")} ${performanceNumber(metric.recorded)} / ${w("미기록", "Missing")} ${performanceNumber(metric.unrecorded)}`;
const fraction = value => value === null ? "—" : `${(value * 100).toFixed(1)}%`;

function MetricCell({ metric, words: w, note }) {
  return <><strong>{durationText(metric.mean_ms, w)}</strong><small>{note || recorded(metric, w)}</small></>;
}

export function PerformanceSummaryContent({ summary, words: w = ko => ko }) {
  const data = performanceSummary(summary);
  if (!data) return <p className="test-performance-note">{w("이 테스트에는 처리 시간 통계가 제공되지 않습니다.", "Performance statistics are not available for this test.")}</p>;
  const { timings, display } = data;
  const roles = ["primary", "verifier", ...(data.phases.llm_evidence_editor.entered_cases > 0 ? ["legacy_evidence_editor"] : [])].map(role => ({ role, ...data.roles[role], phase: data.phases[role === "primary" ? "llm_primary" : role === "verifier" ? "llm_verifier" : "llm_evidence_editor"] }));
  const issues = topPerformanceIssues(data);
  const outcomeLabels = [["output_validation", w("출력 검증 실패", "Validation failure")], ["output_limit", w("출력 한도 초과", "Output limit")], ["timeout", w("응답 시간 초과", "Response timeout")], ["incomplete_unspecified", w("완료되지 않은 출력", "Incomplete output")]];
  const columns = [
    { id: "role", header: w("분석 단계", "Analysis stage"), width: "17%", render: row => <><strong>{roleLabel(row.role, w)}</strong><small>{w("진입", "Entered")} {performanceNumber(row.phase.entered_cases)} / {w("미관측", "Not observed")} {performanceNumber(row.phase.not_observed_cases)}</small><small>{w("호출 기록", "Call records")} {performanceNumber(row.accounting.recorded_cases)} / {w("미기록", "Missing")} {performanceNumber(row.accounting.unrecorded_cases)}</small></> },
    { id: "first_attempt_ms", header: w("첫 호출 시간", "First attempt time"), width: "16%", className: "numeric", render: row => <MetricCell metric={row.first_attempt_ms} words={w} note={`${w("호출 대기", "Slot wait")} ${durationText(row.slot_wait_ms.mean_ms, w)}`} /> },
    { id: "first_output_tokens", header: w("첫 출력량", "First output tokens"), width: "14%", className: "numeric", render: row => <><strong>{performanceNumber(row.tokens.first_attempt_output_tokens.mean)}</strong><small>{recorded(row.tokens.first_attempt_output_tokens, w)}</small></> },
    { id: "output_tokens", header: w("전체 출력량", "All output tokens"), width: "14%", className: "numeric", render: row => <><strong>{performanceNumber(row.tokens.output_tokens.mean)}</strong><small>{w("재시도 포함", "Includes retries")}</small><small>{recorded(row.tokens.output_tokens, w)}</small></> },
    { id: "output_retries", header: w("출력 재시도 문항", "Output retry cases"), width: "20%", className: "numeric", render: row => <><strong>{fraction(retryFraction(row))}</strong><small>{performanceNumber(row.accounting.cases_with_output_retries)} / {performanceNumber(row.accounting.recorded_cases)}</small><small>{w("근거 연결 교정", "Citation repair")} {performanceNumber(row.accounting.cases_with_citation_repairs)}</small></> },
    { id: "output_retry_ms", header: w("출력 재시도 시간", "Output retry time"), width: "19%", className: "numeric", render: row => <><MetricCell metric={row.output_retry_ms} words={w} /><small>{w("근거 연결 교정", "Citation repair")} {durationText(row.citation_repair_ms.mean_ms, w)}</small></> },
  ];
  const issueColumns = [
    { id: "role", header: w("분석 단계", "Stage"), width: "17%", render: row => roleLabel(row.role, w) },
    { id: "field", header: w("항목", "Field"), width: "24%", render: row => performanceFieldLabel(row.field, w) },
    { id: "type", header: w("출력 검증 사유", "Validation issue"), width: "44%", render: row => performanceIssueLabel(row.type, w) },
    { id: "attempts", header: w("시도", "Attempts"), width: "15%", className: "numeric", render: row => performanceNumber(row.attempts) },
  ];
  const pendingDisplay = [display.states.pending, display.states.running].every(value => value !== null) ? display.states.pending + display.states.running : null;
  return <>
    <p className="test-performance-note">{w("선택한 문항 조건에 맞는 전체 범위입니다. 집계 기준, 항목 검색과 페이지 변경에는 영향을 받지 않습니다.", "Covers all cases matching the case filters. Grouping, group search and pagination do not change the scope.")} {w("전체 문항", "All cases")} {performanceNumber(data.cases)}.</p>
    <dl className="test-performance-kpis">
      <div><dt>{w("평균 결과 준비 시간", "Mean result-ready time")}</dt><dd>{durationText(timings.result_ready_elapsed_ms.mean_ms, w)}</dd><small>{recorded(timings.result_ready_elapsed_ms, w)}</small></div>
      <div><dt>{w("결과 준비 시간 P95", "Result-ready time P95")}</dt><dd>{durationText(timings.result_ready_elapsed_ms.p95_ms, w)}</dd><small>{w("기록된 문항의 95%가 이 시간 이내에 준비됩니다.", "95% of recorded cases were ready within this time.")}</small></div>
      <div><dt>{w("평균 분석 처리 시간", "Mean analysis processing time")}</dt><dd>{durationText(timings.analysis_processing_ms.mean_ms, w)}</dd><small>{w("분석 대기", "Analysis queue")} {durationText(timings.analysis_queue_ms.mean_ms, w)} / {w("판정까지", "Until decision")} {durationText(timings.decision_elapsed_ms.mean_ms, w)}</small></div>
    </dl>
    <p className="test-performance-note">{w("결과 준비는 접수부터 판정과 근거 정리 완료까지이며, 시간별로 기록된 문항만 계산합니다.", "Result-ready time runs from admission until the verdict and evidence presentation are ready, using recorded measurements only.")}</p>
    <DataTable className="test-performance-role-table" label={w("단계별 처리 시간과 출력량", "Stage timing and output tokens")} columns={columns} data={roles} getRowId={row => row.role} />
    <p className="test-performance-note">{w("문항당 평균이며 출력량 단위는 토큰입니다. 첫 호출에는 통신과 검증이 포함되고, 재시도 비율은 호출 기록이 있는 문항 기준입니다.", "Means are per case; output is in tokens. First-attempt time includes transport and validation. Retry rates use cases with call records.")}</p>
    <p className="test-performance-note">{w("기록 문항 수가 다르면 첫 출력량과 전체 출력량의 차이를 재시도로 늘어난 양으로 볼 수 없습니다.", "When recorded case counts differ, the difference between first and total output means is not the added retry output.")}</p>
    <p className="test-performance-note">{w("미관측은 실행 기록 없음이며 건너뛰었다는 뜻은 아닙니다. 미기록은 0이 아니며, 겹치는 단계 시간은 더하지 않습니다.", "Not observed means no stage record, not confirmed skipping. Missing is not zero; overlapping durations cannot be added.")}</p>
    <div className="test-performance-display">
      <div><h4>{w("근거 정리", "Evidence presentation")}</h4></div>
      <dl><div><dt>{w("평균 대기", "Mean queue")}</dt><dd>{durationText(display.queue_ms.mean_ms, w)}</dd><small>{recorded(display.queue_ms, w)}</small></div><div><dt>{w("평균 처리", "Mean processing")}</dt><dd>{durationText(display.processing_ms.mean_ms, w)}</dd><small>{recorded(display.processing_ms, w)}</small></div><div><dt>{w("대기 또는 처리 중", "Queued or running")}</dt><dd>{performanceNumber(pendingDisplay)}</dd><small>{w("별도 작업 없음", "No separate job")} {performanceNumber(display.no_job_cases)}</small></div></dl>
    </div>
    <div className="test-performance-failures">
      <h4>{w("출력 문제와 교정", "Output issues and recovery")}</h4>
      <div className="test-performance-recovery">{roles.map(row => <div key={row.role}><p><strong>{roleLabel(row.role, w)}</strong><span>{w("출력 문제 발생", "Cases with output issues")} {performanceNumber(row.output_failures.cases_with_output_failure)}</span><span>{w("해당 단계 복구 완료", "Stage recovered")} {performanceNumber(row.output_failures.completed_cases_with_output_failure)}</span></p>{outcomeLabels.some(([key]) => row.output_failures.attempt_outcomes[key] > 0) && <p className="test-performance-outcomes"><span>{w("시도 기준", "Attempts")}:</span>{outcomeLabels.filter(([key]) => row.output_failures.attempt_outcomes[key] > 0).map(([key, label]) => <span key={key}>{label} {performanceNumber(row.output_failures.attempt_outcomes[key])}</span>)}</p>}</div>)}</div>
      {!!issues.length && <><DataTable className="test-performance-issue-table" label={w("많이 발생한 출력 검증 사유", "Most frequent output validation issues")} data={issues} columns={issueColumns} getRowId={row => row.rowKey} /><p className="test-performance-note">{w("많이 기록된 검증 사유를 최대 5개 표시합니다. 한 시도에 여러 사유가 있을 수 있으며, 교정 후 성공한 문항도 포함합니다.", "Shows up to 5 most frequently recorded validation issues. One attempt can have several issues; successfully recovered cases are included.")}</p></>}
      <p className="test-performance-note">{w("출력량, 재시도 시간과 근거 정리 대기를 함께 비교하세요. 집계만으로 지연 원인이 확정되지는 않습니다.", "Compare output tokens, retry time and evidence-presentation waiting. Aggregates alone do not establish the cause of slowness.")}</p>
    </div>
  </>;
}

const emptyRead = () => ({ key: null, data: null, error: "", loading: true });
export default function TestPerformanceSummary({ run, query = {}, refresh, onUnauthorized, words: suppliedWords }) {
  const contextWords = useWords(), w = suppliedWords || contextWords, headingId = useId();
  const [read, setRead] = useState(emptyRead), [reload, setReload] = useState(0);
  const unauthorized = useRef(onUnauthorized); unauthorized.current = onUnauthorized;
  const scope = testPerformanceScope(query);
  const key = JSON.stringify([run.id, scope, refresh, reload, run.status, run.completed, run.failed, run.pending, run.processing, run.canceled]);
  const current = read.key === key ? read : emptyRead();
  useEffect(() => {
    setRead({ ...emptyRead(), key });
    return watchTestRunRead({ read: async options => {
      const result = await api.testPerformance(run.id, scope, options);
      if (result?.test_run_id !== run.id || !performanceSummary(result)) throw Error("invalid_performance_summary");
      return result;
    }, onUpdate: patch => setRead(previous => ({ ...previous, ...patch, key })),
    isRunning: performanceIsRunning,
    errorMessage: "performance_summary_unavailable",
    onUnauthorized: () => unauthorized.current?.() });
  }, [key]);
  return <section className="test-performance-summary" aria-labelledby={headingId} aria-busy={current.loading}>
    <header className="test-performance-head"><div><h3 id={headingId}>{w("처리 시간과 출력", "Processing time and output")}</h3><p className="test-performance-note">{w("응답 길이, 출력 재시도와 대기 시간을 한눈에 비교합니다.", "Compare output size, retries and waiting time at a glance.")}</p></div><button type="button" className="secondary" disabled={current.loading} onClick={() => setReload(value => value + 1)}>{w("다시 조회", "Refresh")}</button></header>
    {current.error && <p className="error" role="alert">{current.error === "performance_summary_unavailable" ? w("처리 시간 통계를 조회하지 못했습니다. 다시 조회해 주세요.", "Unable to load performance statistics. Try again.") : w("로그인 세션이 만료되었습니다. 다시 로그인하세요.", "Your session expired. Sign in again.")}</p>}
    {!current.data && !current.error && <p className="test-performance-note" role="status">{w("처리 시간 통계를 불러오는 중…", "Loading performance statistics…")}</p>}
    {current.data && <PerformanceSummaryContent summary={current.data} words={w} />}
  </section>;
}
