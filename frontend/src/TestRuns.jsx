import NavigationAction from "./NavigationAction.jsx";
import { useEffect, useMemo, useRef, useState } from "react";
import { api } from "./api.js";
import { analysisRowState, formatDate, formatDuration } from "./analysisView.js";
import { CompactReferenceComparison } from "./ReferenceLabels.jsx";
import { evaluationOutcomes, referenceVerdicts } from "./labelEvaluation.js";
import { metricText, matrixCells } from "./evaluationMetrics.js";
import { runKinds, caseStatuses, testRunDisplayState, testRunQuery, testScopeSelection } from "./testRuns.js";
import { MetricHelp } from "./EvaluationMetrics.jsx";
import TestRunComparison from "./TestRunComparison.jsx";
import { initialTestComparisonState } from "./testComparison.js";
import "./testRuns.css";
import "./overviewEvaluationDesign.css";
import Dialog from "./Dialog.jsx";
import EvaluationOverview from "./EvaluationOverview.jsx";
import SummaryPreview from "./SummaryPreview.jsx";
import AnalysisSelectionActions, { useAnalysisSelection } from "./AnalysisSelection.jsx";
import TestReevaluation from "./TestReevaluation.jsx";
import DataTable, { serverSorting, changedSort } from "./DataTable.jsx";
import Pagination from "./Pagination.jsx";
import RetryTestFailures from "./RetryTestFailures.jsx";
import StopTestRun, { TestStoppedNotice } from "./StopTestRun.jsx";
import { isTestStopped } from "./testStop.js";
import DetailTabs from "./DetailTabs.jsx";
import TextInspector from "./TextInspector.jsx";
import { afterTestRetry } from "./testRetry.js";
import { useWords, MetricStrip } from "./LifecycleViews.jsx";
import R5Evaluation, { purposeText } from "./R5Evaluation.jsx";
import TestGroundTruthImport from "./TestGroundTruthImport.jsx";
import { canCopyTest } from "./evaluationDatasets.js";
import { initialAssessmentError } from "./initialAssessmentInput.js";
import { configurationChanges, comparisonContext } from "./testConfigurationIdentity.js";
import { ConsolePopover } from "./ConsoleShell.jsx";
import TestStatistics from "./TestStatistics.jsx";
import TestExport from "./TestExport.jsx";
import { clearGroupSelection, groupCases, initialStatistics, selectedGroups, statisticFields } from "./testStatistics.js";
import { formatCaseFilter } from "./testCaseFilters.js";
import { beginScopedRead, emptyScopedRead, scopedRead, updateScopedRead } from "./scopedRead.js";

function SelectedStatisticsGroup({ filters, official = false }) {
  const w = useWords(), groups = selectedGroups(filters);
  return <>{groups.length > 0 && <p className="v5-context">{w("선택한 항목", "Selected group")}: {groups.map(({ field, value }, index) => <span key={field}>
    {index > 0 && " · "}{w(...(statisticFields.find(([key]) => key === field)?.slice(1) || ["필드", "Field"]))}: {value == null
      ? w("미입력", "Not provided") : field === "waf_action" ? ({ D: w("차단", "Deny"), A: w("허용", "Allow") }[value] || value) : value}
  </span>)}</p>}{filters.case_filter && <p className="test-filter-summary">{w("집계 전 필터 적용", "Pre-group filter applied")}: {formatCaseFilter(filters.case_filter, w, official)}</p>}</>;
}

export const initialTestRunHistoryState = () => ({ queryText: "", query: { q: "", limit: 10, offset: 0 } });
export const initialTestRunFilters = () => ({ view: "items", statistics: initialStatistics(), difficulty: "", test_category: "", difficulty_missing: false, test_category_missing: false, status: "", evaluation_outcome: "", cell: "", limit: 25, offset: 0, comparison: initialTestComparisonState() });

export function testRunHistoryChange(state, action) {
  if (action.type === "draft") return { ...state, queryText: action.value };
  if (action.type === "search") return { ...state, query: { ...state.query, q: state.queryText.trim(), offset: 0 } };
  if (action.type === "clear") return { ...state, queryText: "", query: { ...state.query, q: "", offset: 0 } };
  if (action.type === "offset") return { ...state, query: { ...state.query, offset: action.value } };
  if (action.type === "page") return { ...state, query: { ...state.query, offset: Math.max(0, state.query.offset + action.direction * state.query.limit) } };
  return state;
}

