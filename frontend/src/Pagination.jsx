import { paginationState } from "./pagination.js";
import "./pagination.css";

export default function Pagination({ total, limit, offset, onOffsetChange, onLimitChange,
  pageSizes = [25, 50, 100], disabled = false, label = "목록 페이지", unit = "건", note }) {
  const state = paginationState(total, limit, offset);
  function go(page) {
    const next = Math.max(0, Math.min(state.lastOffset, (page - 1) * state.limit));
    if (!disabled && next !== state.offset) onOffsetChange(next);
  }
  return <nav className="pagination" aria-label={label}>
    <div className="pagination-meta">
      <span aria-live="polite">{state.total ? `${state.firstRow.toLocaleString()}–${state.lastRow.toLocaleString()} / ${state.total.toLocaleString()}${unit}` : `0${unit}`} · {state.page.toLocaleString()} / {state.pageCount.toLocaleString()}페이지</span>
      {onLimitChange && <label>페이지당<select aria-label={`${label} 페이지당 항목 수`} value={state.limit} disabled={disabled} onChange={event => onLimitChange(Number(event.target.value))}>{pageSizes.map(size => <option key={size} value={size}>{size}{unit}</option>)}</select></label>}
    </div>
    <div className="pagination-controls">
      <button type="button" className="secondary" aria-label="첫 페이지" disabled={disabled || state.offset === 0} onClick={() => go(1)}>처음</button>
      <button type="button" className="secondary" aria-label="이전 페이지" disabled={disabled || state.offset === 0} onClick={() => go(Math.ceil(state.offset / state.limit))}>이전</button>
      {state.pages[0] > 1 && <span className="pagination-gap" aria-hidden="true">…</span>}
      {state.pages.map(page => <button key={page} type="button" className="secondary pagination-number" aria-label={`${page}페이지`} aria-current={page === state.page ? "page" : undefined} disabled={disabled} onClick={() => go(page)}>{page.toLocaleString()}</button>)}
      {state.pages.at(-1) < state.pageCount && <span className="pagination-gap" aria-hidden="true">…</span>}
      <button type="button" className="secondary" aria-label="다음 페이지" disabled={disabled || state.offset >= state.lastOffset} onClick={() => go(state.page + 1)}>다음</button>
      <button type="button" className="secondary" aria-label="마지막 페이지" disabled={disabled || state.offset >= state.lastOffset} onClick={() => go(state.pageCount)}>끝</button>
    </div>
    {note && <small className="pagination-note">{note}</small>}
  </nav>;
}
