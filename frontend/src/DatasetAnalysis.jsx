import { useEffect, useRef, useState } from "react";
import { api } from "./api.js";
import Pagination from "./Pagination.jsx";
import { newTestRequestKey } from "./testRuns.js";
import { validationDataError } from "./validationData.js";

export default function DatasetAnalysis({ onCreated, disabledReason }) {
  const [catalog, setCatalog] = useState(null), [selected, setSelected] = useState("");
  const [name, setName] = useState(""), [busy, setBusy] = useState(false), [error, setError] = useState("");
  const [reload, setReload] = useState(0); const key = useRef(null);
  const [offset, setOffset] = useState(0);
  useEffect(() => { const controller = new AbortController(); setCatalog(null); setError(""); api.validationDatasets({ limit: 200, offset }, { signal: controller.signal }).then(result => { if (!controller.signal.aborted) setCatalog(result); }).catch(err => { if (!controller.signal.aborted) setError(validationDataError(err)); }); return () => controller.abort(); }, [reload, offset]);
  const dataset = catalog?.items.find(item => item.id === selected);
  async function run(event) {
    event.preventDefault(); if (!dataset || busy || disabledReason) return;
    setBusy(true); setError(""); key.current ||= newTestRequestKey();
    try { const result = await api.runValidationDataset(dataset.id, { expected_revision: dataset.revision, name: name.trim() || null, idempotency_key: key.current }); onCreated(result); }
    catch (err) { setError(validationDataError(err)); } finally { setBusy(false); }
  }
  return <section className="panel"><div className="panel-head"><h2>검증 데이터셋 분석</h2><button type="button" className="secondary" disabled={busy} onClick={() => { key.current = null; setReload(value => value + 1); }}>목록 새로고침</button></div><form className="data-form" onSubmit={run}>
    <label>데이터셋<select aria-label="데이터셋" required disabled={busy || !catalog} value={selected} onChange={event => { key.current = null; setSelected(event.target.value); }}><option value="">선택하세요</option>{catalog?.items.map(item => <option key={item.id} value={item.id}>{item.name} · {item.total}문항</option>)}</select></label>
    {catalog && (offset > 0 || catalog.total > 200) && <Pagination label="분석할 데이터셋 페이지" total={catalog.total} limit={200} offset={offset} disabled={busy} unit="개" onOffsetChange={value => { setSelected(""); key.current = null; setOffset(value); }} />}
    {dataset && <p className="ux-muted">버전 {dataset.revision} · {dataset.total}문항 · 참고 답안 {dataset.labeled}건 · 답안 없는 문항도 분석하며 평가에서만 제외합니다.</p>}
    {dataset?.internal_only && <p className="notice">운영에서 가져온 문항이 있습니다. Primary·Verifier·근거 정리 모델을 모두 내부 vLLM으로 설정해야 합니다.</p>}
    <label>테스트명<input value={name} disabled={busy} onChange={event => { key.current = null; setName(event.target.value); }} maxLength={120} placeholder="비워두면 자동 생성" /></label>
    <p className="ux-muted">현재 Test 모델로 판정을 실행합니다. OpenAI 모델을 사용하면 호출 비용이 발생할 수 있습니다.</p>
    {error && <p className="error" role="alert">{error}</p>}<button type="submit" className="primary" disabled={busy || !dataset?.total || Boolean(disabledReason)}>{busy ? "접수 중…" : "분석 시작"}</button>
  </form></section>;
}
