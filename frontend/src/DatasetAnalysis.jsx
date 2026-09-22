import { useEffect, useRef, useState } from "react";
import { api } from "./api.js";
import { newTestRequestKey } from "./testRuns.js";
import { validationDataError } from "./validationData.js";
import { useWords } from "./LifecycleViews.jsx";

export default function DatasetAnalysis({ onCreated, disabledReason, candidateConfiguration, onBusy, agentMode, external, purpose, sourceTemplate }) {
  const w = useWords(); const [catalog, setCatalog] = useState(null), [dataset, setDataset] = useState(null), [working, setWorking] = useState(null);
  const [name, setName] = useState(""), [busy, setBusy] = useState(false), [error, setError] = useState("");
  const [query, setQuery] = useState(""), [offset, setOffset] = useState(0), [revisionId, setRevisionId] = useState("");
  const [localMode, setMode] = useState(agentMode === "stub" ? "reference" : "ground_truth"); const mode = purpose ? purpose === "official_evaluation" ? "ground_truth" : "reference" : localMode; const key = useRef(null), pending = useRef(false);
  useEffect(() => { if (!sourceTemplate?.source_dataset_id) return; const controller = new AbortController(); api.validationDataset(sourceTemplate.source_dataset_id, { signal: controller.signal }).then(value => { if (!controller.signal.aborted) setDataset(value); }).catch(() => { if (!controller.signal.aborted) setError(w("원본 데이터셋을 사용할 수 없습니다. 다른 데이터셋으로 바꾸지 않았습니다.", "The source dataset is unavailable. No replacement was selected.")); }); return () => controller.abort(); }, [sourceTemplate]);
  useEffect(() => { key.current = null; }, [candidateConfiguration, mode, revisionId, name]);
  useEffect(() => { const controller = new AbortController(); const timer = setTimeout(() => api.searchValidationDatasets({ query, limit: 20, offset }, { signal: controller.signal }).then(result => { if (!controller.signal.aborted) setCatalog(previous => ({ ...result, items: offset ? [...(previous?.items || []), ...result.items] : result.items })); }).catch(e => { if (!controller.signal.aborted) setError(validationDataError(e)); }), 200); return () => { clearTimeout(timer); controller.abort(); }; }, [offset, query]);
  useEffect(() => { setWorking(null); setRevisionId(""); if (!dataset) return; const controller = new AbortController(); api.workingDataset(dataset.id, {}, { signal: controller.signal }).then(result => { if (!controller.signal.aborted) { setWorking(result); setRevisionId(sourceTemplate?.source_dataset_id === dataset.id ? sourceTemplate.source_dataset_revision_id || "" : result.latest_published_revision_id || ""); } }).catch(e => { if (!controller.signal.aborted) setError(validationDataError(e)); }); return () => controller.abort(); }, [dataset]);
  const revision = working?.published_revisions.find(row => row.id === revisionId), official = mode === "ground_truth";
  const unavailable = busy || !working || Boolean(disabledReason) || (official && (!revision || !candidateConfiguration || agentMode !== "moduagent")) || (dataset?.internal_only && external);
  async function run(event) { event.preventDefault(); if (pending.current || unavailable) return; pending.current = true; setBusy(true); onBusy?.(true); setError(""); key.current ||= newTestRequestKey();
    try { const result = await api.runValidationDataset(dataset.id, { expected_revision: revision?.revision ?? dataset.revision,
      ...(revision ? { dataset_revision_id: revision.id } : {}), name: name.trim() || null, idempotency_key: key.current,
      evaluation_mode: mode, ...(candidateConfiguration ? { candidate_configuration: candidateConfiguration } : {}) }); onCreated(result); }
    catch (e) { setError(validationDataError(e)); } finally { pending.current = false; setBusy(false); onBusy?.(false); } }
  return <section className="panel"><div className="panel-head"><h2>{w("정답 데이터", "Ground Truth")}</h2></div><form className="data-form" onSubmit={run}>
    <label>{w("데이터셋 검색", "Find dataset")}<input value={query} disabled={busy} maxLength={120} placeholder={w("데이터셋 이름", "Dataset name")} onChange={e => { setQuery(e.target.value); setOffset(0); }} /></label>
    <div className="dataset-picker">{catalog?.items.map(row => <button type="button" className="secondary" key={row.id} disabled={busy} aria-pressed={dataset?.id === row.id} onClick={() => { key.current = null; setDataset(row); }}><strong>{row.name}</strong><small>{row.description}</small></button>)}</div>
    {catalog && catalog.items.length < catalog.total && <button type="button" className="text-button" onClick={() => setOffset(catalog.items.length)}>{w("더 보기", "Load more")}</button>}
    {working && <><label>{w("공식 버전", "Published Version")}<select value={revisionId} disabled={busy} onChange={e => setRevisionId(e.target.value)}><option value="">{w("공식 버전을 선택하세요", "Choose a Published Version")}</option>{working.published_revisions.map(row => <option key={row.id} value={row.id}>r{row.revision} · {row.total} {w("문항", "cases")}</option>)}</select></label>{!working.published_revisions.length && <p className="notice">{w("공식 테스트 전에 정답 데이터에서 공식 버전을 만드세요.", "Publish a Ground Truth version before an Official Test.")}</p>}</>}
    {!purpose && <label>{w("평가 방식", "Evaluation mode")}<select disabled={busy} value={mode} onChange={e => setMode(e.target.value)}><option value="ground_truth" disabled={agentMode === "stub"}>{w("공식 평가 · 공식 버전", "Official · Published Version")}</option><option value="reference">{w("참고 답안 비교 · 개발 테스트", "Reference comparison · unofficial")}</option></select></label>}
    {sourceTemplate?.source_dataset_revision_id && revisionId === sourceTemplate.source_dataset_revision_id && sourceTemplate.latest_published_dataset_revision_id !== revisionId && <p className="notice">{w("더 최신 공식 버전이 있지만 이전 테스트의 버전을 유지했습니다.", "A newer Published Version exists; the source Test version is retained.")}</p>}
    <p className="v5-context">{official ? w("선택한 공식 버전의 모든 문항을 평가합니다. 편집 중 데이터의 변경은 영향을 주지 않습니다.", "Evaluates all published cases. Draft changes do not affect this run.") : w("선택한 공식 버전 또는 이전 저장본을 실행합니다. 공식 평가나 운영 반영 근거로 사용하지 않습니다.", "Runs the selected Published Version, or legacy saved version. Not eligible for Production Review.")}</p>
    {revision && <p className="v5-context">{w("평가 문항", "Evaluation cases")}: {revision.total} · {w("발행 당시 포함 비율", "Inclusion rate at publication")}: {(revision.metadata.inclusion_rate * 100).toFixed(1)}% · {w("확인 필요 / 제외", "Needs attention / excluded")}: {revision.metadata.needs_attention_count} / {revision.metadata.excluded_count}</p>}
    {dataset?.internal_only && <p className="notice">{w("내부 전용 문항은 모든 역할에 내부 vLLM을 사용해야 합니다.", "Internal cases require vLLM for every agent role.")}</p>}
    <label>{w("테스트명", "Test name")}<input disabled={busy} value={name} maxLength={120} onChange={e => setName(e.target.value)} placeholder={w("비워두면 날짜와 시간으로 생성", "Defaults to the test date and time")} /></label>
    <p className="v5-context">{w("선택한 구성으로 모델을 호출합니다. 외부 모델은 API 비용이 발생할 수 있습니다.", "Calls models with the selected configuration. External models may incur API charges.")}</p>
    {error && <p className="error" role="alert">{error}</p>}<button className="primary" disabled={unavailable}>{busy ? w("접수 중…", "Submitting…") : w("테스트 시작", "Start test")}</button>
  </form></section>;
}
