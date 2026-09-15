import { Table } from "./DataTable.jsx";
import { useState } from "react";
import HelpTooltip from "./HelpTooltip.jsx";
import Dialog from "./Dialog.jsx";
import { extraMetrics, mainMetrics, matrixCells, metricHelp, metricText } from "./evaluationMetrics.js";
import "./evaluationMetrics.css";

export function MetricHelp({ metric, label }) {
  return <HelpTooltip label={label}>{metricHelp[metric]}</HelpTooltip>;
}

export function MetricCards({ metrics, compact = false }) {
  return <div className={`quality-metrics${compact ? " quality-metrics-compact" : ""}`}>{mainMetrics.map(([key, label]) => <div className={`quality-metric quality-metric-${key}`} key={key}><span>{label}<MetricHelp metric={key} label={label} /></span><strong>{metricText(metrics?.[key], key)}</strong></div>)}</div>;
}

export function AdditionalMetrics({ metrics }) {
  const [open, setOpen] = useState(false);
  return <><button type="button" className="secondary" onClick={() => setOpen(true)}>추가 지표</button><Dialog open={open} title="추가 평가 지표" onClose={() => setOpen(false)}><div className="quality-additional"><dl>{extraMetrics.map(([key, label]) => <div key={key}><dt>{label}<MetricHelp metric={key} label={label} /></dt><dd>{metricText(metrics?.[key], key)}</dd></div>)}</dl><p className="evaluation-footnote">계산에 필요한 표본이 없으면 —로 표시합니다.</p></div></Dialog></>;
}

export function ConfusionMatrix({ matrix, onCell, selectedCell }) {
  if (!matrix) return <p className="evaluation-footnote">혼동행렬 정보가 없습니다. 최신 집계를 다시 조회하세요.</p>;
  const renderCell = ([key, label]) => <td key={key} className={`matrix-${key}`}>
    {onCell ? <button type="button" aria-label={`${label} ${matrix[key] ?? 0}건 문항 보기`} aria-pressed={selectedCell === key} onClick={() => onCell(key)}><strong>{matrix[key] ?? 0}</strong><small>{label}</small></button> : <><strong>{matrix[key] ?? 0}</strong><small>{label}</small></>}
  </td>;
  const held = matrix.expected_hold_match;
  const confirmed = (matrix.expected_hold_positive ?? 0) + (matrix.expected_hold_negative ?? 0);
  const support = typeof held === "number" ? held + confirmed : 0;
  return <div className="quality-matrix"><h3>Confusion Matrix <small>최종 판정 기준</small></h3><div className="table-wrap"><Table><caption>참고 답안과 AI 최종 판정의 교차표 · 정탐·오탐·보류</caption><thead><tr><th scope="col">참고 답안 ↓ / AI →</th><th scope="col">정탐</th><th scope="col">오탐</th><th scope="col">판정 보류</th></tr></thead><tbody>{["정탐", "오탐", "판정 보류"].map((label, row) => <tr key={label}><th scope="row">{label}</th>{matrixCells.slice(row * 3, row * 3 + 3).map(renderCell)}</tr>)}</tbody></Table></div>
    <div className="quality-metrics quality-metrics-compact">{[["expected_hold_match_rate", "보류 답안 일치율", held], ["expected_hold_decided_rate", "보류 답안 확정률", confirmed]].map(([key, label, count]) => <div className="quality-metric" key={key}><span>{label}<MetricHelp metric={key} label={label} /></span><strong>{metricText(support ? count / support : null, key)}</strong></div>)}</div>
    <p className="evaluation-footnote">기존 Accuracy·Precision·Recall·F1은 정탐·오탐 답안의 확정 판정 기준입니다. 보류 답안의 정탐·오탐 확정은 실제 미탐·과탐과 구분합니다.</p>{onCell && <p className="evaluation-footnote">셀을 선택하면 문항 목록만 좁힙니다. 위 평가 지표와 혼동행렬의 분모는 유지합니다.</p>}</div>;
}