export function testRunFilterChange(filters, action) {
  if (action.type === "scope") return { ...filters, ...testScopeSelection(action.name, action.value), cell: "", status: "", evaluation_outcome: "", offset: 0 };
  if (action.type === "row" && ["status", "evaluation_outcome"].includes(action.name)) return { ...filters, [action.name]: action.value, cell: "", offset: 0 };
  if (action.type === "cell") return { ...clearGroupSelection(filters), cell: filters.cell === action.value ? "" : action.value, status: "", evaluation_outcome: "", offset: 0 };
  if (action.type === "clear_rows") return { ...clearGroupSelection(filters), status: "", evaluation_outcome: "", cell: "", offset: 0 };
  if (action.type === "first_page") return { ...filters, offset: 0 };
  if (action.type === "offset") return { ...filters, offset: action.value };
  if (action.type === "clear_cell") return { ...filters, cell: "", offset: 0 };
  if (action.type === "limit") return { ...filters, limit: action.value, offset: 0 };
  if (action.type === "page") return { ...filters, offset: Math.max(0, filters.offset + action.direction * filters.limit) };
  return filters;
}

import { invalidateHome, testRunActive } from "./readInvalidation.js";

// Each read belongs to one mounted query. Cancellation invalidates even a late
// response from a transport that does not honor AbortSignal.
export function watchTestRunRead({ read, onUpdate, isRunning, errorMessage, onUnauthorized, setTimer = setTimeout, clearTimer = clearTimeout, document: doc = globalThis.document }) {
  let active = true, timer, loading = false, failures = 0, wasRunning = false;
  const controller = new AbortController();
  async function load() {
    clearTimer(timer);
    if (!active || loading || doc?.hidden) return;
    loading = true;
    try {
      const data = await read({ signal: controller.signal });
      if (!active) return;
      failures = 0;
      onUpdate({ data, error: "", loading: false, updatedAt: new Date().toISOString() });
      const running = isRunning(data);
      if (wasRunning && !running) invalidateHome();
      wasRunning = running;
      if (!doc?.hidden && running) timer = setTimer(load, 5000);
    } catch (error) {
      if (!active) return;
      onUpdate({ error: error?.status === 401 ? "로그인 세션이 만료되었습니다. 다시 로그인하세요." : errorMessage, loading: false,
        ...([401, 403, 404].includes(error?.status) ? { data: null } : {}) });
      if (error?.status === 401) onUnauthorized?.();
      if ([401, 403, 404].includes(error?.status)) active = false;
      else if (!doc?.hidden) timer = setTimer(load, Math.min(25000 * 2 ** Math.min(++failures, 3), 120000));
    } finally {
      loading = false;
    }
  }
  const visibility = () => { clearTimer(timer); if (!doc?.hidden) void load(); };
  doc?.addEventListener("visibilitychange", visibility);
  void load();
  return () => { active = false; clearTimer(timer); controller.abort(); doc?.removeEventListener("visibilitychange", visibility); };
}

const testMetric = value => Number.isFinite(value) ? metricText(value) : "—";
function testRunVisualStatus(run) {
  const { status } = testRunDisplayState(run);
  return ["accepting", "evaluating"].includes(status) ? "processing" : status;
}
function TestRunStatus({ run, words: w }) {
  const { labelKo, labelEn } = testRunDisplayState(run);
  return <span className={`status status-${testRunVisualStatus(run)}`}>{w(labelKo, labelEn)}</span>;
}

