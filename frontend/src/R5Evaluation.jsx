import { useState } from "react";
import { useConsolePreferences } from "./consolePreferences.jsx";
import { MetricHelp, AdditionalMetrics } from "./EvaluationMetrics.jsx";
import { metricText } from "./evaluationMetrics.js";
import DataTable from "./DataTable.jsx";
import "./r5.css";
import "./overviewEvaluationDesign.css";

export const useR5Words = () => { const { locale } = useConsolePreferences(); return (ko, en) => locale === "en" ? en : ko; };
export const purposeText = (purpose, w) => purpose === "official_evaluation" ? w("공식 테스트", "Official Test") : purpose === "development" ? w("개발 테스트", "Development Test") : w("이전 테스트", "Legacy Test");
export const verdictText = (value, w) => ({ true_positive: w("정탐", "True Positive"), false_positive: w("오탐", "False Positive"), inconclusive: w("판정 보류", "Inconclusive") })[value] || "—";
export function InitialAssessment({ value, verdict, confidence, compact = false }) {
  const w = useR5Words(); if (!value || value.comparison === "unavailable") return null;
  const warning = ["different", "final_inconclusive"].includes(value.comparison);
  const labels = { match: w("일치", "Match"), different: w("판정 다름", "Different"), final_inconclusive: w("심층 판정이 보류되었습니다", "Deep Assessment is inconclusive"), pending: w("심층 판정 대기", "Deep Assessment pending") };
  if (compact) return <small className={warning ? "r5-initial-warning" : "muted"}>{labels[value.comparison]}</small>;
  return <section className={`panel r5-initial ${warning ? "r5-initial-warning" : ""}`}><h3>{w("1차 판정과 심층 판정", "Initial and Deep Assessment")}</h3><div className="r5-assessment-pair"><div><span>{w("1차 판정", "Initial Assessment")}</span><strong>{verdictText(value.verdict, w)} · {Number.isFinite(value.probability) ? `${(value.probability * 100).toFixed(1)}%` : "—"}</strong><small>{value.model_version || "—"}</small></div><div><span>{w("심층 판정", "Deep Assessment")}</span><strong>{verdictText(verdict, w)}{Number.isFinite(confidence) ? ` · ${(confidence * 100).toFixed(1)}%` : ""}</strong><small>{labels[value.comparison]}</small></div></div><p className="v5-context">{w("1차 판정은 수집기가 제공한 정보이며 심층 판정의 모델 입력이나 정답으로 사용하지 않습니다.", "The collector's Initial Assessment is not used as a model input or Evaluation Dataset.")}</p></section>;
}

