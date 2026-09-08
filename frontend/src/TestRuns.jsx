import { useEffect, useMemo, useRef, useState } from "react";
import { api } from "./api.js";
import { analysisRowState, formatDate, formatDuration } from "./analysisView.js";
import { CompactReferenceComparison, EvaluationSummary } from "./ReferenceLabels.jsx";
import { evaluationOutcomes, referenceVerdicts } from "./labelEvaluation.js";
import { metricText, matrixCells } from "./evaluationMetrics.js";
import { runKinds, runStatuses, testRunQuery, testScopeSelection } from "./testRuns.js";
import { MetricHelp } from "./EvaluationMetrics.jsx";
import TestRunComparison from "./TestRunComparison.jsx";
import { initialTestComparisonState } from "./testComparison.js";
import "./testRuns.css";
import Dialog from "./Dialog.jsx";
import EvaluationOverview from "./EvaluationOverview.jsx";

export const initialTestRunHistoryState = () => ({ queryText: "", query: { q: "", limit: 10, offset: 0 } });
export const initialTestRunFilters = () => ({ difficulty: "", test_category: "", difficulty_missing: false, test_category_missing: false, status: "", evaluation_outcome: "", cell: "", limit: 25, offset: 0, comparison: initialTestComparisonState() });

export function testRunHistoryChange(state, action) {
  if (action.type === "draft") return { ...state, queryText: action.value };
  if (action.type === "search") return { ...state, query: { ...state.query, q: state.queryText.trim(), offset: 0 } };
  if (action.type === "clear") return { ...state, queryText: "", query: { ...state.query, q: "", offset: 0 } };
  if (action.type === "page") return { ...state, query: { ...state.query, offset: Math.max(0, state.query.offset + action.direction * state.query.limit) } };
  return state;
}

export function testRunFilterChange(filters, action) {
  if (action.type === "scope") return { ...filters, ...testScopeSelection(action.name, action.value), cell: "", status: "", evaluation_outcome: "", offset: 0 };
  if (action.type === "row" && ["status", "evaluation_outcome"].includes(action.name)) return { ...filters, [action.name]: action.value, cell: "", offset: 0 };
  if (action.type === "cell") return { ...filters, cell: filters.cell === action.value ? "" : action.value, status: "", evaluation_outcome: "", offset: 0 };
  if (action.type === "clear_rows") return { ...filters, status: "", evaluation_outcome: "", cell: "", offset: 0 };
  if (action.type === "first_page") return { ...filters, offset: 0 };
  if (action.type === "clear_cell") return { ...filters, cell: "", offset: 0 };
  if (action.type === "limit") return { ...filters, limit: action.value, offset: 0 };
  if (action.type === "page") return { ...filters, offset: Math.max(0, filters.offset + action.direction * filters.limit) };
  return filters;
}

// Each read belongs to one mounted query. Cancellation invalidates even a late
// response from a transport that does not honor AbortSignal.
export function watchTestRunRead({ read, onUpdate, isRunning, errorMessage, onUnauthorized, setTimer = setTimeout, clearTimer = clearTimeout }) {
  let active = true, timer;
  const controller = new AbortController();
  async function load() {
    try {
      const data = await read({ signal: controller.signal });
      if (!active) return;
      onUpdate({ data, error: "", loading: false });
      if (isRunning(data)) timer = setTimer(load, 5000);
    } catch (error) {
      if (!active) return;
      onUpdate({ error: error?.status === 401 ? "로그인 세션이 만료되었습니다. 다시 로그인하세요." : errorMessage, loading: false });
      if (error?.status === 401) onUnauthorized?.();
    }
  }
  void load();
  return () => { active = false; clearTimer(timer); controller.abort(); };
}

const emptyRead = () => ({ key: null, data: null, error: "", loading: true });

