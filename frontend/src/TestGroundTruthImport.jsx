import { useEffect, useRef, useState } from "react";
import { api } from "./api.js";
import Dialog from "./Dialog.jsx";
import DataTable from "./DataTable.jsx";
import Pagination from "./Pagination.jsx";
import { useR5Words } from "./R5Evaluation.jsx";
import { newTestRequestKey } from "./testRuns.js";
import { importCategories, importCounts, importError } from "./groundTruthImport.js";

export default function TestGroundTruthImport({ runId, open, onClose, onOpenDataset, itemIds }) {
  const w = useR5Words();
  const [target, setTarget] = useState("create_new_dataset"), [name, setName] = useState(""), [datasetId, setDatasetId] = useState("");
  const [catalog, setCatalog] = useState(null), [query, setQuery] = useState(""), [catalogOffset, setCatalogOffset] = useState(0);
  const [preview, setPreview] = useState(null), [result, setResult] = useState(null), [error, setError] = useState("");
  const [phase, setPhase] = useState(null), [category, setCategory] = useState(""), [offset, setOffset] = useState(0);
  const busy = phase !== null, inFlight = useRef(false), requestKey = useRef(null), generation = useRef(0), feedback = useRef(null);
  const selectionKey = JSON.stringify(itemIds || null);
  function resetPreview() { setPreview(null); setError(""); setResult(null); requestKey.current = null; setOffset(0); setCategory(""); }
  useEffect(() => { generation.current += 1; resetPreview(); inFlight.current = false; setPhase(null); return () => { generation.current += 1; }; }, [open, runId, selectionKey]);
  useEffect(() => {
    if (!open || target !== "append_to_existing_dataset") return;
    const controller = new AbortController();
    const timer = setTimeout(() => api.searchValidationDatasets({ query, offset: catalogOffset, limit: 20 }, { signal: controller.signal })
      .then(value => { if (!controller.signal.aborted) setCatalog(value); })
      .catch(() => { if (!controller.signal.aborted) setError(w("평가 데이터셋을 불러오지 못했습니다.", "Could not load datasets.")); }), 180);
    return () => { clearTimeout(timer); controller.abort(); };
  }, [open, target, query, catalogOffset]);
  useEffect(() => {
    if (open && (preview || result || error)) { feedback.current?.focus({ preventScroll: true }); feedback.current?.scrollIntoView({ block: "nearest", behavior: "auto" }); }
  }, [open, preview, result, error]);
  async function inspect(event) {
    event.preventDefault(); if (inFlight.current) return;
    inFlight.current = true; setPhase("preview"); resetPreview(); const token = generation.current;
    try {
      const working = target === "append_to_existing_dataset" ? await api.workingDataset(datasetId) : null;
      if (token !== generation.current) return;
      const value = await api.previewTestImport(runId, { target, ...(itemIds ? { test_run_item_ids: itemIds } : {}),
        ...(working ? { dataset_id: datasetId, expected_working_revision: working.working_revision } : { new_dataset_name: name.trim() || null }) });
      if (token === generation.current) { setPreview(value); requestKey.current = newTestRequestKey(); }
    } catch (e) { if (token === generation.current) setError(w(...importError(e, "preview"))); }
    finally { if (token === generation.current) { inFlight.current = false; setPhase(null); } }
  }
  async function confirm() {
    if (inFlight.current || !preview || result) return;
    inFlight.current = true; setPhase("confirm"); setError(""); const token = generation.current;
    try {
      const saved = await api.confirmTestImport(runId, { preview_token: preview.preview_token, idempotency_key: requestKey.current });
      if (token === generation.current) setResult(saved);
    } catch (e) { if (token === generation.current) { if ([404, 409].includes(e.status)) setPreview(null); setError(w(...importError(e, "confirm"))); } }
    finally { if (token === generation.current) { inFlight.current = false; setPhase(null); } }
  }
  function changeCatalog(value) { setCatalog(null); setDatasetId(""); resetPreview(); setCatalogOffset(value); }
  const entries = preview?.items.filter(row => !category || row.category === category) || [];
  return <Dialog open={open} title={w("테스트 문항을 평가 데이터셋에 복사", "Copy Test Cases to Evaluation Dataset")} onClose={() => { if (!inFlight.current) onClose(); }}>
    <div className="data-form" aria-busy={busy}>
      <p className="v5-context">{w("테스트의 요청 입력과 기존 기대 판정을 평가 데이터셋 초안으로 복사합니다. AI 심층 판정은 기대 판정으로 사용하지 않습니다. 기대 판정이 없는 문항은 확인 필요 상태로 추가됩니다.", "Copy request inputs and existing expected verdicts into an Evaluation Dataset draft. Deep Assessment is never used as the expected verdict. Cases without an expected verdict are marked Needs attention.")}</p>
      <p className="v5-context">{w("모델 처리 완료 여부와 관계없이 공식 버전의 기대 판정, 없으면 접수 당시 참고 판정만 복사합니다. 미리보기 후 ‘초안에 추가’를 눌러 저장하세요. 공식 버전 만들기는 별도입니다.", "Model processing does not need to finish. We copy the Published Version's expected verdict, or the Reference Verdict fixed at admission. Preview, then Add to Draft to save. Publishing is a separate action.")}</p>
      {result ? <section ref={feedback} tabIndex={-1} aria-label={w("저장 결과", "Save result")}><h3 role="status">{w("초안에 추가했습니다.", "Cases added to Draft.")}</h3><p>{w(`추가 ${result.added} · 중복 ${result.duplicates} · 충돌 ${result.conflicts}`, `Added ${result.added} · duplicates ${result.duplicates} · conflicts ${result.conflicts}`)}</p><button className="primary" onClick={() => { onClose(); onOpenDataset?.(result.dataset_id); }}>{w("평가 데이터셋 열기", "Open Evaluation Dataset")}</button></section> : <>
        <form className="data-form" onSubmit={inspect}>
          <label>{w("저장 위치", "Destination")}<select aria-label={w("저장 위치", "Destination")} value={target} disabled={busy} onChange={e => { setTarget(e.target.value); resetPreview(); }}><option value="create_new_dataset">{w("새 평가 데이터셋 만들기", "Create new Evaluation Dataset")}</option><option value="append_to_existing_dataset">{w("기존 평가 데이터셋에 추가", "Add to existing Evaluation Dataset")}</option></select></label>
          {target === "create_new_dataset" ? <label>{w("평가 데이터셋 이름", "Evaluation Dataset name")}<input maxLength={120} value={name} disabled={busy} placeholder={w("비워두면 테스트명 사용", "Defaults to Test name")} onChange={e => { setName(e.target.value); resetPreview(); }} /></label> : <>
            <label>{w("평가 데이터셋 검색", "Find Evaluation Dataset")}<input disabled={busy} value={query} onChange={e => { setQuery(e.target.value); changeCatalog(0); }} /></label>
            <label>{w("평가 데이터셋", "Evaluation Dataset")}<select aria-label={w("평가 데이터셋", "Evaluation Dataset")} required value={datasetId} disabled={busy || !catalog} onChange={e => { setDatasetId(e.target.value); resetPreview(); }}><option value="">{w("선택하세요", "Choose dataset")}</option>{catalog?.items.map(row => <option key={row.id} value={row.id}>{row.name} · {row.total} {w("문항", "cases")}</option>)}</select></label>
            {catalog && <Pagination total={catalog.total} limit={20} offset={catalogOffset} onOffsetChange={changeCatalog} disabled={busy} label={w("평가 데이터셋 페이지", "Dataset pages")} />}
          </>}
          <button className="secondary" disabled={busy || target === "append_to_existing_dataset" && !datasetId}>{phase === "preview" ? w("미리보기 준비 중…", "Preparing preview…") : w("미리보기", "Preview")}</button>
        </form>
        {preview && <section className="data-form" ref={error ? null : feedback} tabIndex={-1} aria-label={w("가져오기 미리보기", "Import preview result")}>
          <h3>{w(`미리보기 · 전체 ${preview.source_total}문항`, `Preview · ${preview.source_total} cases`)}</h3>
          <div className="r5-import-summary">{Object.entries(importCounts(preview)).map(([key, count]) => <span key={key}>{w(...importCategories[key])}<strong>{count}</strong></span>)}</div><p className="v5-context">{w("각 문항은 아래 구분 중 하나에만 집계됩니다.", "Each case is counted in exactly one category.")}</p>
          <label>{w("문항 구분", "Category")}<select aria-label={w("문항 구분", "Category")} value={category} disabled={busy} onChange={e => { setCategory(e.target.value); setOffset(0); }}><option value="">{w("전체", "All")}</option>{Object.entries(importCategories).map(([key, label]) => <option key={key} value={key}>{w(...label)}</option>)}</select></label>
          <DataTable className="r5-import-preview-table" label={w("가져올 문항 미리보기", "Import preview")} data={entries.slice(offset, offset + 10)} getRowId={row => row.test_run_item_id} columns={[{ id: "name", header: w("문항", "Case"), render: row => row.case_name || `${w("문항", "Case")} ${row.row_number}` }, { id: "category", header: w("상태", "Status"), render: row => importCategories[row.category] ? w(...importCategories[row.category]) : "—" }]} />
          <Pagination total={entries.length} limit={10} offset={offset} onOffsetChange={setOffset} disabled={busy} label={w("미리보기 페이지", "Preview pages")} />
          <p className="v5-context">{w("충돌한 기존 기대 판정은 덮어쓰지 않고 확인 필요로 표시합니다. 기대 판정 없는 신규 문항도 확인 필요로 추가합니다.", "Conflicts do not overwrite existing answers. Conflicts and new cases without references need attention.")}</p>
          <button className="primary" disabled={busy || !preview.importable} onClick={confirm}>{phase === "confirm" ? w("저장 중…", "Saving…") : w("초안에 추가", "Add to Draft")}</button>
        </section>}
      </>}
      {error && <p ref={feedback} tabIndex={-1} className="error" role="alert">{error}</p>}
    </div>
  </Dialog>;
}
