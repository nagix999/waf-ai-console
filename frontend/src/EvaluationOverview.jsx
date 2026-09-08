import { useState } from "react";
import Dialog from "./Dialog.jsx";
import { MetricCards } from "./EvaluationMetrics.jsx";
import { EvaluationSummary } from "./ReferenceLabels.jsx";

export default function EvaluationOverview({ summary, loading, error, scopeLabel, onMatrixCell, selectedCell }) {
  const [open, setOpen] = useState(false);
  return <section className="panel evaluation-overview" aria-label="평가 지표">
    <div className="panel-head"><div><h2>평가 지표</h2><p className="ux-muted">{scopeLabel || "검색 조건"}의 답안 표본 기준 · 운영 전체 정확도는 아닙니다.</p></div><button className="secondary" type="button" onClick={() => setOpen(true)} disabled={!summary || !!error}>평가 상세</button></div>
    {error ? <p className="error" role="alert">평가 지표를 불러오지 못했습니다.</p> : loading && !summary ? <p className="loading" role="status">지표를 불러오는 중…</p> : <>
      <MetricCards metrics={summary?.metrics} compact />
      <p className="evaluation-footnote">참고 답안 표본 · 확정 {summary?.binary_decided ?? 0}건 / 평가 대상 {summary?.binary_evaluable ?? 0}건 · 전체 {summary?.total ?? 0}건{!summary?.binary_evaluable && " · 평가 가능한 답안이 없습니다."}</p>
    </>}
    <Dialog open={open} title="평가 상세" onClose={() => setOpen(false)}><EvaluationSummary summary={summary} loading={loading} error={error} scopeLabel={scopeLabel} selectedCell={selectedCell} onMatrixCell={onMatrixCell ? cell => { onMatrixCell(cell); setOpen(false); } : undefined} /></Dialog>
  </section>;
}
