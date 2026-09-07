import { useEffect, useId, useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { extraMetrics, mainMetrics, matrixCells, metricHelp, metricText } from "./evaluationMetrics.js";
import "./evaluationMetrics.css";

export function MetricHelp({ metric, label }) {
  const id = useId(); const [open, setOpen] = useState(false); const [position, setPosition] = useState(null);
  const trigger = useRef(null); const tooltip = useRef(null); const closing = useRef(null);
  const show = () => { clearTimeout(closing.current); setOpen(true); };
  const hide = () => { clearTimeout(closing.current); setOpen(false); setPosition(null); };
  const leave = () => { clearTimeout(closing.current); closing.current = setTimeout(() => { if (document.activeElement !== trigger.current) hide(); }, 140); };
  useEffect(() => () => clearTimeout(closing.current), []);
  useLayoutEffect(() => {
    if (!open) return undefined;
    const place = () => {
      if (!trigger.current || !tooltip.current) return;
      const anchor = trigger.current.getBoundingClientRect(); const box = tooltip.current.getBoundingClientRect();
      const left = Math.max(8, Math.min(anchor.left + anchor.width / 2 - box.width / 2, window.innerWidth - box.width - 8));
      const below = anchor.bottom + 8; const preferredTop = below + box.height <= window.innerHeight - 8 ? below : anchor.top - box.height - 8;
      setPosition({ left, top: Math.max(8, Math.min(preferredTop, window.innerHeight - box.height - 8)) });
    };
    const dismiss = event => { if (event.key === "Escape" || (event.type === "pointerdown" && !trigger.current?.contains(event.target) && !tooltip.current?.contains(event.target))) hide(); };
    place(); window.addEventListener("resize", place); window.addEventListener("scroll", place, true); document.addEventListener("keydown", dismiss); document.addEventListener("pointerdown", dismiss);
    return () => { window.removeEventListener("resize", place); window.removeEventListener("scroll", place, true); document.removeEventListener("keydown", dismiss); document.removeEventListener("pointerdown", dismiss); };
  }, [open]);
  return <span className="metric-help" onMouseEnter={show} onMouseLeave={leave}><button ref={trigger} type="button" className="metric-help-trigger" aria-label={`${label} 설명`} aria-describedby={open ? id : undefined} onFocus={show} onBlur={hide} onClick={show} onKeyDown={event => { if (event.key === "Escape") { hide(); event.stopPropagation(); } }}>?</button>{open && createPortal(<span ref={tooltip} className="metric-help-tooltip" style={{ ...(position || {}), visibility: position ? "visible" : "hidden" }} id={id} role="tooltip" onMouseEnter={show} onMouseLeave={leave}>{metricHelp[metric]}</span>, document.body)}</span>;
}

export function MetricCards({ metrics, compact = false }) {
  return <div className={`quality-metrics${compact ? " quality-metrics-compact" : ""}`}>{mainMetrics.map(([key, label, help]) => <div className={`quality-metric quality-metric-${key}`} key={key}><span>{label}<MetricHelp metric={key} label={label} /></span><strong>{metricText(metrics?.[key], key)}</strong><small>{help}</small></div>)}</div>;
}

export function AdditionalMetrics({ metrics }) {
  return <details className="quality-additional"><summary>추가 지표 · 클래스 불균형과 오류 방향 확인</summary><dl>{extraMetrics.map(([key, label, help]) => <div key={key}><dt>{label}<MetricHelp metric={key} label={label} /><small>{help}</small></dt><dd>{metricText(metrics?.[key], key)}</dd></div>)}</dl><p className="evaluation-footnote">Coverage·보류율·보류 포함 정답률을 제외한 지표는 보류를 제외한 확정 판정 기준입니다. 지표별 분모가 0이면 계산하지 않습니다. Balanced Accuracy·Macro F1은 확정한 참고 정탐·오탐이 모두 필요합니다.</p></details>;
}

export function ConfusionMatrix({ matrix, onCell, selectedCell }) {
  if (!matrix) return <p className="evaluation-footnote">혼동행렬 정보가 없습니다. 최신 집계를 다시 조회하세요.</p>;
  const renderCell = ([key, label]) => <td key={key} className={`matrix-${key}`}>
    {onCell ? <button type="button" aria-label={`${label} ${matrix[key] ?? 0}건 문항 보기`} aria-pressed={selectedCell === key} onClick={() => onCell(key)}><strong>{matrix[key] ?? 0}</strong><small>{label}</small></button> : <><strong>{matrix[key] ?? 0}</strong><small>{label}</small></>}
  </td>;
  return <div className="quality-matrix"><h3>Confusion Matrix <small>정탐 = 양성 · 최종 판정 기준</small></h3><div className="table-wrap"><table><caption>참고 답안과 AI 최종 판정의 교차표 · 기대 답안이 보류인 문항은 별도 집계</caption><thead><tr><th scope="col">참고 답안 ↓ / AI →</th><th scope="col">정탐</th><th scope="col">오탐</th><th scope="col">판정 보류</th></tr></thead><tbody><tr><th scope="row">정탐</th>{matrixCells.slice(0, 3).map(renderCell)}</tr><tr><th scope="row">오탐</th>{matrixCells.slice(3).map(renderCell)}</tr></tbody></table></div>{onCell && <p className="evaluation-footnote">셀을 선택하면 문항 목록만 좁힙니다. 위 평가 지표와 혼동행렬의 분모는 유지합니다.</p>}</div>;
}
