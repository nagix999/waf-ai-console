import { useState } from "react";
import { useConsolePreferences } from "./consolePreferences.jsx";
import { MetricHelp, AdditionalMetrics } from "./EvaluationMetrics.jsx";
import { metricText } from "./evaluationMetrics.js";
import DataTable from "./DataTable.jsx";
import "./r5.css";

export const useR5Words = () => { const { locale } = useConsolePreferences(); return (ko, en) => locale === "en" ? en : ko; };
export const purposeText = (purpose, w) => purpose === "official_evaluation" ? w("공식 테스트", "Official Test") : purpose === "development" ? w("개발 테스트", "Development Test") : w("이전 테스트", "Legacy Test");
export const verdictText = (value, w) => ({ true_positive: w("정탐", "True Positive"), false_positive: w("오탐", "False Positive"), inconclusive: w("판정 보류", "Inconclusive") })[value] || "—";
export function InitialAssessment({ value, verdict, confidence, compact = false }) {
  const w = useR5Words(); if (!value || value.comparison === "unavailable") return null;
  const warning = ["different", "final_inconclusive"].includes(value.comparison);
  const labels = { match: w("일치", "Match"), different: w("판정 다름", "Different"), final_inconclusive: w("심층 판정이 보류되었습니다", "Deep Assessment is inconclusive"), pending: w("심층 판정 대기", "Deep Assessment pending") };
  if (compact) return <small className={warning ? "r5-initial-warning" : "muted"}>{labels[value.comparison]}</small>;
  return <section className={`panel r5-initial ${warning ? "r5-initial-warning" : ""}`}><h3>{w("1차 판정과 심층 판정", "Initial and Deep Assessment")}</h3><div className="r5-assessment-pair"><div><span>{w("1차 판정", "Initial Assessment")}</span><strong>{verdictText(value.verdict, w)} · {Number.isFinite(value.probability) ? `${(value.probability * 100).toFixed(1)}%` : "—"}</strong><small>{value.model_version || "—"}</small></div><div><span>{w("심층 판정", "Deep Assessment")}</span><strong>{verdictText(verdict, w)}{Number.isFinite(confidence) ? ` · ${(confidence * 100).toFixed(1)}%` : ""}</strong><small>{labels[value.comparison]}</small></div></div><p className="v5-context">{w("1차 판정은 수집기가 제공한 정보이며 심층 판정의 모델 입력이나 정답으로 사용하지 않습니다.", "The collector's Initial Assessment is not used as a model input or Ground Truth.")}</p></section>;
}

export default function R5Evaluation({ summary, onMatrixCell, selectedCell, official, scopeLabel }) {
  const w = useR5Words(), [details, setDetails] = useState(false);
  if (!summary) return <section className="panel"><p>{w("평가 결과가 아직 없습니다.", "No evaluation is available yet.")}</p></section>;
  const m = summary.metrics || {}, matrix = summary.confusion_matrix || {}, counts = summary.outcomes || {};
  const metrics = [["accuracy", "Accuracy"], ["precision", "Precision"], ["recall", "Recall"], ["f1", "F1"], ["coverage", "Coverage"]];
  const cell = (key, label) => <button type="button" className={`r5-matrix-cell ${["fp", "fn"].includes(key) ? "r5-matrix-error" : ""}`} aria-label={`${label}: ${matrix[key] ?? 0}`} aria-pressed={selectedCell === key} onClick={() => onMatrixCell?.(key)} disabled={!onMatrixCell}><strong>{matrix[key] ?? 0}</strong><span>{label}</span></button>;
  return <section className="panel r5-evaluation"><div className="panel-head"><div><h2>{w("평가 상세", "Evaluation Details")}</h2><small>{scopeLabel} · {official ? w("공식 버전에 고정된 정답", "Published Ground Truth") : w("참고 라벨 비교 · 운영 반영에 사용할 수 없음", "Reference Label comparison · not eligible for Production")}</small></div></div>
    <div className="r5-metric-grid">{metrics.map(([key, label]) => <div key={key}><span>{label}<MetricHelp metric={key} label={label} /></span><strong>{Number.isFinite(m[key]) ? metricText(m[key]) : "—"}</strong></div>)}</div>
    <p className="v5-context">{w(`정탐·오탐 답안 ${summary.binary_evaluable ?? 0}건 중 확정 판정 ${summary.binary_decided ?? 0}건. 실패와 보류를 오답으로 바꾸어 계산하지 않습니다.`, `${summary.binary_decided ?? 0} decided cases out of ${summary.binary_evaluable ?? 0} binary reference cases. Failures and holds are not counted as classification errors.`)}</p>
    <div className="r5-evaluation-grid"><div><h3>{w("정답과 심층 판정 비교", "Ground Truth × Deep Assessment")}</h3><div className="r5-matrix" role="group" aria-label={w("정탐·오탐 2×2 혼동행렬", "Binary 2×2 confusion matrix")}><span>{w("정답 ↓ / 심층 판정 →", "Ground Truth ↓ / Deep Assessment →")}</span><strong>{verdictText("true_positive", w)}</strong><strong>{verdictText("false_positive", w)}</strong><strong>{verdictText("true_positive", w)}</strong>{cell("tp", w("정탐 일치", "Correct attack"))}{cell("fn", w("공격을 오탐으로 판정", "Attack classified as benign"))}<strong>{verdictText("false_positive", w)}</strong>{cell("fp", w("정상 요청을 정탐으로 판정", "Benign classified as attack"))}{cell("tn", w("오탐 일치", "Correct benign"))}</div></div>
    <div><h3>{w("보류·평가 제외", "Holds and exclusions")}</h3><dl className="r5-counts">{[[w("정탐 답안 → 보류", "Attack reference → hold"), matrix.abstained_positive], [w("오탐 답안 → 보류", "Benign reference → hold"), matrix.abstained_negative], [w("보류 답안 일치", "Hold reference matched"), matrix.expected_hold_match], [w("보류 답안에 확정 판정", "Hold reference decided"), (matrix.expected_hold_positive || 0) + (matrix.expected_hold_negative || 0)], [w("참고 라벨 없음", "No Reference Label"), counts.unlabeled], [w("실패 / 처리 중", "Failed / pending"), `${counts.failed || 0} / ${counts.pending || 0}`], [w("모의·출처 미확인·정답 포함 입력", "Stub, unknown origin or contaminated input"), (counts.stub || 0) + (counts.unknown_provenance || 0) + (counts.input_contaminated || 0)]].map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{value ?? 0}</dd></div>)}</dl><p className="v5-context">{w("보류 답안은 이진 지표에서 제외합니다. 보류의 일치 여부는 별도로 평가합니다.", "Hold references are excluded from binary metrics and evaluated separately.")}</p></div></div>
    <AdditionalMetrics metrics={m} />
    {!!summary.source_groups?.length && <><button className="text-button" aria-expanded={details} onClick={() => setDetails(v => !v)}>{w("출처별 집계", "Reference sources")}</button>{details && <DataTable label={w("출처별 집계", "Reference sources")} data={summary.source_groups} getRowId={r => `${r.source_kind}-${r.ai_visible}`} columns={[{ id: "source", header: w("출처", "Source"), render: r => r.source_kind }, { id: "count", header: w("비교 가능", "Evaluable"), render: r => r.evaluable }, { id: "match", header: w("일치", "Matches"), render: r => r.matches }]} />}</>}
  </section>;
}
