import { paginationState } from "./pagination.js";
import "./pagination.css";
import { useConsolePreferences } from "./consolePreferences.jsx";

export default function Pagination({ total, limit, offset, onOffsetChange, onLimitChange,
  pageSizes = [25, 50, 100], disabled = false, label = "목록 페이지", unit = "건", note }) {
  const state = paginationState(total, limit, offset);
  const { locale } = useConsolePreferences(), w = (ko, en) => locale === "en" ? en : ko;
  const suffix = locale === "en" ? "" : unit;
  function go(page) {
    const next = Math.max(0, Math.min(state.lastOffset, (page - 1) * state.limit));
    if (!disabled && next !== state.offset) onOffsetChange(next);
  }
  return <nav className="pagination" aria-label={label}>
    <div className="pagination-meta">
      <span aria-live="polite">{state.total ? `${state.firstRow.toLocaleString()}–${state.lastRow.toLocaleString()} / ${state.total.toLocaleString()}${suffix}` : `0${suffix}`} · {state.page.toLocaleString()} / {state.pageCount.toLocaleString()}{w("페이지", " pages")}</span>
      {onLimitChange && <label>{w("페이지당", "Per page")}<select aria-label={w(`${label} 페이지당 항목 수`, "Items per page")} value={state.limit} disabled={disabled} onChange={event => onLimitChange(Number(event.target.value))}>{pageSizes.map(size => <option key={size} value={size}>{size}{suffix}</option>)}</select></label>}
    </div>
    <div className="pagination-controls">
      <button type="button" className="secondary" aria-label={w("첫 페이지", "First page")} disabled={disabled || state.offset === 0} onClick={() => go(1)}>{w("처음", "First")}</button>
      <button type="button" className="secondary" aria-label={w("이전 페이지", "Previous page")} disabled={disabled || state.offset === 0} onClick={() => go(Math.ceil(state.offset / state.limit))}>{w("이전", "Previous")}</button>
      {state.pages[0] > 1 && <span className="pagination-gap" aria-hidden="true">…</span>}
      {state.pages.map(page => <button key={page} type="button" className="secondary pagination-number" aria-label={w(`${page}페이지`, `Page ${page}`)} aria-current={page === state.page ? "page" : undefined} disabled={disabled} onClick={() => go(page)}>{page.toLocaleString()}</button>)}
      {state.pages.at(-1) < state.pageCount && <span className="pagination-gap" aria-hidden="true">…</span>}
      <button type="button" className="secondary" aria-label={w("다음 페이지", "Next page")} disabled={disabled || state.offset >= state.lastOffset} onClick={() => go(state.page + 1)}>{w("다음", "Next")}</button>
      <button type="button" className="secondary" aria-label={w("마지막 페이지", "Last page")} disabled={disabled || state.offset >= state.lastOffset} onClick={() => go(state.pageCount)}>{w("끝", "Last")}</button>
    </div>
    {note && <small className="pagination-note">{note}</small>}
  </nav>;
}