export function TestRunRows({ items, onSelect, sorting, onSortingChange, expanded = false, production, onStopped, onUnauthorized, words: w = ko => ko }) {
  const metadata = run => `${purposeText(run.test_purpose, w)} · ` + (run.ground_truth ? `${run.ground_truth.dataset_name} · r${run.ground_truth.dataset_revision} · ${run.ground_truth.sample_count ?? run.ground_truth.approved_count} ${w("문항", "cases")}` : w("참고 판정 비교", "Reference Verdict comparison"));
  const identity = run => { const changes = configurationChanges(run.configuration_snapshot, production?.snapshot); const names = { primary: w("주 분석 모델", "Primary"), verifier: w("검증 모델", "Verifier"), evidence_editor: w("근거 정리 모델", "Evidence Editor"), prompt: w("분석 지침", "Analysis Instructions"), input_schema: w("입력 스키마", "Input Schema"), parser: w("파서", "Parser"), signature_explanations: w("시그니처 설명", "Signature explanations") }; return changes?.length ? changes.map(key => names[key]).join(" · ") + w(" 변경", " changed") : changes ? w("현재 운영 설정과 동일", "Same as current Production") : run.profile_metadata?.model_name || "—"; };
  const context = run => ({ not_official: w("공식 비교 대상 아님", "Not an official comparison"), no_baseline: w("동일 기준의 운영 평가 없음", "No matching Production baseline"), same: w("현재 운영 평가와 동일 기준", "Same Production evaluation basis"), different: w("현재 운영 평가와 다른 기준", "Different Production evaluation basis") })[comparisonContext(run, production?.evaluation?.summary?.ground_truth)];
  const columns = [
    { id: "name", header: w("테스트", "Test"), width: expanded ? 250 : "28%", sortable: true, render: run => <div className="oe-test-table-name"><button type="button" className="text-button" onClick={() => onSelect(run.id)}>{run.name}</button><small>{purposeText(run.test_purpose, w)}{run.ground_truth ? ` · ${run.ground_truth.dataset_name} · r${run.ground_truth.dataset_revision}` : ""}</small><details className="oe-test-table-context"><summary>{w("비교 기준", "Comparison basis")}</summary><small>{metadata(run)}</small><small>{context(run)}</small></details></div> },
    ...(!expanded ? [{ id: "configuration", header: w("구성", "Configuration"), width: "20%", render: run => <>{run.execution_mode === "stub" ? w("모의 실행", "Stub") : identity(run)}<small>{run.prompt_version || "—"}</small></> }] : []),
    { id: "status", header: w("상태", "Status"), width: expanded ? 130 : "15%", render: run => <>
      <TestRunStatus run={run} words={w} />
      <small>{w("성공", "Succeeded")} {run.completed ?? "—"} / {run.total ?? "—"} · {w("실패", "failed")} {run.failed ?? "—"}{run.rejected > 0 && <> · {w("접수 거부", "rejected")} {run.rejected}</>}{run.canceled > 0 && <> · {w("중지", "stopped")} {run.canceled}</>}</small>
      {isTestStopped(run) && <small>{w("완료 문항의 참고 지표", "Completed cases only")}</small>}
    </> },
    ...(expanded ? ["accuracy", "precision", "recall", "f1", "coverage", "abstention_rate", "false_positive_rate", "false_negative_rate"].map(key => ({ id: key, header: <>{key === "f1" ? "F1" : key[0].toUpperCase() + key.slice(1)}<MetricHelp metric={key} label={key} /></>, className: "numeric", render: run => testMetric(run.evaluation_summary?.metrics?.[key]) }))
      : [{ id: "evaluation", header: w("평가", "Evaluation"), render: run => <span className="r3-test-metrics">Acc {testMetric(run.evaluation_summary?.metrics?.accuracy)} · F1 {testMetric(run.evaluation_summary?.metrics?.f1)} · Cov {testMetric(run.evaluation_summary?.metrics?.coverage)}</span> }]),
    { id: "duration", header: w("소요 시간", "Duration"), width: expanded ? 100 : "10%", className: "numeric", render: run => formatDuration(run.total_elapsed_ms, "—") },
    ...(onStopped ? [{ id: "actions", header: w("작업", "Actions"), width: 72, render: run => <StopTestRun menu run={run} onStopped={onStopped} onUnauthorized={onUnauthorized} /> }] : []),
  ];
  return <DataTable className={expanded ? "test-run-table-expanded" : "test-run-table"} label={w("테스트 목록", "Tests")} data={items} columns={columns} headerGroups={expanded ? [{ label: "", span: 2 }, { label: w("정확도", "Quality"), span: 4 }, { label: w("판정 범위", "Decision coverage"), span: 2 }, { label: w("오류 방향", "Error direction"), span: 2 }, { label: "", span: onStopped ? 2 : 1 }] : undefined} sorting={sorting} onSortingChange={onSortingChange} rowClassName={run => analysisRowState(testRunVisualStatus(run)).className} />;
}

export function TestRunScope({ data, filters, onChange }) {
  const w = useWords();
  return <section className="panel test-run-scope"><h3>{w("평가 범위", "Evaluation scope")}</h3><p className="ux-muted">{w("접수 당시 난이도·유형으로 지표와 문항을 함께 좁힙니다.", "Filter metrics and cases by the difficulty and category captured at admission.")}</p><div className="filter-grid">
    <label>{w("난이도", "Difficulty")}<select name="difficulty" value={filters.difficulty_missing ? "missing:" : filters.difficulty ? `value:${filters.difficulty}` : ""} onChange={onChange}><option value="">{w("전체", "All")}</option><option value="missing:">미분류{Number.isInteger(data.missing_difficulty_count) ? ` (${data.missing_difficulty_count}건)` : ""}</option>{data.facets.difficulties.map(value => <option key={value} value={`value:${value}`}>{value}</option>)}</select></label>
    <label>{w("테스트 유형", "Category")}<select name="test_category" value={filters.test_category_missing ? "missing:" : filters.test_category ? `value:${filters.test_category}` : ""} onChange={onChange}><option value="">{w("전체", "All")}</option><option value="missing:">미분류{Number.isInteger(data.missing_test_category_count) ? ` (${data.missing_test_category_count}건)` : ""}</option>{data.facets.test_categories.map(value => <option key={value} value={`value:${value}`}>{value}</option>)}</select></label>
  </div></section>;
}

export function TestRunHistoryEmpty({ query, onClear, onViewAnalyses }) {
  return <div className="test-run-empty"><p>{query.q ? "검색한 이름에 해당하는 테스트가 없습니다." : query.offset ? "이 페이지에 표시할 테스트가 없습니다." : "아직 접수한 테스트가 없습니다."}</p>{query.q && <button type="button" className="secondary small" onClick={onClear}>검색 초기화</button>}<p className="muted">이전 개별 분석은 별도 목록에서 확인하세요.</p>{onViewAnalyses && <NavigationAction type="button" onClick={onViewAnalyses}>개별 테스트 분석 보기</NavigationAction>}</div>;
}