export function TestRunRows({ items, onSelect }) {
  return <div className="table-wrap"><table className="test-run-table"><thead><tr><th>테스트명 / 접수 시각</th><th>모델</th><th>진행 상태</th><th>Accuracy<MetricHelp metric="accuracy" label="Accuracy" /> / 커버리지<MetricHelp metric="coverage" label="커버리지" /></th><th>전체 소요 시간</th></tr></thead><tbody>{items.map(run => <tr key={run.id} className={analysisRowState(run.status).className}><td><button type="button" className="text-button" onClick={() => onSelect(run.id)}>{run.name}</button><small>{runKinds[run.kind] || "유형 미확인"} · {formatDate(run.created_at)}</small></td><td>{run.execution_mode === "stub" ? "모의 실행 · 품질 평가 제외" : run.profile_metadata?.model_name || "실행 모델 정보 없음"}</td><td><span>{runStatuses[run.status] || "상태 미확인"}</span><small>완료 {run.completed} · 실패 {run.failed} · 거부 {run.rejected}</small><small>접수 {run.accepted} / 전체 {run.total}건</small></td><td>{metricText(run.evaluation_summary?.metrics?.accuracy)} / {metricText(run.evaluation_summary?.metrics?.coverage)}<small>{Number.isFinite(run.evaluation_summary?.binary_decided) ? `확정 판정 ${run.evaluation_summary.binary_decided}건 기준` : "평가 정보 미기록"}</small></td><td>{formatDuration(run.total_elapsed_ms, ["pending", "processing"].includes(run.status) ? "진행 중" : "측정 정보 없음")}</td></tr>)}</tbody></table></div>;
}

export function TestRunScope({ data, filters, onChange }) {
  return <section className="panel test-run-scope"><h3>테스트 범위</h3><p className="ux-muted">접수 당시 난이도·유형으로 지표와 문항 목록을 함께 좁힙니다.</p><div className="filter-grid">
    <label>난이도<select name="difficulty" value={filters.difficulty_missing ? "missing:" : filters.difficulty ? `value:${filters.difficulty}` : ""} onChange={onChange}><option value="">전체</option><option value="missing:">미분류{Number.isInteger(data.missing_difficulty_count) ? ` (${data.missing_difficulty_count}건)` : ""}</option>{data.facets.difficulties.map(value => <option key={value} value={`value:${value}`}>{value}</option>)}</select></label>
    <label>테스트 유형<select name="test_category" value={filters.test_category_missing ? "missing:" : filters.test_category ? `value:${filters.test_category}` : ""} onChange={onChange}><option value="">전체</option><option value="missing:">미분류{Number.isInteger(data.missing_test_category_count) ? ` (${data.missing_test_category_count}건)` : ""}</option>{data.facets.test_categories.map(value => <option key={value} value={`value:${value}`}>{value}</option>)}</select></label>
  </div></section>;
}

export function TestRunHistoryEmpty({ query, onClear, onViewAnalyses }) {
  return <div className="test-run-empty"><p>{query.q ? "검색한 이름에 해당하는 테스트가 없습니다." : query.offset ? "이 페이지에 표시할 테스트가 없습니다." : "아직 접수한 테스트가 없습니다."}</p>{query.q && <button type="button" className="secondary small" onClick={onClear}>검색 초기화</button>}<p className="muted">이전 개별 분석은 별도 목록에서 확인하세요.</p>{onViewAnalyses && <button type="button" className="text-button" onClick={onViewAnalyses}>개별 테스트 분석 보기 →</button>}</div>;
}

