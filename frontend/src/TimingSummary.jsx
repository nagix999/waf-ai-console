import { completedTimingSummary, durationText, performanceMetric, performanceNumber } from "./testPerformance.js";
import "./testPerformance.css";

export function TimingSummaryCards({ timings, words: w = ko => ko }) {
  const cards = [
    ["decision_elapsed_ms", w("접수 → 판정 완료", "Admission → decision"), w("초기 대기 포함", "Includes initial queue")],
    ["analysis_queue_ms", w("초기 대기", "Initial queue"), w("접수 → 첫 분석 시작", "Admission → first start")],
    ["analysis_processing_ms", w("분석 처리", "Analysis processing"), w("첫 시작 → 판정 완료", "First start → decision")],
    ["result_ready_elapsed_ms", w("접수 → 결과 준비", "Admission → result ready"), w("근거 정리 완료까지", "Includes evidence presentation")],
  ];
  return <dl className="test-performance-kpis timing-summary-cards">{cards.map(([key, label, note]) => {
    const metric = performanceMetric(timings?.[key]);
    return <div key={key} data-timing={key}><dt>{label}</dt><dd><span className="timing-mean-label">{w("평균", "Mean")}</span>{durationText(metric.mean_ms, w)}</dd><small>{note}</small>
      <div className="timing-percentiles">{[50, 95, 99].map(percentile => <span key={percentile}><span>P{percentile}</span><strong>{durationText(metric[`p${percentile}_ms`], w)}</strong></span>)}</div>
      <small>{w("기록", "Recorded")} {performanceNumber(metric.recorded)} / {w("미기록", "Missing")} {performanceNumber(metric.unrecorded)}</small>
    </div>;
  })}</dl>;
}

export function TimingSummaryNotes({ completedCases, completedOnly = true, words: w = ko => ko }) {
  return <>
    <p className="test-performance-note">{completedOnly ? w(`시간 평균과 P50/P95/P99는 완료된 ${performanceNumber(completedCases)}문항 중 각 시간 기록이 있는 문항 기준입니다. 실패, 중지와 처리 중인 문항은 제외합니다.`, `Timing means and P50/P95/P99 use recorded measurements among ${performanceNumber(completedCases)} completed cases. Failed, stopped and in-flight cases are excluded.`) : w("이 서버의 시간 집계는 완료와 실패 등 기록된 문항을 포함합니다. 완료 문항 전용 집계를 보려면 API 업데이트가 필요합니다.", "This server's timing aggregates include recorded finished cases, including failures. Update the API for completed-only statistics.")}</p>
    <p className="test-performance-note">{w("P50은 중앙값이며 P95와 P99는 측정값의 95%와 99%가 이 시간 이내임을 뜻합니다. 표본이 적으면 최댓값과 같을 수 있으며 미기록 값은 0으로 계산하지 않습니다.", "P50 is the median. P95 and P99 indicate the time within which 95% and 99% of measurements fall. With small samples they may equal the maximum; missing measurements are not treated as zero.")}</p>
    <p className="test-performance-note">{w("분석 처리는 초기 큐 대기만 제외하며 내부 대기, 재시도와 복구 시간을 포함합니다. HTTP 요청부터 응답까지의 시간은 별도로 측정하지 않았습니다.", "Processing excludes only the initial queue; internal waiting, retries and recovery remain included. HTTP request-to-response time is not measured here.")}</p>
  </>;
}

export default function CompletedTimingSummary({ summary, words: w = ko => ko, scopeNote }) {
  const data = completedTimingSummary(summary);
  return <section className="test-performance-summary completed-timing-summary" aria-label={w("처리 시간", "Processing time")}>
    <header className="test-performance-head"><div><h3>{w("처리 시간", "Processing time")}</h3>{scopeNote && <p className="test-performance-note">{scopeNote}</p>}</div></header>
    <TimingSummaryCards timings={data.completed_timings} words={w} />
    {data.completed_timings ? <TimingSummaryNotes completedCases={data.completed_cases} words={w} /> : <p className="test-performance-note">{w("완료 문항의 시간 통계가 제공되지 않았습니다. 값을 추정하지 않으며 이전 서버는 API 업데이트가 필요합니다.", "Completed-case timing statistics were not supplied. Values are not estimated; older servers require an API update.")}</p>}
  </section>;
}