export function TestRunHistory({ refresh = 0, onSelect, state, onStateChange, onViewAnalyses, onUnauthorized, title, description }) {
  const w = useWords();
  const [production, setProduction] = useState(null);
  useEffect(() => { const controller = new AbortController(); api.productionConfiguration({ signal: controller.signal }).then(value => { if (!controller.signal.aborted) setProduction(value); }).catch(() => {}); return () => controller.abort(); }, []);
  const [localState, setLocalState] = useState(initialTestRunHistoryState);
  const view = state ?? localState;
  const setView = state == null ? setLocalState : onStateChange;
  const change = action => setView?.(current => testRunHistoryChange(current, action));
  const { queryText, query } = view;
  const [read, setRead] = useState(emptyScopedRead); const [reload, setReload] = useState(0);
  const unauthorized = useRef(onUnauthorized); unauthorized.current = onUnauthorized;
  const scopeKey = JSON.stringify(query);
  const requestKey = JSON.stringify([scopeKey, refresh, reload]);
  const { data, error, loading } = scopedRead(read, scopeKey);
  useEffect(() => {
    setRead(previous => beginScopedRead(previous, scopeKey, requestKey));
    return watchTestRunRead({ read: async options => { const result = await api.testRuns({ ...query, reference_basis: "latest" }, options); if (!Array.isArray(result?.items)) throw new Error("invalid_test_runs"); return result; }, onUpdate: patch => setRead(current => updateScopedRead(current, scopeKey, requestKey, patch)), isRunning: result => result.items.some(testRunActive), errorMessage: "테스트 목록을 조회하지 못했습니다. 다시 조회하세요.", onUnauthorized: () => unauthorized.current?.() });
  }, [requestKey]);
  return <section className="panel test-run-history oe-test-history oe-test-history-v2" aria-busy={loading}><div className="panel-head"><div><h2>{title || w("테스트 목록", "Tests")}</h2>{data && <span className="oe-count"><strong>{data.total.toLocaleString()}</strong>{w("개 테스트", "tests")}</span>}<small>{description || w("실행 당시 설정과 평가 기준으로 결과를 비교합니다.", "Compare results using the settings and evaluation basis captured at execution.")}</small></div><button type="button" className="secondary" disabled={loading} onClick={() => setReload(v => v + 1)}>{w("새로고침", "Refresh")}</button></div>
    <div className="oe-history-controls">
    <form className="test-run-search" onSubmit={event => { event.preventDefault(); change({ type: "search" }); }}><label>{w("테스트명 검색", "Find test")}<input value={queryText} maxLength={120} onChange={event => change({ type: "draft", value: event.target.value })} placeholder={w("테스트 이름", "Test name")} /></label><button type="submit" className="secondary" disabled={loading}>{w("검색", "Search")}</button><button type="button" className="secondary r3-metrics-toggle" aria-expanded={Boolean(view.metricsExpanded)} onClick={() => setView(current => ({ ...current, metricsExpanded: !current.metricsExpanded }))}>{view.metricsExpanded ? w("평가지표 접기", "Collapse metrics") : w("평가지표 펼치기", "Expand metrics")}</button></form>
    <label className="checkbox-row"><input type="checkbox" checked={Boolean(query.has_failures)} onChange={e => setView(current => ({ ...current, query: { ...current.query, has_failures: e.target.checked, offset: 0 } }))} />{w("실패·접수 거부가 있는 테스트", "Tests with failed or rejected cases")}</label>
    </div>
    {query.q && <p className="test-run-applied">적용된 테스트명: <strong>{query.q}</strong> <button type="button" className="text-button" onClick={() => change({ type: "clear" })}>검색 초기화</button></p>}
    {error && <p className="error" role="alert">{data ? w("목록을 갱신하지 못해 이전 값을 표시합니다. 다시 조회해 주세요.", "Refresh failed; showing the previous list. Try again.") : error}</p>}{!data && !error && <p role="status">{w("테스트 목록을 불러오는 중…", "Loading Tests…")}</p>}
    {data && loading && <span className="sr-only" role="status">{w("최신 목록을 확인하는 중…", "Refreshing tests…")}</span>}
    {data && <>{data.items.length ? <TestRunRows onStopped={() => setReload(value => value + 1)} onUnauthorized={onUnauthorized} production={production} words={w} expanded={Boolean(view.metricsExpanded)} items={data.items} onSelect={onSelect} sorting={serverSorting(query.sort_by || "created_at", query.sort_order || "desc")} onSortingChange={update => setView?.(current => ({ ...current, query: { ...current.query, ...changedSort(update, serverSorting(query.sort_by || "created_at", query.sort_order || "desc")) } }))} /> : <TestRunHistoryEmpty query={query} onClear={() => change({ type: "clear" })} onViewAnalyses={onViewAnalyses} />}<Pagination label="테스트 목록 페이지" total={data.total} limit={query.limit} offset={query.offset} disabled={loading} unit="개" onOffsetChange={value => change({ type: "offset", value })} /></>}
  </section>;
}