export function TestRunHistory({ refresh = 0, onSelect, state, onStateChange, onViewAnalyses, onUnauthorized, title = "테스트 목록", description = "테스트명을 선택하면 해당 실행의 분석 결과와 평가 지표를 함께 확인합니다." }) {
  const [localState, setLocalState] = useState(initialTestRunHistoryState);
  const view = state ?? localState;
  const setView = state == null ? setLocalState : onStateChange;
  const change = action => setView?.(current => testRunHistoryChange(current, action));
  const { queryText, query } = view;
  const [read, setRead] = useState(emptyRead); const [reload, setReload] = useState(0);
  const unauthorized = useRef(onUnauthorized); unauthorized.current = onUnauthorized;
  const requestKey = JSON.stringify([query.q, query.limit, query.offset, refresh, reload]);
  const { data, error, loading } = read.key === requestKey ? read : emptyRead();
  useEffect(() => {
    setRead({ ...emptyRead(), key: requestKey });
    return watchTestRunRead({ read: async options => { const result = await api.testRuns(query, options); if (!Array.isArray(result?.items)) throw new Error("invalid_test_runs"); return result; }, onUpdate: patch => setRead(current => ({ ...current, ...patch, key: requestKey })), isRunning: result => result.items.some(run => ["pending", "processing"].includes(run.status)), errorMessage: "테스트 목록을 조회하지 못했습니다. 다시 조회하세요.", onUnauthorized: () => unauthorized.current?.() });
  }, [requestKey]);
  return <section className="panel test-run-history" aria-busy={loading}><div className="panel-head"><div><h2>{title}</h2><small>{description}</small></div><button type="button" className="secondary" disabled={loading} onClick={() => setReload(v => v + 1)}>새로고침</button></div>
    <form className="test-run-search" onSubmit={event => { event.preventDefault(); change({ type: "search" }); }}><label>테스트명 검색<input value={queryText} maxLength={120} onChange={event => change({ type: "draft", value: event.target.value })} placeholder="예: SQLi 회귀 검증 1차" /></label><button type="submit" className="secondary" disabled={loading}>검색</button></form>
    {query.q && <p className="test-run-applied">적용된 테스트명: <strong>{query.q}</strong> <button type="button" className="text-button" onClick={() => change({ type: "clear" })}>검색 초기화</button></p>}
    {error && <p className="error" role="alert">{error}</p>}{!data && !error && <p role="status">테스트 목록을 불러오는 중…</p>}
    {data && <>{data.items.length ? <TestRunRows items={data.items} onSelect={onSelect} /> : <TestRunHistoryEmpty query={query} onClear={() => change({ type: "clear" })} onViewAnalyses={onViewAnalyses} />}<div className="pagination"><span>{data.total}개 테스트</span><button type="button" className="secondary" disabled={!query.offset} onClick={() => change({ type: "page", direction: -1 })}>이전</button><button type="button" className="secondary" disabled={query.offset + query.limit >= data.total} onClick={() => change({ type: "page", direction: 1 })}>다음</button></div></>}
  </section>;
}

export function TestRunItemError({ item }) {
  const [open, setOpen] = useState(false);
  return <><button type="button" className="text-button" onClick={() => setOpen(true)}>오류 정보</button><Dialog open={open} title="문항 오류 정보" onClose={() => setOpen(false)}><dl className="label-metadata"><dt>문항</dt><dd>{item.case_name || item.event_id || `${item.row_number}행`}</dd><dt>이벤트 식별자</dt><dd>{item.event_id || "미기록"}</dd><dt>오류 코드</dt><dd><code>{item.error_code}</code></dd></dl><p>접수 거부·실행 실패는 정답 불일치와 구분하며 품질 지표에 오답으로 포함하지 않습니다.</p></Dialog></>;
}

export function TestRunItemRows({ items, onOpen }) {
  return <div className="table-wrap"><table className="test-run-items"><thead><tr><th>문항 / 분류</th><th>판정 / 분석 요약</th><th>참고 답안 비교</th><th>처리 상태</th></tr></thead><tbody>{items.map(item => <tr key={item.id} className={analysisRowState(item.status).className}><td>{item.analysis_id ? <button type="button" className="text-button" onClick={() => onOpen(item.analysis_id)}>{item.case_name || item.event_id || `${item.row_number}행`}</button> : <strong>{item.case_name || item.event_id || `${item.row_number}행`}</strong>}<small>{item.row_number}행 · {item.difficulty || "난이도 미분류"} · {item.test_category || "유형 미분류"}</small></td><td><strong>{item.status === "completed" ? referenceVerdicts[item.verdict] || "판정 정보 없음" : "—"}</strong><small>{item.summary_ko || ""}</small></td><td>{item.ingest_status === "rejected" ? "접수 거부 · 평가 제외" : <CompactReferenceComparison evaluation={item.evaluation} />}</td><td>{item.ingest_status === "rejected" ? "접수 거부" : runStatuses[item.status] || "상태 미확인"}{item.error_code && <TestRunItemError item={item} />}</td></tr>)}</tbody></table></div>;
}

