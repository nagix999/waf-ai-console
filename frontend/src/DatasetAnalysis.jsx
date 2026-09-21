import { useEffect, useRef, useState } from "react";
import { api } from "./api.js";
import { Icon } from "./Icon.jsx";
import { newTestRequestKey } from "./testRuns.js";
import { validationDataError } from "./validationData.js";

export default function DatasetAnalysis({ onCreated, disabledReason, candidateConfiguration, onBusy, agentMode, external }) {
  const [catalog, setCatalog] = useState(null), [dataset, setDataset] = useState(null), [loading, setLoading] = useState(true);
  const [name, setName] = useState(""), [busy, setBusy] = useState(false), [error, setError] = useState("");
  const [reload, setReload] = useState(0), [query, setQuery] = useState(""), [offset, setOffset] = useState(0);
  const [mode, setMode] = useState(agentMode === "stub" ? "reference" : "ground_truth"); const key = useRef(null);
  useEffect(() => { key.current = null; }, [candidateConfiguration, mode]);
  useEffect(() => { if (agentMode === "stub") setMode("reference"); }, [agentMode]);
  useEffect(() => {
    const controller = new AbortController(); setLoading(true); setError("");
    const timer = setTimeout(() => api.searchValidationDatasets({ query, limit: 20, offset }, { signal: controller.signal }).then(result => {
      if (!controller.signal.aborted) { setCatalog(previous => ({ ...result, items: offset ? [...(previous?.items || []), ...result.items] : result.items })); setLoading(false); }
    }).catch(err => { if (!controller.signal.aborted) { setError(validationDataError(err)); setLoading(false); } }), 200);
    return () => { clearTimeout(timer); controller.abort(); };
  }, [reload, offset, query]);
  const official = mode === "ground_truth", approved = dataset?.review_counts?.approved || 0;
  const unavailable = busy || Boolean(disabledReason) || !dataset || (official ? !approved || agentMode === "stub" : !dataset.total) || (dataset.internal_only && external);
  async function run(event) {
    event.preventDefault(); if (unavailable) return;
    setBusy(true); onBusy?.(true); setError(""); key.current ||= newTestRequestKey();
    try { const result = await api.runValidationDataset(dataset.id, { expected_revision: dataset.revision, name: name.trim() || null, idempotency_key: key.current, evaluation_mode: mode, ...(candidateConfiguration ? { candidate_configuration: candidateConfiguration } : {}) }); onCreated(result); }
    catch (err) { setError(validationDataError(err)); } finally { setBusy(false); onBusy?.(false); }
  }
  return <section className="panel dataset-run-workspace"><div className="panel-head"><h2>검증 데이터셋 분석</h2><button type="button" className="secondary" disabled={busy || loading} onClick={() => { key.current = null; setDataset(null); setOffset(0); setReload(value => value + 1); }}>목록 새로고침</button></div><div className="dataset-run-grid"><form className="data-form" onSubmit={run}>
    <label>데이터셋 검색<input aria-label="데이터셋 검색" value={query} disabled={busy} maxLength={120} placeholder="데이터셋명으로 찾기" onKeyDown={event => { if (event.key === "Enter") event.preventDefault(); }} onChange={event => { setQuery(event.target.value); setOffset(0); setCatalog(null); }} /></label>
    <div className="dataset-picker" aria-label="데이터셋 선택">{catalog?.items.map(item => <button type="button" className="secondary" key={item.id} disabled={busy || loading} aria-pressed={dataset?.id === item.id} onClick={() => { key.current = null; setDataset(item); }}><strong>{item.name}</strong><small>버전 {item.revision} · 전체 {item.total}문항 · 승인 {item.review_counts?.approved || 0}문항</small></button>)}</div>
    {loading ? <p role="status">목록을 불러오는 중…</p> : catalog?.items.length === 0 && <p className="ux-muted">검색한 데이터셋이 없습니다.</p>}
    {catalog && catalog.items.length < catalog.total && <button type="button" className="text-button" disabled={busy || loading} onClick={() => setOffset(catalog.items.length)}>더 보기 ({catalog.items.length}/{catalog.total})</button>}
    {dataset && <p>선택: <strong>{dataset.name}</strong> · 버전 {dataset.revision}</p>}
    <label>평가 방식<select value={mode} disabled={busy} onChange={event => setMode(event.target.value)}><option value="ground_truth" disabled={agentMode === "stub"}>공식 평가 · 승인된 답안만</option><option value="reference">일반 분석 · 참고 답안 비교</option></select></label>
    {official ? <p className="ux-muted">승인된 {approved}문항만 실행합니다. 미검토·검토 완료 {dataset ? dataset.total - approved : 0}문항은 실행과 평가에서 제외합니다. 접수 당시 답안을 고정하고 완료 후 평가 기록을 자동 저장합니다.</p> : <p className="ux-muted">전체 문항을 분석하고 답안이 있는 문항만 비교합니다. 공식 평가로 인정하지 않습니다.</p>}
    {dataset?.internal_only && <p className="notice">내부 전용 문항이 있습니다. 모든 모델을 내부 vLLM으로 선택하세요.</p>}
    <label>테스트명<input value={name} disabled={busy} onChange={event => { key.current = null; setName(event.target.value); }} maxLength={120} placeholder="비워두면 자동 생성" /></label>
    <p className="ux-muted">위 실행 구성으로 판정을 실행합니다. 모델 호출 비용이 발생할 수 있습니다.</p>
    {error && <p className="error" role="alert">{error}</p>}<button type="submit" className="primary" disabled={unavailable}>{busy ? "접수 중…" : official ? "공식 평가 시작" : "분석 시작"}</button>
  </form><aside className="dataset-run-preview"><h3>선택한 Ground Truth 데이터셋</h3>{dataset ? <>
    <div className="dataset-run-highlight"><span className="dataset-card-icon"><Icon name="database" size={30} /></span><div><strong>{dataset.name}</strong>{dataset.description && <p>{dataset.description}</p>}</div></div>
    <dl><div><dt>버전</dt><dd>{dataset.revision}</dd></div><div><dt>승인 문항</dt><dd>{dataset.review_counts?.approved ?? "미기록"} / {dataset.total}문항</dd></div><div><dt>검토 완료</dt><dd>{dataset.review_counts?.reviewed ?? "미기록"}</dd></div><div><dt>미검토</dt><dd>{dataset.review_counts?.draft ?? "미기록"}</dd></div></dl>
    <div className="dataset-evaluation-note"><Icon name="shield" size={20} /><p>{official ? "공식 평가는 이 버전에서 승인된 문항만 사용합니다. 답안과 실행 구성을 고정해 평가 기록을 남깁니다." : "일반 분석은 전체 문항을 사용합니다. 참고 답안 비교는 공식 평가와 구분해 기록합니다."}</p></div>
  </> : <div className="dataset-preview-empty"><Icon name="database" size={36} /><p>왼쪽에서 데이터셋을 선택하세요.</p><span>저장된 버전과 검토 상태를 확인할 수 있습니다.</span></div>}</aside></div></section>;
}