export function TestRunItemError({ item }) {
  const [open, setOpen] = useState(false);
  const w = useWords();
  const explanation = initialAssessmentError({ message: item.error_code }, w);
  return <><button type="button" className="text-button" onClick={() => setOpen(true)}>오류 정보</button><Dialog open={open} title="문항 오류 정보" onClose={() => setOpen(false)}>{explanation && <p>{explanation}</p>}<dl className="label-metadata"><dt>문항</dt><dd>{item.case_name || item.event_id || `${item.row_number}행`}</dd><dt>이벤트 식별자</dt><dd>{item.event_id || "미기록"}</dd><dt>오류 코드</dt><dd><code>{item.error_code}</code></dd></dl><p>접수 거부·실행 실패는 정답 불일치와 구분하며 품질 지표에 오답으로 포함하지 않습니다.</p></Dialog></>;
}

export function TestRunItemRows({ items, onOpen, selection, sorting, onSortingChange, onImport, onGroundTruth }) {
  const w = useWords();
  const columns = [
    ...(selection ? [{ id: "select", header: selection.header, width: 44, className: "selection-cell", render: item => item.analysis_id && selection.cell(item.analysis_id, item.case_name || `${item.row_number}행`) }] : []),
    { id: "case_name", header: w("문항 / 분류", "Case / category"), sortable: true, width: "28%", render: item => <>{item.analysis_id ? <button type="button" className="text-button" onClick={() => onOpen(item.analysis_id)}>{item.case_name || item.event_id || `${item.row_number}행`}</button> : <strong>{item.case_name || item.event_id || `${item.row_number}행`}</strong>}<small>{item.row_number}행 · {item.difficulty || "난이도 미분류"} · {item.test_category || "유형 미분류"}</small></> },
    { id: "summary", header: w("심층 판정 / 요약", "Deep Assessment / summary"), render: item => <><strong>{item.status === "completed" ? referenceVerdicts[item.verdict] || "판정 정보 없음" : "—"}</strong><SummaryPreview text={item.summary_ko} /></> },
    { id: "reference", header: w("참고 판정 비교", "Reference Verdict comparison"), width: "20%", render: item => item.ingest_status === "rejected" ? "접수 거부 · 평가 제외" : <CompactReferenceComparison evaluation={item.evaluation} /> },
    { id: "status", header: w("처리 상태", "Execution status"), sortable: true, width: "14%", render: item => <>{item.ingest_status === "rejected" ? "접수 거부" : w(caseStatuses[item.status] || "상태 미확인", item.status === "canceled" ? "Stopped" : item.status || "Unknown")}{item.retry_count > 0 && <small>재실행 {item.retry_count}회</small>}{item.error_code && item.error_code !== "test_run_stopped" && <TestRunItemError item={item} />}</> },
    ...(items.some(item => item.analysis_id && (onImport || item.ground_truth_source && onGroundTruth)) ? [{
      id: "actions", header: w("작업", "Actions"), width: 88,
      render: item => item.analysis_id && (onImport || item.ground_truth_source && onGroundTruth) ? <ConsolePopover label={w("문항 작업", "Case actions")} trigger={w("더 보기", "More")}>{close => <>
        {item.ground_truth_source && onGroundTruth && <button type="button" onClick={() => { close(); onGroundTruth(item.ground_truth_source.dataset_id, item.ground_truth_source.stable_case_id); }}>{w("평가 데이터셋에서 열기", "Open in Evaluation Dataset")}</button>}
        {onImport && <button type="button" onClick={() => { close(); onImport(item.id); }}>{w("이 문항을 평가 데이터셋에 복사", "Copy This Case to Evaluation Dataset")}</button>}
      </>}</ConsolePopover> : "—",
    }] : []),
  ];
  return <DataTable label="문항별 분석 결과" data={items} columns={columns} sorting={sorting} onSortingChange={onSortingChange} rowClassName={item => analysisRowState(item.status).className} />;
}