export function TestRunDetail({ id, onBack, onOpen, filters: controlledFilters, onFiltersChange, backLabel = "테스트 목록", onUnauthorized }) {
  const [metadataOpen, setMetadataOpen] = useState(false);
  const [localFilters, setLocalFilters] = useState(initialTestRunFilters);
  const filters = controlledFilters ?? localFilters;
  const setFilters = controlledFilters == null ? setLocalFilters : onFiltersChange;
  const comparisonOpen = Boolean(filters.comparison?.open);
  const setComparisonOpen = value => setFilters?.(current => ({ ...current, comparison: { ...(current.comparison || initialTestComparisonState()), open: value } }));
  const change = action => setFilters?.(current => testRunFilterChange(current, action));
  const [read, setRead] = useState(emptyRead); const [reload, setReload] = useState(0);
  const unauthorized = useRef(onUnauthorized); unauthorized.current = onUnauthorized;
  const query = useMemo(() => testRunQuery(filters), [filters]);
  const requestKey = JSON.stringify([id, query, reload]);
  const { data, error, loading } = read.key === requestKey ? read : emptyRead();
  useEffect(() => {
    setRead({ ...emptyRead(), key: requestKey });
    return watchTestRunRead({ read: async options => { const result = await api.testRun(id, query, options); if (result?.id !== id || !Array.isArray(result.items)) throw new Error("invalid_test_run"); return result; }, onUpdate: patch => setRead(current => ({ ...current, ...patch, key: requestKey })), isRunning: result => ["pending", "processing"].includes(result.status), errorMessage: "테스트 실행 정보를 조회하지 못했습니다. 다시 조회하세요.", onUnauthorized: () => unauthorized.current?.() });
  }, [requestKey]);
  const scopeChange = event => { const { name, value } = event.target; change({ type: "scope", name, value }); };
  const rowChange = event => { const { name, value } = event.target; change({ type: "row", name, value }); };
  return <div className="page-stack test-run-detail" aria-busy={loading}><div className="panel-head-inline"><button type="button" className="back" onClick={onBack}>← {backLabel}</button><button type="button" className="secondary" disabled={loading} onClick={() => setReload(v => v + 1)}>새로고침</button></div>{error && <p className="error" role="alert">{error}</p>}{!data && !error && <p role="status">실행 정보를 불러오는 중…</p>}{data && <>
    <section className="panel test-run-header"><div className="panel-head"><div><h2>{data.name}</h2><small>{runKinds[data.kind]} · {formatDate(data.created_at)}</small></div><span className={`status status-${data.status}`}>{runStatuses[data.status]}</span></div><dl><div><dt>접수 / 거부</dt><dd>{data.accepted} / {data.rejected}건</dd></div><div><dt>진행 / 완료 / 실패</dt><dd>{data.pending + data.processing} / {data.completed} / {data.failed}건</dd></div><div><dt>전체 소요 시간</dt><dd>{formatDuration(data.total_elapsed_ms, ["pending", "processing"].includes(data.status) ? "진행 중" : "측정 정보 없음")}</dd></div></dl><div className="ux-toolbar"><button type="button" className="text-button" onClick={() => setMetadataOpen(true)}>실행 정보</button></div>{["pending", "processing"].includes(data.status) && <p className="notice">분석 진행 중 · 완료된 문항에 따라 지표가 달라집니다.</p>}{data.execution_mode === "stub" && <p className="notice">모의 실행 · 실제 모델의 품질 평가에서 제외됩니다.</p>}</section>
    <Dialog open={metadataOpen} title="테스트 실행 정보" onClose={() => setMetadataOpen(false)}><p className="reference-inline-note">접수 당시 답안·모델·지침을 기준으로 평가합니다. 사후 답안 연결과 실패 재실행은 이 테스트의 기존 지표를 바꾸지 않습니다.</p><dl className="label-metadata"><dt>모델</dt><dd>{data.profile_metadata?.model_name || "미기록"}</dd><dt>지침</dt><dd>{data.prompt_version || "미기록"}</dd><dt>연동 시스템 · 답안 연결용</dt><dd><code>{data.source_system}</code></dd><dt>테스트 ID</dt><dd><code>{data.id}</code></dd></dl></Dialog>
    <TestRunScope data={data} filters={filters} onChange={scopeChange} />
    <EvaluationOverview summary={data.evaluation_summary} scopeLabel="선택한 테스트·난이도·유형" onMatrixCell={cell => change({ type: "cell", value: cell })} selectedCell={filters.cell} />
    <div className="ux-toolbar"><button type="button" className="secondary" onClick={() => setComparisonOpen(true)}>다른 테스트와 비교</button></div>
    <Dialog open={comparisonOpen} title="테스트 비교" onClose={() => setComparisonOpen(false)}>{comparisonOpen && <TestRunComparison embedded candidateId={id} state={filters.comparison || initialTestComparisonState()} onStateChange={update => setFilters?.(current => ({ ...current, comparison: typeof update === "function" ? update(current.comparison || initialTestComparisonState()) : update }))} onOpen={onOpen} onUnauthorized={onUnauthorized} />}</Dialog>
    <section className="panel test-run-cases"><div className="panel-head"><div><h2>문항별 분석 결과</h2><small>상태·답안 비교·행렬 선택은 문항 목록만 좁힙니다. 위 지표는 유지됩니다.</small></div></div><div className="filter-grid"><label>처리 상태<select name="status" value={filters.status} onChange={rowChange}><option value="">전체</option>{Object.entries(runStatuses).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label><label>참고 답안 비교<select name="evaluation_outcome" value={filters.evaluation_outcome} onChange={rowChange}><option value="">전체</option>{Object.entries(evaluationOutcomes).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label></div>{(filters.status || filters.evaluation_outcome || filters.cell) && <button type="button" className="text-button" onClick={() => change({ type: "clear_rows" })}>문항 필터 초기화</button>}{filters.cell && <p className="notice">선택한 셀: {matrixCells.find(([key]) => key === filters.cell)?.[1]} <button type="button" className="text-button" onClick={() => change({ type: "clear_cell" })}>선택 해제</button></p>}{data.items.length ? <TestRunItemRows items={data.items} onOpen={onOpen} /> : <div className="test-run-empty"><p>{data.total_items > 0 && filters.offset ? "이 페이지에 표시할 문항이 없습니다." : "조건에 맞는 문항이 없습니다."}</p>{filters.offset > 0 && <button type="button" className="secondary small" onClick={() => change({ type: "first_page" })}>첫 페이지로</button>}{!filters.status && !filters.evaluation_outcome && !filters.cell && <p className="muted">위의 난이도·테스트 유형을 확인해 보세요.</p>}</div>}<div className="pagination"><span>{data.total_items}건</span><label>페이지당<select value={filters.limit} onChange={event => change({ type: "limit", value: Number(event.target.value) })}>{[25, 50, 100].map(value => <option key={value} value={value}>{value}건</option>)}</select></label><button type="button" className="secondary" disabled={!filters.offset} onClick={() => change({ type: "page", direction: -1 })}>이전</button><button type="button" className="secondary" disabled={filters.offset + filters.limit >= data.total_items} onClick={() => change({ type: "page", direction: 1 })}>다음</button></div></section>
  </>}</div>;
}