export default function R5Evaluation({ summary, onMatrixCell, selectedCell, official, scopeLabel }) {
  const w = useR5Words(), [details, setDetails] = useState(false);
  if (!summary) return <section className="panel"><p>{w("평가 결과가 아직 없습니다.", "No evaluation is available yet.")}</p></section>;
  const m = summary.metrics || {}, matrix = summary.confusion_matrix || {}, counts = summary.outcomes || {};
  const metrics = [["accuracy", "Accuracy"], ["precision", "Precision"], ["recall", "Recall"], ["f1", "F1"]];
  const risks = [["fn", w("공격 → 정상", "Attack → benign")], ["abstained_positive", w("공격 → 보류", "Attack → hold")], ["fp", w("정상 → 공격", "Benign → attack")], ["abstained_negative", w("정상 → 보류", "Benign → hold")], ["expected_hold_positive", w("보류 기대 판정 → 정탐 확정", "Hold reference → attack")], ["expected_hold_negative", w("보류 기대 판정 → 오탐 확정", "Hold reference → benign")]];
  const classes = ["true_positive", "false_positive", "inconclusive"];
  const keys = [["tp", "fn", "abstained_positive"], ["fp", "tn", "abstained_negative"], ["expected_hold_positive", "expected_hold_negative", "expected_hold_match"]];
  const compared = keys.flat().reduce((sum, key) => sum + (matrix[key] ?? 0), 0);
  const reference = official ? w("기대 판정", "Expected Verdict") : w("참고 판정", "Reference Verdict");
  const cell = (key, label, diagonal = false, matrixCell = false) => <button type="button" className={matrixCell ? `oe-matrix-cell ${diagonal ? "is-match" : (matrix[key] ?? 0) > 0 ? "is-mismatch" : ""}` : `r5-matrix-cell ${["fp", "fn"].includes(key) ? "r5-matrix-error" : "r5-matrix-hold"}`} data-matrix-cell={matrixCell ? key : undefined} aria-label={`${label}: ${matrix[key] ?? 0}`} aria-pressed={selectedCell === key} onClick={() => onMatrixCell?.(key)} disabled={!onMatrixCell}><strong>{matrix[key] ?? 0}</strong><span>{matrixCell ? diagonal ? w("일치", "Match") : w("불일치", "Different") : label}</span></button>;
  return <div className="oe-evaluation-v2" aria-label={w("평가 상세", "Evaluation Details")}>
    <div className="oe-evaluation-metrics">{metrics.map(([key, label]) => <article key={key}><span>{label}<MetricHelp metric={key} label={label} /></span><strong>{Number.isFinite(m[key]) ? metricText(m[key], key) : "—"}</strong><small>{w("정탐과 오탐 참고 판정 중 확정한 결과", "Decided binary reference cases")}</small></article>)}</div>
    <div className="oe-evaluation-layout">
      <section className="panel oe-matrix-panel"><header className="oe-panel-heading"><div><h2>{reference}{w("과 심층 판정 비교", " × Deep Assessment")}</h2><p>{w("셀을 누르면 해당 조합의 문항으로 이동합니다.", "Select a cell to view the matching cases.")}</p></div><span className="oe-inline-count">{compared}{w("건 비교", " compared")}</span></header>
        <div className="oe-matrix-axis">{w("가로: 심층 판정 / 세로: ", "Columns: Deep Assessment / rows: ")}{reference}</div>
        <div className="oe-matrix-grid" role="group" aria-label={w("정탐, 오탐과 보류 3×3 혼동행렬", "Three-class 3×3 confusion matrix")}><span className="oe-matrix-corner">{reference} ↓<br />{w("심층 판정 →", "Deep Assessment →")}</span>{classes.map(value => <strong key={value} className="oe-matrix-label">{verdictText(value, w)}</strong>)}{classes.map((expected, row) => <div className="oe-matrix-row" key={expected}><strong className="oe-matrix-label">{verdictText(expected, w)}</strong>{classes.map((actual, column) => <div key={actual}>{cell(keys[row][column], `${reference} ${verdictText(expected, w)} → ${w("심층", "Deep Assessment")} ${verdictText(actual, w)}`, row === column, true)}</div>)}</div>)}</div>
        <p className="oe-evaluation-note">{w("참고가 보류인 문항도 행렬에 포함합니다. 판정 없음과 실행 실패는 비교에서 제외합니다.", "Reference holds are included. Missing reference verdicts and execution failures are excluded from the matrix.")}</p>
      </section>
      <section className="panel oe-evaluation-scope"><header className="oe-panel-heading"><h2>{w("집계 범위", "Evaluation scope")}</h2></header><dl>{[[w("전체 문항", "All cases"), summary.total], [w("비교 가능한 문항", "Comparable cases"), compared], [w("참고 판정 없음", "No Reference Verdict"), counts.unlabeled], [w("실행 실패", "Execution failures"), counts.failed], [w("처리 중", "Pending"), counts.pending], [w("중지됨", "Stopped"), counts.canceled]].map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{value ?? 0}</dd></div>)}</dl>
        <div className="oe-evaluation-basis"><strong>{official ? w("공식 버전의 기대 판정", "Expected verdicts from a Published Version") : w("참고 판정 비교", "Reference Verdict comparison")}</strong><p>{scopeLabel || w("선택한 테스트 문항", "Selected Test cases")}</p><p>{w("위 네 지표는 정탐과 오탐 기대 판정 중 확정한 결과만 비교합니다. 전체 요청의 정확도가 아닙니다. 보류를 미탐이나 과탐으로 바꾸지 않습니다.", "The four metrics compare decided binary reference cases only, not all requests. Holds are not reclassified as false negatives or false positives.")}</p>{!official && <p>{w("이 비교는 운영 반영의 공식 평가로 사용할 수 없습니다.", "This reference comparison does not qualify as an Official Test for Production.")}</p>}</div>
        <div className="oe-coverage-inline"><span>Coverage<MetricHelp metric="coverage" label="Coverage" /></span><strong>{metricText(m.coverage, "coverage")}</strong><small>{summary.binary_decided ?? 0} / {summary.binary_evaluable ?? 0}</small></div>
      </section>
    </div>
    <details className="r5-statistics oe-evaluation-more"><summary>{w("보류와 오류 상세", "Hold and error details")}</summary><h3>{w("오류와 보류 방향", "Errors and holds by direction")}</h3><div className="r5-risk-grid">{risks.map(([key, label]) => <div key={key}>{cell(key, label)}</div>)}</div><p className="v5-context">{w("보류 기대 판정의 정탐 또는 오탐 확정은 실제 미탐과 과탐에서 구분합니다.", "Decisions on hold references are distinct from actual false negatives and false positives.")}</p><div className="r5-metric-grid">{[["balanced_accuracy", "Balanced Accuracy"], ["mcc", "MCC"]].map(([key, label]) => <div key={key}><span>{label}<MetricHelp metric={key} label={label} /></span><strong>{Number.isFinite(m[key]) ? metricText(m[key], key) : "—"}</strong></div>)}</div><AdditionalMetrics metrics={m} /><p className="v5-context">{w("모의 실행, 출처 미확인 또는 기대 판정이 입력에 섞인 문항은 평가에서 제외합니다.", "Stub runs, unknown provenance and input-contaminated cases are excluded.")} {(counts.stub || 0) + (counts.unknown_provenance || 0) + (counts.input_contaminated || 0)}{w("건", " cases")}</p>
    {!!summary.source_groups?.length && <><button type="button" className="text-button" aria-expanded={details} onClick={() => setDetails(value => !value)}>{w("출처별 집계", "Reference sources")}</button>{details && <DataTable label={w("출처별 집계", "Reference sources")} data={summary.source_groups} getRowId={row => `${row.source_kind}-${row.ai_visible}`} columns={[{ id: "source", header: w("출처", "Source"), render: row => row.source_kind }, { id: "count", header: w("비교 가능", "Evaluable"), render: row => row.evaluable }, { id: "match", header: w("일치", "Matches"), render: row => row.matches }]} />}</>}</details>
  </div>;
}
