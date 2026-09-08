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
  return <div className="quality-matrix"><h3>Confusion Matrix <small>정탐 = 양성 · 최종 판정 기준</small></h3><div className="table-wrap"><table><caption>참고 답안과 AI 최종 판정의 교차표 · 기대 답안이 보류인 문항은 별도 집계</caption><thead><tr><th scope="col">참고 답안 ↓ / AI →</th><th scope="col">정탐</th><th scope="col">오탐</th><th scope="col">판정 보류</th></tr></thead><tbody><tr><th scope="row">정탐</th>{matrixCells.slice(0, 3).map(renderCell)}</tr><tr><th scope="row">오탐</th>{matrixCells.slice(3).map(renderCell)}</tr></tbody></table></div>{onCell && <p className="evaluation-footnote">셀을 선택하면 문항 목록만 좁힙니다. 위 평가 지표와 혼동행렬의 분모는 유지합니다.</p>}</div>;
}
