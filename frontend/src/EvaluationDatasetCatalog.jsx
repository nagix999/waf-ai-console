import { useEffect, useState } from "react";
import { api } from "./api.js";
import DataTable from "./DataTable.jsx";
import Pagination from "./Pagination.jsx";
import HelpTooltip from "./HelpTooltip.jsx";
import { useWords } from "./LifecycleViews.jsx";
import { draftCount, productionDatasetRevision } from "./evaluationDatasets.js";
import "./evaluationDatasets.css";

export function ProductionEvaluationBadge({ datasetId, groundTruth }) {
  const w = useWords(), revision = productionDatasetRevision(datasetId, groundTruth);
  if (revision === null) return null;
  return <span className="dataset-production-use"><span className="dataset-production-badge">{w("현재 운영 평가", "Current Production Evaluation")} · r{revision}</span><HelpTooltip label={w("현재 운영 평가", "Current Production Evaluation")}>{w("현재 운영 설정과 동일한 구성으로 수행한 최신 공식 평가에 사용된 버전입니다.", "The version used in the latest official evaluation of the current Production configuration.")}</HelpTooltip></span>;
}

export default function EvaluationDatasetCatalog({ state, onChange, onSelect, onCreate, groundTruth, revision, disabled }) {
  const w = useWords();
  const [reload, setReload] = useState(0);
  const [read, setRead] = useState({ key: "", data: null, error: false });
  const key = JSON.stringify([state, revision, reload]);
  const { data, error } = read.key === key ? read : { data: null, error: false };
  useEffect(() => {
    const controller = new AbortController();
    const timer = setTimeout(() => api.searchValidationDatasets(state, { signal: controller.signal })
      .then(value => { if (!controller.signal.aborted) setRead({ key, data: value, error: false }); })
      .catch(() => { if (!controller.signal.aborted) setRead({ key, data: null, error: true }); }), 180);
    return () => { clearTimeout(timer); controller.abort(); };
  }, [key]);
  const columns = [
    { id: "name", header: w("평가 데이터셋", "Evaluation Dataset"), width: "32%", render: row => <><button type="button" className="text-button dataset-open" disabled={disabled} onClick={() => onSelect(row.id)}>{row.name}</button>{row.description && <div className="dataset-description">{row.description.length > 85 ? <HelpTooltip label={w("설명", "Description")} previewText={`${row.description.slice(0, 85)}…`}>{row.description}</HelpTooltip> : row.description}</div>}</> },
    { id: "draft", header: w("초안", "Draft"), render: row => <><strong>{draftCount(row) ?? "—"} {w("문항", "cases")}</strong>{row.needs_attention_count > 0 && <small className="dataset-needs-attention">{w("확인 필요", "Needs attention")} {row.needs_attention_count}</small>}</> },
    { id: "published", header: w("최근 공식 버전", "Latest Published Version"), render: row => row.latest_published_revision ? <span>r{row.latest_published_revision.revision} · {row.published_case_count} {w("문항", "cases")}</span> : <span className="ux-muted">{w("미발행", "Not published")}</span> },
    { id: "usage", header: w("평가 사용", "Evaluation use"), render: row => productionDatasetRevision(row.id, groundTruth) !== null ? <ProductionEvaluationBadge datasetId={row.id} groundTruth={groundTruth} /> : "—" },
    { id: "changes", header: w("변경", "Changes"), className: "dataset-change-count", render: row => row.working_change_count ?? "—" },
  ];
  return <section className="panel evaluation-dataset-catalog" aria-busy={!data && !error}>
    <div className="dataset-catalog-toolbar"><label>{w("평가 데이터셋 검색", "Find evaluation datasets")}<input maxLength={120} value={state.query} disabled={disabled} placeholder={w("이름으로 검색", "Search by name")} onChange={event => onChange({ ...state, query: event.target.value, offset: 0 })} /></label><button type="button" className="primary" disabled={disabled} onClick={onCreate}>+ {w("평가 데이터셋 만들기", "Create Evaluation Dataset")}</button></div>
    <p className="v5-context">{w("요청 입력과 기대 판정을 함께 관리하는 문항 모음입니다. 초안을 편집한 뒤 공식 버전으로 평가 기준을 고정합니다.", "Manage request inputs and expected verdicts. Edit the draft, then publish a version to freeze the evaluation criteria.")}</p>
    {error ? <p className="error" role="alert">{w("평가 데이터셋 목록을 불러오지 못했습니다.", "Could not load evaluation datasets.")} <button type="button" className="text-button" onClick={() => setReload(value => value + 1)}>{w("다시 조회", "Retry")}</button></p> : !data ? <p role="status">{w("목록을 불러오는 중…", "Loading datasets…")}</p> : <>
      <DataTable label={w("평가 데이터셋 목록", "Evaluation Dataset list")} className="evaluation-dataset-table" data={data.items} columns={columns} onRowClick={disabled ? undefined : row => onSelect(row.id)} rowClassName={row => productionDatasetRevision(row.id, groundTruth) !== null ? "dataset-current-evaluation" : ""} empty={state.query ? w("검색 결과가 없습니다.", "No matching datasets.") : w("평가 데이터셋을 만들어 문항을 추가하세요. 테스트 문항을 복사해 시작할 수도 있습니다.", "Create a dataset and add cases, or copy cases from a Test.")} />
      <Pagination total={data.total} limit={state.limit} offset={state.offset} onOffsetChange={offset => onChange({ ...state, offset })} disabled={disabled} unit={w("개", "datasets")} label={w("평가 데이터셋 페이지", "Evaluation Dataset pages")} />
    </>}
  </section>;
}