export function TestRunDetail({ id, onBack, onOpen, filters: controlledFilters, onFiltersChange, backLabel = "테스트 목록", onUnauthorized, onPromote, onClone, onGroundTruth }) {
  const w = useWords(); const [importOpen, setImportOpen] = useState(false), [importIds, setImportIds] = useState(null);
  const [metadataOpen, setMetadataOpen] = useState(false), [scopeOpen, setScopeOpen] = useState(false), [basisOpen, setBasisOpen] = useState(false);
  const [saveNotice, setSaveNotice] = useState("");
  const [localFilters, setLocalFilters] = useState(initialTestRunFilters);
  const filters = controlledFilters ?? localFilters;
  const setFilters = controlledFilters == null ? setLocalFilters : onFiltersChange;
  const comparisonOpen = Boolean(filters.comparison?.open);
  const setComparisonOpen = value => setFilters?.(current => ({ ...current, comparison: { ...(current.comparison || initialTestComparisonState()), open: value } }));
  const change = action => setFilters?.(current => testRunFilterChange(current, action));
  const [read, setRead] = useState(emptyScopedRead); const [reload, setReload] = useState(0);
  const unauthorized = useRef(onUnauthorized); unauthorized.current = onUnauthorized;
  const query = useMemo(() => testRunQuery(filters), [filters]);
  const scopeKey = JSON.stringify([id, query]);
  const requestKey = JSON.stringify([scopeKey, reload]);
  const { data, error, loading } = scopedRead(read, scopeKey);
  useEffect(() => {
    setRead(previous => beginScopedRead(previous, scopeKey, requestKey));
    return watchTestRunRead({ read: async options => { const result = await api.testRun(id, query, options); if (result?.id !== id || !Array.isArray(result.items)) throw new Error("invalid_test_run"); return result; }, onUpdate: patch => setRead(current => updateScopedRead(current, scopeKey, requestKey, patch)), isRunning: testRunActive, errorMessage: "테스트 실행 정보를 조회하지 못했습니다. 다시 조회하세요.", onUnauthorized: () => unauthorized.current?.() });
  }, [requestKey]);
  const selection = useAnalysisSelection(data?.items || [], JSON.stringify([id, query]));
  const scopeChange = event => { const { name, value } = event.target; change({ type: "scope", name, value }); };
  const rowChange = event => { const { name, value } = event.target; change({ type: "row", name, value }); };
  const retrySubmitted = message => { setSaveNotice(message); selection.clear(); setFilters?.(afterTestRetry); setReload(value => value + 1); };
  const moreActions = data && <ConsolePopover label={w("테스트 작업", "Test actions")} trigger={w("더 보기", "More")}>{close => <><button onClick={() => { close(); setComparisonOpen(true); }}>{w("다른 테스트와 비교", "Compare tests")}</button><button onClick={() => { close(); setMetadataOpen(true); }}>{w("실행 정보", "Execution records")}</button><button onClick={() => { close(); setScopeOpen(true); }}>{w("난이도와 유형 필터", "Difficulty and category")}</button><button onClick={() => { close(); setBasisOpen(true); }}>{w("평가 기준과 기록", "Evaluation basis and history")}</button><button disabled={!canCopyTest(data)} onClick={() => { close(); setImportIds(null); setImportOpen(true); }}>{w("테스트 문항을 평가 데이터셋에 복사", "Copy Test Cases to Evaluation Dataset")}</button></>}</ConsolePopover>;
  return <div className="page-stack test-run-detail oe-test-detail-v2" aria-busy={loading}>{saveNotice && <p className="notice" role="status">{saveNotice}</p>}<div className="panel-head-inline oe-test-backline"><button type="button" className="back" onClick={onBack}>← {backLabel === "테스트 목록" ? w("테스트", "Tests") : backLabel}</button><button type="button" className="text-button" disabled={loading} onClick={() => setReload(v => v + 1)}>{w("새로고침", "Refresh")}</button></div>{error && <p className="error" role="alert">{error}</p>}{!data && !error && <p role="status">{w("실행 정보를 불러오는 중…", "Loading Test…")}</p>}{data && <>
    <header className="oe-test-detail-heading"><div><h1>{data.name}</h1><p>{w("실행 당시의 설정과 문항을 기준으로 결과를 확인합니다.", "Review results using the configuration and cases recorded for this Test.")}</p></div><div className="oe-test-heading-actions">{onClone && <button type="button" className="secondary" onClick={() => onClone(id)}>{w("이 설정으로 다시 테스트", "Run Again as New Test")}</button>}{onPromote && !isTestStopped(data) && ["completed", "failed"].includes(data.status) && data.test_purpose === "official_evaluation" && data.ground_truth?.published && data.configuration_snapshot && <NavigationAction onClick={() => onPromote(id)}>{w("운영 반영 검토", "Production Review")}</NavigationAction>}{!isTestStopped(data) && data.failed > 0 && <RetryTestFailures key={id} run={data} onSubmitted={retrySubmitted} />}<StopTestRun run={data} onStopped={() => { selection.clear(); setReload(value => value + 1); }} onUnauthorized={onUnauthorized} />{moreActions}<TestExport id={id} filters={filters} onUnauthorized={onUnauthorized} /></div></header>
    <div className="oe-test-context-strip"><span className="oe-test-purpose">{purposeText(data.test_purpose, w)}</span><TestRunStatus run={data} words={w} /><span>{data.evaluation_mode === "ground_truth" ? `${data.ground_truth?.published ? w("공식 버전", "Published Version") : w("이전 평가 기록", "Legacy evaluation")} · ${data.ground_truth?.dataset_name || "Evaluation Dataset"} · r${data.ground_truth?.dataset_revision}` : w("참고 판정 비교", "Reference Verdict comparison")}</span><time dateTime={data.created_at}>{formatDate(data.created_at)}</time></div>
    <dl className="oe-test-inline-totals"><div><dt>{w("성공", "Succeeded")}</dt><dd>{data.completed} / {data.total}</dd></div><div className={data.failed > 0 ? "oe-run-failures" : ""}><dt>{w("실행 실패", "Execution failures")}</dt><dd>{data.failed}</dd></div><div><dt>{w("접수 거부", "Rejected")}</dt><dd>{data.rejected}</dd></div><div><dt>{w("경과 시간", "Elapsed")}</dt><dd>{formatDuration(data.total_elapsed_ms, "—")}</dd></div></dl>
    {data.execution_mode === "stub" && <p className="notice">{w("모의 실행은 모델 평가에서 제외됩니다.", "Stub runs are excluded from model evaluation.")}</p>}
    <details className="oe-run-configuration"><summary>{w("실행에 사용한 설정", "Configuration used for this Test")}</summary><dl><div><dt>{w("주 분석 모델", "Primary")}</dt><dd>{data.profile_metadata?.model_name || "—"}</dd></div><div><dt>{w("검증 모델", "Verifier")}</dt><dd>{data.profile_metadata?.verifier_profile?.model_name || "—"}</dd></div><div><dt>{w("분석 지침", "Analysis Instructions")}</dt><dd>{data.prompt_version || "—"}</dd></div><div><dt>{w("입력 스키마", "Input Schema")}</dt><dd>{data.input_schema_metadata?.version_number ? `v${data.input_schema_metadata.version_number}` : w("실행 시 고정", "Pinned at admission")}</dd></div></dl><button type="button" className="text-button oe-config-records" onClick={() => setMetadataOpen(true)}>{w("실행 정보 전체 보기", "View full execution records")}</button></details>
    <TestStoppedNotice run={data} />
    <Dialog open={metadataOpen} title="테스트 실행 정보" onClose={() => setMetadataOpen(false)}><p className="reference-inline-note">모델·지침은 실행 당시 설정을 유지합니다. 화면의 점수는 선택한 참고 판정 기준이며, 접수 당시 기대 판정과 저장한 평가는 별도로 보존합니다.</p><dl className="label-metadata"><dt>모델</dt><dd>{data.profile_metadata?.model_name || "미기록"}</dd><dt>지침</dt><dd>{data.prompt_version || "미기록"}</dd><dt>연동 시스템 · 기대 판정 연결용</dt><dd><code>{data.source_system}</code></dd><dt>테스트 ID</dt><dd><code>{data.id}</code></dd></dl>{data.configuration_snapshot ? <TextInspector label="실행 당시 구성" value={data.configuration_snapshot} /> : <p className="ux-muted">이 실행에는 구성 전체를 기록한 정보가 없습니다. 현재 설정으로 대신 표시하지 않습니다.</p>}</Dialog>
    {data.accepting_items && <p className="v5-context">{w("문항 접수가 진행 중입니다. API 접수를 닫으면 모델 처리 중에도 평가 데이터셋으로 복사할 수 있습니다.", "Case ingestion is still open. Close API ingestion to copy cases while model processing continues.")}</p>}
    {importOpen && <TestGroundTruthImport itemIds={importIds} open runId={id} onClose={() => setImportOpen(false)} onOpenDataset={onGroundTruth} />}
    <Dialog open={basisOpen} onClose={() => setBasisOpen(false)} title={w("평가 기준·기록", "Evaluation basis and history")}><TestReevaluation run={data} value={filters.evaluation_id} onChange={value => setFilters?.(current => ({ ...current, evaluation_id: value, offset: 0 }))} /></Dialog>
    <Dialog open={scopeOpen} onClose={() => setScopeOpen(false)} title={w("평가 범위", "Evaluation scope")}><TestRunScope data={data} filters={filters} onChange={scopeChange} /></Dialog>{(filters.difficulty || filters.test_category || filters.difficulty_missing || filters.test_category_missing) && <p className="v5-context">{w("적용된 평가 범위", "Applied scope")}: {filters.difficulty || (filters.difficulty_missing ? w("난이도 미분류", "Unclassified difficulty") : w("전체 난이도", "All difficulties"))} · {filters.test_category || (filters.test_category_missing ? w("유형 미분류", "Unclassified category") : w("전체 유형", "All categories"))} <button className="text-button" onClick={() => setScopeOpen(true)}>{w("변경", "Edit")}</button></p>}

    <Dialog className="comparison-dialog" open={comparisonOpen} title={w("테스트 비교", "Compare Tests")} onClose={() => setComparisonOpen(false)}>{comparisonOpen && <TestRunComparison embedded candidateId={id} state={filters.comparison || initialTestComparisonState()} onStateChange={update => setFilters?.(current => ({ ...current, comparison: typeof update === "function" ? update(current.comparison || initialTestComparisonState()) : update }))} onOpen={onOpen} onUnauthorized={onUnauthorized} />}</Dialog>
    <DetailTabs label={w("테스트 결과 상세", "Test result details")} items={[["items", w("문항별 결과", "Case Results")], ["metrics", w("평가 상세", "Evaluation Details")], ["statistics", w("통계 요약", "Statistics Summary")]]} value={filters.view || "items"} onChange={view => setFilters?.(current => ({ ...current, view }))}>{key => key === "metrics" ? <R5Evaluation official={data.evaluation_mode === "ground_truth"} summary={data.evaluation_summary} scopeLabel={w("선택한 테스트·난이도·유형", "Selected Test, difficulty and category")} onMatrixCell={cell => { setFilters?.(current => ({ ...testRunFilterChange(current, { type: "cell", value: cell }), view: "items" })); }} selectedCell={filters.cell} /> : key === "statistics" ? filters.view === "statistics" && <TestStatistics run={data} filters={filters} refresh={reload} state={filters.statistics} onChange={update => setFilters?.(current => ({ ...current, statistics: typeof update === "function" ? update(current.statistics || initialStatistics()) : update }))} onGroup={(field, value) => setFilters?.(current => groupCases(current, field, value))} onUnauthorized={onUnauthorized} /> : <>
    <AnalysisSelectionActions allowDataset={canCopyTest(data)} onAddDataset={() => { setImportIds(data.items.filter(item => selection.ids.includes(item.analysis_id)).map(item => item.id)); setImportOpen(true); }} allowReferences={data.evaluation_mode !== "ground_truth"} ids={selection.ids} onClear={selection.clear} onSaved={result => { if (result?.kind === "reference") { setSaveNotice(result.message); setFilters?.(current => ({ ...current, evaluation_id: "latest", status: "", evaluation_outcome: "", cell: "", offset: 0 })); } setReload(value => value + 1); }} />
    <section className="panel test-run-cases"><div className="panel-head"><div><h2>{w("문항별 결과", "Case Results")}</h2><small>{w("문항 필터는 평가 범위를 바꾸지 않습니다. 통계에서는 집계 전 필터를 별도로 적용합니다.", "Case filters do not change evaluation scope. Statistics have a separate pre-group filter.")}</small></div></div><SelectedStatisticsGroup filters={filters} official={data.evaluation_mode === "ground_truth"} /><div className="filter-grid"><label>{w("처리 상태", "Execution status")}<select name="status" value={filters.status} onChange={rowChange}><option value="">{w("전체", "All")}</option>{Object.entries(caseStatuses).map(([value, label]) => <option key={value} value={value}>{w(label, value === "canceled" ? "Stopped" : value)}</option>)}</select></label><label>{w("참고 판정 비교", "Reference Verdict comparison")}<select name="evaluation_outcome" value={filters.evaluation_outcome} onChange={rowChange}><option value="">{w("전체", "All")}</option>{Object.entries(evaluationOutcomes).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label></div>{(filters.status || filters.evaluation_outcome || filters.cell || selectedGroups(filters).length || filters.case_filter) && <button type="button" className="text-button" onClick={() => change({ type: "clear_rows" })}>{w("문항 필터 초기화", "Clear case filters")}</button>}{filters.cell && <p className="notice">선택한 셀: {matrixCells.find(([key]) => key === filters.cell)?.[1]} <button type="button" className="text-button" onClick={() => change({ type: "clear_cell" })}>{w("선택 해제", "Clear selection")}</button></p>}{data.items.length ? <TestRunItemRows items={data.items} onOpen={onOpen} onGroundTruth={onGroundTruth} onImport={canCopyTest(data) ? itemId => { setImportIds([itemId]); setImportOpen(true); } : undefined} selection={selection} sorting={serverSorting(filters.sort_by || "row_number", filters.sort_order || "asc")} onSortingChange={update => setFilters?.(current => ({ ...current, ...changedSort(update, serverSorting(filters.sort_by || "row_number", filters.sort_order || "asc")) }))} /> : <div className="test-run-empty"><p>{data.total_items > 0 && filters.offset ? "이 페이지에 표시할 문항이 없습니다." : "조건에 맞는 문항이 없습니다."}</p>{filters.offset > 0 && <button type="button" className="secondary small" onClick={() => change({ type: "first_page" })}>첫 페이지로</button>}{!filters.status && !filters.evaluation_outcome && !filters.cell && <p className="muted">선택한 난이도·테스트 유형을 확인해 보세요.</p>}</div>}<Pagination label="테스트 문항 페이지" total={data.total_items} limit={filters.limit} offset={filters.offset} disabled={loading} onOffsetChange={value => change({ type: "offset", value })} onLimitChange={value => change({ type: "limit", value })} /></section></>}</DetailTabs>
  </>}</div>;
}
