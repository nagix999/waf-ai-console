import { useEffect, useRef, useState } from "react";
import { api } from "./api.js";
import { useWords } from "./LifecycleViews.jsx";
import { startVisiblePolling } from "./visiblePolling.js";
import { analysisPerformanceIsRunning, analysisPerformanceScope, analysisPerformanceSummary } from "./analysisPerformance.js";
import { durationText, performanceNumber } from "./testPerformance.js";
import { TimingSummaryCards, TimingSummaryNotes } from "./TimingSummary.jsx";
import "./testPerformance.css";

export function ProductionTimingPreview({ metric, words: w = ko => ko }) {
  return <span className="production-timing-preview"><span>{w("분석 처리", "Analysis processing")}</span>{[50, 95, 99].map(percentile => <span key={percentile}>P{percentile} <strong>{durationText(metric?.[`p${percentile}_ms`], w)}</strong></span>)}</span>;
}

export function ProductionPerformanceContent({ summary, words: w = ko => ko, action }) {
  const data = analysisPerformanceSummary(summary);
  if (!data) return null;
  return <><div className="production-timing-actions"><p className="test-performance-note">{w(`현재 검색 조건에 맞는 전체 ${performanceNumber(data.cases)}건을 집계합니다. 페이지와 정렬 변경에는 영향을 받지 않습니다.`, `Aggregates all ${performanceNumber(data.cases)} analyses matching the applied search filters, independently of pagination and sorting.`)}</p>{action}</div>
      <TimingSummaryCards timings={data.completed_timings} words={w} />
      {data.completed_timings ? <TimingSummaryNotes completedCases={data.completed_cases} words={w} /> : <p className="test-performance-note">{w("완료 문항의 시간 기록이 제공되지 않았습니다. 값을 추정하지 않습니다.", "Completed-case timing measurements were not supplied. Values are not estimated.")}</p>}
  </>;
}

const emptyRead = () => ({ key: "", data: null, loading: true, error: false });
export default function ProductionPerformanceSummary({ filters = {}, refresh, onUnauthorized }) {
  const w = useWords(), unauthorized = useRef(onUnauthorized); unauthorized.current = onUnauthorized;
  const [read, setRead] = useState(emptyRead), [reload, setReload] = useState(0), [expanded, setExpanded] = useState(false);
  const lastRequest = useRef(null);
  const scope = analysisPerformanceScope(filters), key = JSON.stringify(scope), requestKey = JSON.stringify([key, refresh, reload]);
  const current = read.key === key ? read : emptyRead();
  useEffect(() => {
    const changed = lastRequest.current !== requestKey;
    lastRequest.current = requestKey;
    // Keep the compact preview's initial read, but do not poll a disclosure
    // the analyst has closed. Opening it refreshes the same retained cards.
    if (!expanded && !changed && read.key === key && read.data) {
      setRead(previous => previous.key === key ? { ...previous, loading: false } : previous);
      return;
    }
    let active = true;
    setRead(previous => previous.key === key ? { ...previous, loading: true, error: false } : { ...emptyRead(), key });
    let stop;
    stop = startVisiblePolling(async signal => {
      const result = await api.analysisPerformance(scope, { signal });
      if (!analysisPerformanceSummary(result)) throw Error("invalid_performance_summary");
      if (active && !signal.aborted) setRead({ key, data: result, loading: false, error: false });
      return expanded && analysisPerformanceIsRunning(result);
    }, { interval: null, activeInterval: 5000, onError: error => {
      if (!active) return;
      setRead(previous => ({ key, data: previous.key === key && ![401, 403, 404].includes(error.status) ? previous.data : null, loading: false, error: true }));
      if ([401, 403, 404].includes(error.status)) stop?.();
      if (error.status === 401) unauthorized.current?.();
    } });
    return () => { active = false; stop(); };
  }, [requestKey, expanded]);
  const data = analysisPerformanceSummary(current.data), metric = data?.completed_timings?.analysis_processing_ms;
  const refreshButton = <button type="button" className="secondary small" disabled={current.loading} onClick={() => setReload(value => value + 1)}>{w("다시 조회", "Refresh")}</button>;
  return <section className="panel production-performance-summary" aria-busy={current.loading}>
    <details open={expanded} onToggle={event => setExpanded(event.currentTarget.open)}><summary><span>{w("처리 시간", "Processing time")}</span><ProductionTimingPreview metric={metric} words={w} /></summary>
      <div className="production-timing-content">{!data && <div className="production-timing-actions">{refreshButton}</div>}
        {current.loading && (current.data ? <span className="sr-only" role="status">{w("최신 통계를 확인하는 중…", "Refreshing measurements…")}</span> : <p className="test-performance-note" role="status">{w("처리 시간 통계를 불러오는 중…", "Loading performance statistics…")}</p>)}
        {current.error && <p className="error" role="alert">{current.data ? w("통계를 갱신하지 못해 이전 값을 표시합니다. 다시 조회해 주세요.", "Refresh failed; showing the previous measurements. Try again.") : w("처리 시간 통계를 조회하지 못했습니다. 다시 조회해 주세요.", "Unable to load performance statistics. Try again.")}</p>}
        {data && <ProductionPerformanceContent summary={current.data} words={w} action={refreshButton} />}
      </div>
    </details>
  </section>;
}
