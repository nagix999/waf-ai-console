import { useEffect, useRef, useState } from "react";
import { api } from "./api.js";
import { newTestRequestKey } from "./testRuns.js";
import { validationDataError } from "./validationData.js";
import { useWords } from "./LifecycleViews.jsx";

export default function DatasetAnalysis({ onCreated, disabledReason, candidateConfiguration, onBusy, agentMode, external }) {
  const w = useWords(); const [catalog, setCatalog] = useState(null), [dataset, setDataset] = useState(null), [working, setWorking] = useState(null);
  const [name, setName] = useState(""), [busy, setBusy] = useState(false), [error, setError] = useState("");
  const [query, setQuery] = useState(""), [offset, setOffset] = useState(0), [revisionId, setRevisionId] = useState("");
  const [mode, setMode] = useState(agentMode === "stub" ? "reference" : "ground_truth"); const key = useRef(null), pending = useRef(false);
  useEffect(() => { key.current = null; }, [candidateConfiguration, mode, revisionId, name]);
  useEffect(() => { const controller = new AbortController(); const timer = setTimeout(() => api.searchValidationDatasets({ query, limit: 20, offset }, { signal: controller.signal }).then(result => { if (!controller.signal.aborted) setCatalog(previous => ({ ...result, items: offset ? [...(previous?.items || []), ...result.items] : result.items })); }).catch(e => { if (!controller.signal.aborted) setError(validationDataError(e)); }), 200); return () => { clearTimeout(timer); controller.abort(); }; }, [offset, query]);
  useEffect(() => { setWorking(null); setRevisionId(""); if (!dataset) return; const controller = new AbortController(); api.workingDataset(dataset.id, {}, { signal: controller.signal }).then(result => { if (!controller.signal.aborted) { setWorking(result); setRevisionId(result.latest_published_revision_id || ""); } }).catch(e => { if (!controller.signal.aborted) setError(validationDataError(e)); }); return () => controller.abort(); }, [dataset]);
  const revision = working?.published_revisions.find(row => row.id === revisionId), official = mode === "ground_truth";
  const unavailable = busy || !working || Boolean(disabledReason) || (official && (!revision || !candidateConfiguration || agentMode !== "moduagent")) || (dataset?.internal_only && external);
  async function run(event) { event.preventDefault(); if (pending.current || unavailable) return; pending.current = true; setBusy(true); onBusy?.(true); setError(""); key.current ||= newTestRequestKey();
    try { const result = await api.runValidationDataset(dataset.id, { expected_revision: revision?.revision ?? dataset.revision,
      ...(revision ? { dataset_revision_id: revision.id } : {}), name: name.trim() || null, idempotency_key: key.current,
      evaluation_mode: mode, ...(candidateConfiguration ? { candidate_configuration: candidateConfiguration } : {}) }); onCreated(result); }
    catch (e) { setError(validationDataError(e)); } finally { pending.current = false; setBusy(false); onBusy?.(false); } }
  return <section className="panel"><div className="panel-head"><h2>{w("검증 데이터셋 분석", "Ground Truth test")}</h2></div><form className="data-form" onSubmit={run}>
    <label>{w("데이터셋 검색", "Find dataset")}<input value={query} disabled={busy} maxLength={120} placeholder={w("데이터셋 이름", "Dataset name")} onChange={e => { setQuery(e.target.value); setOffset(0); }} /></label>
    <div className="dataset-picker">{catalog?.items.map(row => <button type="button" className="secondary" key={row.id} disabled={busy} aria-pressed={dataset?.id === row.id} onClick={() => { key.current = null; setDataset(row); }}><strong>{row.name}</strong><small>{row.description}</small></button>)}</div>
    {catalog && catalog.items.length < catalog.total && <button type="button" className="text-button" onClick={() => setOffset(catalog.items.length)}>{w("더 보기", "Load more")}</button>}
    {working && <><label>{w("발행 리비전", "Published revision")}<select value={revisionId} disabled={busy} onChange={e => setRevisionId(e.target.value)}><option value="">{w("발행 리비전을 선택하세요", "Choose a published revision")}</option>{working.published_revisions.map(row => <option key={row.id} value={row.id}>r{row.revision} · {row.total} {w("문항", "cases")}</option>)}</select></label>{!working.published_revisions.length && <p className="notice">{w("공식 평가 전에 Ground Truth에서 리비전을 발행하세요.", "Publish a revision in Ground Truth before an official evaluation.")}</p>}</>}
    <label>{w("평가 방식", "Evaluation mode")}<select disabled={busy} value={mode} onChange={e => setMode(e.target.value)}><option value="ground_truth" disabled={agentMode === "stub"}>{w("공식 평가 · 발행 리비전", "Official · published revision")}</option><option value="reference">{w("참고 답안 비교 · 비공식", "Reference comparison · unofficial")}</option></select></label>
    <p className="v5-context">{official ? w("선택한 리비전의 모든 문항을 평가합니다. 작업 초안의 변경은 영향을 주지 않습니다.", "Evaluates all published cases. Working Draft changes do not affect this run.") : w("선택한 발행 리비전 또는 이전 저장본을 실행합니다. 공식 평가나 승격 근거로 사용하지 않습니다.", "Runs the selected published revision, or legacy saved version. Not official promotion evidence.")}</p>
    {revision && <p className="v5-context">{w("평가 문항", "Evaluation cases")}: {revision.total} · {w("발행 당시 포함 비율", "Inclusion rate at publication")}: {(revision.metadata.inclusion_rate * 100).toFixed(1)}% · {w("확인 필요 / 제외", "Needs attention / excluded")}: {revision.metadata.needs_attention_count} / {revision.metadata.excluded_count}</p>}
    {dataset?.internal_only && <p className="notice">{w("내부 전용 문항은 모든 역할에 내부 vLLM을 사용해야 합니다.", "Internal cases require vLLM for every agent role.")}</p>}
    <label>{w("테스트명", "Test name")}<input disabled={busy} value={name} maxLength={120} onChange={e => setName(e.target.value)} placeholder={w("비워두면 날짜와 시간으로 생성", "Defaults to the test date and time")} /></label>
    <p className="v5-context">{w("선택한 구성으로 모델을 호출합니다. 외부 모델은 API 비용이 발생할 수 있습니다.", "Calls models with the selected configuration. External models may incur API charges.")}</p>
    {error && <p className="error" role="alert">{error}</p>}<button className="primary" disabled={unavailable}>{busy ? w("접수 중…", "Submitting…") : w("테스트 시작", "Start test")}</button>
  </form></section>;
}
