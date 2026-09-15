import { useState } from "react";
import { api } from "./api.js";
import Dialog from "./Dialog.jsx";
import { newTestRequestKey } from "./testRuns.js";
import { answerNames, datasetImportMessage, selectedPageIds, validationDataError } from "./validationData.js";
import "./validationData.css";

export function useAnalysisSelection(items, scope) {
  const [state, setState] = useState({ scope, ids: [] });
  const ids = selectedPageIds(state.scope === scope ? state.ids : [], items);
  const available = [...new Set(items.filter(item => item.analysis_id !== null).map(item => item.analysis_id || item.id))];
  const toggle = id => setState({ scope, ids: ids.includes(id) ? ids.filter(value => value !== id) : [...ids, id] });
  return { ids, clear: () => setState({ scope, ids: [] }),
    cell: (id, label = "분석") => <input type="checkbox" aria-label={`${label} 선택`} checked={ids.includes(id)} onChange={() => toggle(id)} />,
    header: <input type="checkbox" aria-label="현재 페이지 전체 선택" aria-checked={ids.length > 0 && ids.length < available.length ? "mixed" : ids.length > 0} ref={element => { if (element) element.indeterminate = ids.length > 0 && ids.length < available.length; }} checked={available.length > 0 && ids.length === available.length} disabled={!available.length} onChange={event => setState({ scope, ids: event.target.checked ? available : [] })} /> };
}

