import { useEffect, useRef, useState } from "react";
import { useWords } from "./LifecycleViews.jsx";
import Dialog from "./Dialog.jsx";
import { fetchTestExportPreview, fetchSelectedTestExport, saveTestExport, testExportError, testExportFilters } from "./testExport.js";
import "./testExport.css";

export default function TestExport({ id, filters, onUnauthorized }) {
  const w = useWords(), current = useRef(null), previewRequest = useRef(null), initialized = useRef(false);
  const [open, setOpen] = useState(false), [busy, setBusy] = useState(false), [loading, setLoading] = useState(false);
  const [preview, setPreview] = useState(null), [selected, setSelected] = useState([]), [search, setSearch] = useState("");
  const [error, setError] = useState("");
  const [scopeChanged, setScopeChanged] = useState(false);
  const query = testExportFilters(filters), key = JSON.stringify([id, query]);
  useEffect(() => {
    initialized.current = false;
    setOpen(false); setBusy(false); setLoading(false); setPreview(null); setError(""); setSearch(""); setScopeChanged(false);
    return () => { current.current?.abort(); previewRequest.current?.abort(); current.current = null; previewRequest.current = null; };
  }, [key]);
  const close = () => { current.current?.abort(); previewRequest.current?.abort(); current.current = null; previewRequest.current = null; setOpen(false); setBusy(false); setLoading(false); };
  async function check() {
    previewRequest.current?.abort();
    const controller = new AbortController(); previewRequest.current = controller;
    setOpen(true); setLoading(true); setError(""); setScopeChanged(false);
    try {
      const next = await fetchTestExportPreview(id, query, { signal: controller.signal });
      if (controller.signal.aborted || previewRequest.current !== controller) return;
      const retain = initialized.current;
      setSelected(previous => retain ? next.columns.filter(column => previous.includes(column.name)).map(column => column.name) : next.columns.map(column => column.name));
      initialized.current = true;
      setPreview(next);
    } catch (error) {
      if (!controller.signal.aborted && previewRequest.current === controller) {
        setPreview(null); setError(testExportError(error, w)); if (error.status === 401) onUnauthorized?.();
      }
    } finally { if (previewRequest.current === controller) { previewRequest.current = null; setLoading(false); } }
  }
  async function download() {
    if (current.current || !preview || !selected.length) return;
    const controller = new AbortController(); current.current = controller;
    setBusy(true); setError("");
    try {
      const blob = await fetchSelectedTestExport(id, { filters: query, expected_count: preview.count, preview_scope: preview.scope,
        columns: preview.columns.filter(column => selected.includes(column.name)).map(column => column.name) }, { signal: controller.signal });
      if (!controller.signal.aborted && current.current === controller) { saveTestExport(blob, id); setOpen(false); }
    } catch (error) {
      if (!controller.signal.aborted && current.current === controller) {
        setError(testExportError(error, w)); setScopeChanged(error.message === "test_export_selection_changed"); if (error.status === 401) onUnauthorized?.();
      }
    } finally { if (current.current === controller) { current.current = null; setBusy(false); } }
  }
  const groups = [["case", w("문항", "Case")], ["input", w("입력", "Input")], ["result", w("분석 결과", "Analysis result")],
    ["evaluation", w("답안 비교·1차 판정", "Reference / initial assessment")], ["execution", w("실행 정보", "Execution")]];
  const visible = (preview?.columns || []).filter(column => (column.name + " " + column.label_ko).toLowerCase().includes(search.trim().toLowerCase()));
  return <div className="test-run-export"><button type="button" className="secondary" onClick={check}>{w("Excel 다운로드", "Download Excel")}</button>
    <span className="test-export-scope">{w("문항 목록의 조건에 맞는 전체 결과", "All cases matching the Case Results filters")}</span>
    <Dialog open={open} title={w("Excel 다운로드", "Download Excel")} className="test-export-dialog" onClose={close}>
      <div className="test-export-summary"><strong role="status" aria-live="polite">{loading ? w("대상 확인 중…", "Checking cases…") : preview ? w(preview.count.toLocaleString() + "건 다운로드", "Export " + preview.count.toLocaleString() + " cases") : w("대상을 확인하지 못했습니다.", "Unable to check cases.")}</strong>
        <p>{w("현재 페이지뿐 아니라 문항 목록의 필터·정렬·답안 기준에 맞는 모든 문항입니다.", "Includes every matching case, not just the current page, using the selected filters, order and reference basis.")}</p>
        <button type="button" className="text-button" disabled={busy || loading} onClick={check}>{w("대상 다시 확인", "Check scope again")}</button></div>
      {error && <p className="error" role="alert">{error}</p>}
      {preview && <><div className="test-export-toolbar"><label>{w("필드 검색", "Find fields")}<input type="search" value={search} onChange={event => setSearch(event.target.value)} /></label>
        <div><button type="button" className="secondary small" disabled={busy || loading} onClick={() => setSelected(preview.columns.map(column => column.name))}>{w("전체 선택", "Select all")}</button>
          <button type="button" className="secondary small" disabled={busy || loading} onClick={() => setSelected([])}>{w("모두 해제", "Clear all")}</button></div></div>
        <div className="test-export-fields" aria-busy={loading}>{groups.map(([group, label]) => {
          const fields = visible.filter(column => column.group === group);
          return fields.length ? <fieldset key={group} disabled={busy || loading}><legend>{label}</legend><div className="test-export-field-grid">{fields.map(column => <label key={column.name}>
            <input type="checkbox" checked={selected.includes(column.name)} onChange={event => setSelected(previous => event.target.checked ? [...previous, column.name] : previous.filter(name => name !== column.name))} />
            <span><strong>{column.label_ko}</strong><code>{column.name}</code></span></label>)}</div></fieldset> : null;
        })}{!visible.length && <p className="muted">{w("검색한 필드가 없습니다.", "No matching fields.")}</p>}</div>
        <p className="test-export-note">{w("파일의 컬럼명은 영어입니다. 분석결과·컬럼안내 두 시트에 선택 필드와 형식 예시를 제공합니다. 긴 값은 이어짐 컬럼으로 나눕니다.", "English headers are used in the Analysis Results and Column Guide sheets. The guide includes illustrative examples. Long values use continuation columns.")}</p>
        {selected.includes("payload") && <p className="notice">{w("HTTP 원문과 Cookie가 마스킹 없이 포함될 수 있습니다. 파일을 안전하게 보관하세요.", "Unmasked HTTP and Cookie data may be included. Store the file securely.")}</p>}
        {!preview.count && <p role="status">{w("조건에 맞는 문항이 없습니다.", "No matching cases.")}</p>}
      </>}
      <div className="test-export-footer"><span>{preview ? w(selected.length + "개 필드 선택", selected.length + " fields selected") : ""}</span>
        <button type="button" className="secondary" onClick={close}>{w("닫기", "Close")}</button>
        <button type="button" className="primary" disabled={loading || busy || scopeChanged || !preview?.count || !selected.length} onClick={download}>{busy ? w("Excel 작성 중…", "Preparing Excel…") : w("다운로드", "Download")}</button></div>
    </Dialog></div>;
}
