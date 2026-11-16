import { useEffect, useRef, useState } from "react";
import { useConsolePreferences } from "./consolePreferences.jsx";
import DataTable from "./DataTable.jsx";
import Pagination from "./Pagination.jsx";
import { parserWorkbenchApi } from "./parserWorkbenchApi.js";
import { parserWorkbenchError } from "./parserWorkbench.js";

export default function ParserSourcePreview({ source, maximum, selected, onSelection, onReady, disabled }) {
  const { locale } = useConsolePreferences(), w = (ko, en) => locale === "en" ? en : ko;
  const [offset, setOffset] = useState(0), [revision, reload] = useState(0), [read, setRead] = useState({ key: "", data: null, error: "" });
  const sourceKey = JSON.stringify([source, maximum]), selectedKey = useRef(""), key = `${sourceKey}:${offset}:${revision}`;
  const view = read.key === key ? read : { data: null, error: "" };
  useEffect(() => { setOffset(0); onReady(false); }, [sourceKey]);
  useEffect(() => {
    if (!source || !Number.isInteger(maximum) || maximum < 2 || maximum > 100) { onReady(false); return; }
    const controller = new AbortController(); onReady(false);
    parserWorkbenchApi.sourcePreview({ source, max_cases: maximum, limit: 20, offset }, { signal: controller.signal }).then(data => {
      if (controller.signal.aborted) return;
      if (selectedKey.current !== sourceKey) { onSelection(data.recommended_case_ids); selectedKey.current = sourceKey; }
      setRead({ key, data, error: "" }); onReady(true);
    }).catch(error => { if (!controller.signal.aborted) { setRead({ key, data: null, error: parserWorkbenchError(error, locale === "en") }); onReady(false); } });
    return () => controller.abort();
  }, [key]);
  const rows = view.data?.items || [], pageIds = rows.map(row => row.case_id);
  const checked = pageIds.length > 0 && pageIds.every(id => selected.includes(id));
  const stateNames = { success: w("구조 인식", "Recognized"), partial: w("일부 미인식", "Partial"), failed: w("인식 실패", "Failed"), unrecorded: w("미기록", "Not recorded") };
  if (!source) return null;
  return <section className="pw-source-preview" aria-busy={!view.data && !view.error}><div className="pw-section-heading"><div><h3>{w("사용할 문항", "Choose cases")}</h3><p>{w("미인식과 기존 형식을 고르게 추천합니다. 원문을 열지 않고 문항을 추가하거나 제외할 수 있습니다.", "Recommendations balance unread and existing formats. Add or remove cases without revealing raw input.")}</p></div><button type="button" className="text-button" disabled={disabled || !view.data} onClick={() => onSelection(view.data.recommended_case_ids)}>{w("추천 문항 선택", "Select recommended cases")}</button></div>
    {view.error ? <p className="error" role="alert">{view.error} <button type="button" className="text-button" disabled={disabled} onClick={() => reload(value => value + 1)}>{w("다시 조회", "Retry")}</button></p> : <><p className="pw-inline-note"><strong>{selected.length}</strong> {w("선택", "selected")} / {w("최소 2문항, 최대", "minimum 2, maximum")} {maximum}. {view.data ? `${w("확인한 입력", "Scanned")}: ${view.data.scanned_count} / ${view.data.total}` : w("문항을 추천하는 중…", "Loading recommendations…")}</p>{view.data?.scan_limited && <p className="notice">{w("조회 한도 안의 문항에서 추천했습니다. 전체 입력을 확인한 결과가 아닙니다.", "Recommendations cover the scanned range, not every source case.")}</p>}<DataTable label={w("파서 표본 문항", "Parser sample cases")} data={rows} getRowId={row => row.case_id} columns={[
      { id: "selected", width: 44, header: <input type="checkbox" aria-label={w("현재 페이지 문항 선택", "Select current page")} disabled={disabled || !rows.length} checked={checked} onChange={event => onSelection(event.target.checked ? [...new Set([...selected, ...pageIds])].slice(0, maximum) : selected.filter(id => !pageIds.includes(id)))} />, render: row => <input type="checkbox" aria-label={`${row.case_name} ${w("선택", "select")}`} checked={selected.includes(row.case_id)} disabled={disabled || !selected.includes(row.case_id) && selected.length >= maximum} onChange={event => onSelection(event.target.checked ? [...selected, row.case_id] : selected.filter(id => id !== row.case_id))} /> },
      { id: "case_name", header: w("문항", "Case") }, { id: "waf_vendor", header: w("벤더", "Vendor") },
      { id: "parser_status", header: w("기존 인식 상태", "Recorded parsing"), render: row => stateNames[row.parser_status] || w("확인 필요", "Needs Attention") },
      { id: "recommended", header: w("추천", "Suggested"), render: row => row.recommended ? w("추천", "Suggested") : "—" },
    ]} empty={view.data ? w("선택할 문항이 없습니다.", "No cases available.") : w("불러오는 중…", "Loading…")} /><Pagination total={view.data?.total || 0} offset={offset} limit={20} disabled={disabled || !view.data} onOffsetChange={setOffset} label={w("표본 문항 페이지", "Sample case pages")} /></>}
  </section>;
}