export default function AnalysisSelectionActions({ ids, onSaved, single = false, onClear }) {
  const [dialog, setDialog] = useState(null), [busy, setBusy] = useState(false), [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  async function open(kind) {
    if (busy || !ids.length) return;
    setBusy(true); setError(""); setNotice("");
    try {
      const selected = [...ids];
      if (kind === "reference") {
        const result = await api.referenceSelection(selected);
        const history = single ? await api.evaluationLabels(selected[0]) : null;
        setDialog({ kind, selected, targets: result.items, verdict: single ? result.items[0].verdict || "" : "", comment: single ? history?.items?.[0]?.comment || "" : "", key: newTestRequestKey() });
      } else {
        const catalog = await api.validationDatasets({ limit: 200 });
        setDialog({ kind, selected, catalog, datasetId: "", offset: 0 });
      }
    } catch (err) { setError(validationDataError(err)); }
    finally { setBusy(false); }
  }
  async function save(event) {
    event.preventDefault(); if (busy) return;
    setBusy(true); setError("");
    try {
      let message = "";
      if (dialog.kind === "reference") {
        const result = await api.saveReferences({ targets: dialog.targets.map(({ analysis_id, expected_revision }) => ({ analysis_id, expected_revision })), verdict: dialog.verdict, comment: dialog.comment, idempotency_key: dialog.key });
        message = `${result.applied_count}건의 참고 답안을 저장했습니다.`;
        setNotice(message);
      } else {
        const chosen = dialog.catalog.items.find(item => item.id === dialog.datasetId);
        const result = await api.importDatasetAnalyses(chosen.id, { expected_revision: chosen.revision, analysis_ids: dialog.selected });
        message = datasetImportMessage(result);
        if (result.conflicts.length || result.rejected.length) {
          setDialog({ ...dialog, result, message });
          onSaved?.(); return;
        }
        setNotice(message);
      }
      setDialog(null); onSaved?.({ kind: dialog.kind, message }); onClear?.();
    } catch (err) { setError(validationDataError(err)); }
    finally { setBusy(false); }
  }
  const change = field => event => setDialog(current => ({ ...current, [field]: event.target.value, key: newTestRequestKey() }));
  async function datasetPage(offset) {
    setBusy(true); setError("");
    try { const catalog = await api.validationDatasets({ limit: 200, offset }); setDialog(current => ({ ...current, catalog, datasetId: "", offset })); }
    catch (err) { setError(validationDataError(err)); } finally { setBusy(false); }
  }
  return <div className="selection-actions">
    <div className="ux-toolbar">{!single && <span>{ids.length}건 선택 · 현재 페이지</span>}<button type="button" className="secondary" disabled={busy || !ids.length} onClick={() => open("reference")}>참고 답안 {single ? "입력" : "일괄 입력"}</button><button type="button" className="secondary" disabled={busy || !ids.length} onClick={() => open("dataset")}>데이터셋에 추가</button>{ids.length > 0 && onClear && <button type="button" className="text-button" onClick={onClear}>선택 해제</button>}</div>
    {notice && <p role="status" className="notice">{notice}</p>}{error && !dialog && <p role="alert" className="error">{error}</p>}
    <Dialog open={Boolean(dialog)} title={dialog?.kind === "reference" ? "참고 답안 입력" : "데이터셋에 추가"} onClose={() => { if (!busy) { setDialog(null); setError(""); } }}>
      {dialog && <form onSubmit={save} className="data-form"><p>{dialog.selected.length}건 선택</p>
        {dialog.kind === "reference" ? <><p className="ux-muted">기존 답안이 있는 {dialog.targets.filter(item => item.expected_revision).length}건도 새 답안으로 저장합니다. 이전 답안·메모와 AI 판정은 보존됩니다.</p><label>참고 답안<select aria-label="참고 답안" required value={dialog.verdict} onChange={change("verdict")} disabled={busy}><option value="">선택하세요</option>{Object.entries(answerNames).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label><label>메모<textarea value={dialog.comment} maxLength={4000} rows={4} onChange={change("comment")} disabled={busy} placeholder="답안 판단 이유 또는 확인 내용" /></label><p className="ux-muted">AI 결과를 본 뒤 작성한 답안으로 기록합니다. 답안·메모는 모델에 전달하지 않습니다.</p></> : <>
          <label>데이터셋<select aria-label="데이터셋" required value={dialog.datasetId} onChange={change("datasetId")} disabled={busy || Boolean(dialog.result)}><option value="">선택하세요</option>{dialog.catalog.items.map(item => <option key={item.id} value={item.id}>{item.name} · {item.total}건</option>)}</select></label>{!dialog.catalog.items.length && <p>데이터 관리에서 데이터셋을 먼저 만드세요.</p>}<p className="ux-muted">동일 입력은 제외합니다. 답안이나 내부 모델 제한이 다르면 충돌로 알립니다. 운영에서 가져온 문항은 내부 vLLM으로만 테스트할 수 있습니다.</p>
          {!dialog.result && (dialog.offset > 0 || dialog.catalog.total > 200) && <div className="pagination"><span>데이터셋 {dialog.catalog.total}개</span><button type="button" disabled={busy || !dialog.offset} onClick={() => datasetPage(Math.max(0, dialog.offset - 200))}>이전 목록</button><button type="button" disabled={busy || dialog.offset + 200 >= dialog.catalog.total} onClick={() => datasetPage(dialog.offset + 200)}>다음 목록</button></div>}
          {dialog.result && <div role="status"><p>{dialog.message}</p><p>충돌 문항은 추가하지 않았습니다. 기존 문항의 답안·내부 모델 제한을 확인하세요. 입력 오류 문항은 현재 입력 스키마를 확인하세요.</p><ul>{dialog.result.conflicts.map((id, index) => <li key={id}>충돌 {index + 1} · <a href={`#analyses/${encodeURIComponent(id)}`}>분석 확인</a></li>)}{dialog.result.rejected.map((item, index) => <li key={item.analysis_id}>입력 오류 {index + 1} · <a href={`#analyses/${encodeURIComponent(item.analysis_id)}`}>분석 확인</a> · {validationDataError({ message: item.code, status: 422 })}</li>)}</ul></div>}
        </>}
        {error && <p className="error" role="alert">{error}</p>}
        {!dialog.result && <button type="submit" className="primary" disabled={busy || (dialog.kind === "reference" ? !dialog.verdict : !dialog.datasetId)}>{busy ? "저장 중…" : dialog.kind === "reference" ? "답안 저장" : "추가"}</button>}
      </form>}
    </Dialog>
  </div>;
}
