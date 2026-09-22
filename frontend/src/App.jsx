import NavigationAction from "./NavigationAction.jsx";
import { useCallback, useEffect, useMemo, useRef, useState, useSyncExternalStore } from "react";
import { api } from "./api.js";
import { decisionExplanation, provisionalAnalysisNotice, threatCategoryLabel } from "./decisionExplanation.js";
import { AnalystEvidence, DecisionIssues } from "./AnalystEvidence.jsx";
import { assessmentView, legacyEvidenceNotice } from "./analystAssessment.js";
import { Icon } from "./Icon.jsx";
import ProductionApi from "./ProductionApi.jsx";
import AnalysisReport from "./AnalysisReport.jsx";
import Pagination from "./Pagination.jsx";
import HelpTooltip from "./HelpTooltip.jsx";
import SummaryPreview from "./SummaryPreview.jsx";
import Dialog from "./Dialog.jsx";
import EvaluationOverview from "./EvaluationOverview.jsx";
import ConsoleShell, { ConsoleAppearance } from "./ConsoleShell.jsx";
import { ConsolePreferencesProvider, useConsolePreferences } from "./consolePreferences.jsx";
import MoreActions from "./MoreActions.jsx";
import { consoleDestination, consoleSearch } from "./consoleNavigation.js";
import { RuntimeStatus, RuntimeQuality, RuntimeInformation } from "./RuntimeViews.jsx";
import { Overview, Promotion, ProductionEvaluation, RuntimeWorkspace, Activity } from "./LifecycleViews.jsx";
import { startVisiblePolling } from "./visiblePolling.js";
import RetryAnalysis from "./RetryAnalysis.jsx";
import AnalysisDownloads from "./AnalysisDownloads.jsx";
import { analysisElapsedTime, analysisQuery, analysisReceivedAt, analysisRowState, appliedFilterTags, dashboardListState, executionDuration, formatDate, formatDuration, initialListState, removeAppliedFilter, searchFields, uploadErrorText, validateFilters } from "./analysisView.js";
import { EXTERNAL_DATA_APPROVAL, changeProfileProvider, editProfileForm, modelProfileError, newProfileForm, profilePayload, profileRequiresKey, providerLabel, providerOf, validateProfileForm } from "./llmProfiles.js";
import { CompactEvaluationDetail, CompactReferenceComparison, EvaluationSummary, LabelAttachment } from "./ReferenceLabels.jsx";
import AnalysisSelectionActions, { useAnalysisSelection } from "./AnalysisSelection.jsx";
import DataTable, { Table, serverSorting, changedSort } from "./DataTable.jsx";
import DataManagement from "./DataManagement.jsx";
import GroundTruthWorkspace from "./GroundTruthWorkspace.jsx";
import R3Overview from "./R3Overview.jsx";
import RegistryFilters from "./RegistryFilters.jsx";
import DatasetAnalysis from "./DatasetAnalysis.jsx";
import CandidateConfiguration from "./CandidateConfiguration.jsx";
import GlobalSearchResults from "./GlobalSearchResults.jsx";
import TestDefaults, { ReadOnlyAgentRoles } from "./R5TestDefaults.jsx";
import { InitialAssessment, useR5Words } from "./R5Evaluation.jsx";
import { evaluationOutcomes, isMockAnalysis, labelSources, referenceVerdicts } from "./labelEvaluation.js";
import { createDetailLoader, emptyDetailState } from "./detailLoader.js";
import { analysisNotices, analystFieldLabel, analystFollowUp, analystGuidance, analystItems, analystSummary, analystText, finalValue, groupedEvidence, hasTuningContent, isTechnicalText } from "./analystView.js";
import RawEventView from "./RawEventView.jsx";
import AgentHistory from "./AgentHistory.jsx";
import DetailTabs from "./DetailTabs.jsx";
import InferenceSummary from "./InferenceSummary.jsx";
import "./inferenceDetail.css";
import QuickValidationDialog from "./QuickValidationDialog.jsx";
import TextInspector from "./TextInspector.jsx";
import { createLogoutController, loginError } from "./inspection.js";
import AgentSettings, { Diagnostics } from "./AgentSettings.jsx";
import PromptSettings from "./PromptSettings.jsx";
import ConcurrencySettings from "./ConcurrencySettings.jsx";
import InputSchemaSettings, { InputSchemaMetadata } from "./InputSchemaSettings.jsx";
import InternalEgressSettings from "./InternalEgressSettings.jsx";
import ServiceApiKeys from "./ServiceApiKeys.jsx";
import FullValidationDialog, { DatasetEvaluation } from "./FullValidationDialog.jsx";
import ModelCheckDetail from "./ModelCheckDetail.jsx";
import { modelCheckNames, modelValidationError } from "./modelCheckDiagnostics.js";
import { createFullValidationController, datasetListState, emptyFullValidationState, expectedVerdictUploadError, uploadLabelNotice } from "./modelValidation.js";
import { allowedInternalTarget, internalEgressError, internalTargetAddress, internalTargetURL, vllmTargetError } from "./internalEgress.js";
import "./unifiedAnalysis.css";
import { initialTestRunFilters, initialTestRunHistoryState, TestRunDetail, TestRunHistory } from "./TestRuns.jsx";
import { autoTestName, emptySingleTest, newTestRequestKey, singleTestEvent, testRunError, validateTestName } from "./testRuns.js";
import { ModelAssignmentDialog } from "./ModelAssignments.jsx";
import { assignmentBlockReason, createAssignmentController, emptyAssignmentState, roleAssigned } from "./modelAssignments.js";
import { createBrowserHistory } from "./browserHistory.js";
import { applyAppRoute, isDetailOrigin, readAppHash, writeAppHash } from "./appRoutes.js";

const labels = {
  pending: "대기",
  processing: "분석 중",
  completed: "완료",
  failed: "실패",
  running: "실행 중",
  passed: "통과",
  draft: "미검증",
  verified: "검증 완료",
  production: "Production",
  disabled: "비활성",
  true_positive: "정탐",
  false_positive: "오탐",
  inconclusive: "보류",
  exact: "일치",
  partial: "부분 일치",
  mismatch: "불일치",
  unknown: "확인 불가",
  unreviewed: "미검토",
  confirmed: "검토 완료",
  deferred: "검토 보류"
};

const purposeLabels = { production: "프로덕션", test: "테스트", legacy_unknown: "기존 미분류" };
const channelLabels = { service_api: "서비스 API", file_upload: "배치 파일 분석", test_lab: "단건 분석", model_validation: "모델 검증", legacy_unknown: "기존 미분류" };

function Purpose({ value }) {
  return <span className={`status purpose-${value || "legacy_unknown"}`}>{purposeLabels[value] || "기존 미분류"}</span>;
}

const severityLabels = {
  CRITICAL: "CRITICAL",
  HIGH: "HIGH",
  MEDIUM: "MEDIUM",
  LOW: "LOW",
  NONE: "해당 없음",
  UNKNOWN: "미확정"
};

function Status({ value }) {
  return <span className={`status status-${value}`}>{labels[value] || value || "-"}</span>;
}

function Severity({ value, describe = false }) {
  const normalized = typeof value === "string" ? value.toUpperCase() : "";
  if (!severityLabels[normalized]) {
    return <span className="severity severity-unrated">미평가(이전 결과)</span>;
  }
  return <span className={`severity severity-${normalized.toLowerCase()}`} aria-label={`심각도 ${severityLabels[normalized]}`}>{describe ? "심각도 " : ""}{severityLabels[normalized]}</span>;
}

export function Login({ onLogin }) {
  const { locale } = useConsolePreferences();
  const w = (ko, en) => locale === "en" ? en : ko;
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const submitting = useRef(false);

  async function submit(event) {
    event.preventDefault();
    if (submitting.current) return;
    submitting.current = true;
    setBusy(true);
    setError("");
    try {
      await api.login(username, password);
      onLogin();
    } catch (err) {
      setError(loginError(err));
    } finally {
      submitting.current = false;
      setBusy(false);
    }
  }

  return (
    <main className="login-shell">
      <div className="r3-login-appearance"><ConsoleAppearance /></div>
      <form className="login-card" onSubmit={submit}>
        <div className="brand-mark"><Icon name="shield" size={26} /></div>
        <span className="eyebrow">WAF AI CONSOLE</span>
        <h1>{w("WAF AI 분석 콘솔", "WAF AI Console")}</h1>
        <label>{w("아이디", "Username")}<input required value={username} placeholder={w("관리자 아이디", "Admin username")} onChange={(e) => setUsername(e.target.value)} autoComplete="username" autoCapitalize="none" spellCheck={false} /></label>
        <div><label htmlFor="login-password">{w("비밀번호", "Password")}</label><div className="login-password"><input id="login-password" required type={showPassword ? "text" : "password"} value={password} placeholder={w("비밀번호 입력", "Enter password")} onChange={(e) => setPassword(e.target.value)} autoComplete="current-password" /><button type="button" className="secondary" aria-label={showPassword ? w("비밀번호 숨기기", "Hide password") : w("비밀번호 표시", "Show password")} aria-pressed={showPassword} onClick={() => setShowPassword(value => !value)}>{showPassword ? w("숨김", "Hide") : w("표시", "Show")}</button></div></div>
        {error && <div className="error" role="alert">{error}</div>}
        <button className="primary" disabled={busy}>{busy ? w("확인 중…", "Signing in…") : w("로그인", "Sign in")}<Icon name="arrow" size={17} /></button>
      </form>
    </main>
  );
}


export function AnalysisTable({ items, onOpen, title = "분석 결과", subtitle, total = items.length, loading = false, purpose = "", selection, sorting, onSortingChange }) {
  const showPurpose = !["test", "production", "legacy_unknown"].includes(purpose);
  const columns = [
    ...(selection ? [{ id: "select", header: selection.header, width: 44, className: "selection-cell", render: item => selection.cell(item.id, item.signature || item.event_name || "분석") }] : []),
    { id: "decision", header: "판정 / 심각도", width: "14%", className: "table-meta unified-decision", render: item => {
      const state = analysisRowState(item.status);
      return <>{item.status === "completed" ? <><Status value={finalValue(item, "verdict")} /><Severity value={item.severity} /><span className="sr-only">{state.label}</span></> : <span className="analysis-row-state"><Icon name={item.status === "failed" ? "alert" : "clock"} size={14} />{state.label}</span>}
        <InitialAssessment compact value={item.initial_assessment} />{isMockAnalysis(item) && <small className="label-revision">모의 판정 · LLM 아님</small>}{item.input_truncated === true && <small>입력 일부 생략</small>}</>;
    }},
    { id: "summary", header: "이벤트 / 요약", className: "table-meta analyst-event unified-event", render: item => <><button className="text-button unified-event-title" onClick={() => onOpen(item.id)}>{item.signature || item.event_name || "분석 상세 보기"}</button><SummaryPreview text={analystSummary(item)} />{showPurpose && <Purpose value={item.analysis_purpose} />}</> },
    { id: "company_name", header: "회사 / 연결", sortable: true, width: "18%", className: "table-meta unified-company", render: item => <><strong title={item.company_name || ""}>{item.company_name}</strong><small title={`${item.src_ip || "-"}${item.src_port != null ? ` : ${item.src_port}` : ""}`}>{item.src_ip || "-"}{item.src_port != null ? ` : ${item.src_port}` : ""}</small><small title={`${item.dest_ip || "-"}${item.dest_port != null ? ` : ${item.dest_port}` : ""}`}>→ {item.dest_ip || "-"}{item.dest_port != null ? ` : ${item.dest_port}` : ""}</small></> },
    { id: "reference", header: "참고 답안 비교", width: "14%", className: "unified-reference", render: item => <CompactReferenceComparison evaluation={item.evaluation} detail={item} /> },
    { id: "duration", header: "전체 소요 시간", width: "10%", className: "numeric", render: item => analysisElapsedTime(item) },
    { id: "created_at", header: "접수 시각", sortable: true, width: "13%", render: item => { const receivedAt = analysisReceivedAt(item.created_at); return <time className="analysis-received-at" dateTime={receivedAt.dateTime}><span>{receivedAt.date}</span><small>{receivedAt.time}</small></time>; } },
  ];
  return <section className="panel"><div className="panel-head"><div><h2>{title}</h2>{subtitle && <small>{subtitle}</small>}</div><span className="count-label" aria-live="polite">{loading ? "조회 중…" : `${total.toLocaleString()}건`}</span></div>
    <DataTable label={title} className="analysis-data-table" data={items} columns={columns} sorting={sorting} onSortingChange={onSortingChange} rowClassName={item => analysisRowState(item.status).className}
      empty={<><Icon name="search" size={28} /><strong>{loading ? "분석을 불러오는 중…" : "조건에 맞는 분석이 없습니다."}</strong>{!loading && <small>검색 조건을 조정하거나 테스트 데이터를 접수해 보세요.</small>}</>} />
  </section>;
}

function SelectFilter({ label, name, value, onChange, options }) {
  return <label>{label}<select name={name} value={value} onChange={onChange}><option value="">전체</option>{options.map(([key, text]) => <option key={key} value={key}>{text}</option>)}</select></label>;
}

export function AnalysisScopeTabs({ purpose, onChange }) {
  return <div className="tabs purpose-tabs" role="tablist" aria-label="분석 구분">
    {[["", "전체"], ["test", "테스트"], ["production", "프로덕션"]].map(([value, label]) => <button key={value} type="button" role="tab" aria-selected={purpose === value || (value === "" && purpose === "legacy_unknown")} onClick={() => onChange(value)}>{label}</button>)}
  </div>;
}

export function initialTestResultsState() {
  return { view: "runs", runId: null, history: initialTestRunHistoryState(), filters: initialTestRunFilters(), items: initialListState("test") };
}

// Test-run searches and item filters never become Production analysis filters.
export function AnalysisResultsPage({ purpose, onScopeChange, state, setState, testState, setTestState, onSelectRun, onOpenRunItem, onOpen, onUnauthorized, onShowTestRuns, onShowAllTestItems, onCaseChange, onPromote, onClone, onGroundTruth, splitNavigation = false, actions }) {
  const setPart = key => update => setTestState(current => ({ ...current, [key]: typeof update === "function" ? update(current[key]) : update }));
  const showRuns = onShowTestRuns || (() => setTestState(current => ({ ...current, view: "runs", runId: null })));
  const showAllTestItems = onShowAllTestItems || (() => setTestState(current => ({ ...current, view: "items", runId: null, items: initialListState("test") })));
  if (purpose !== "test") return <AnalysisList purpose="production" key="production" state={state} setState={setState} onOpen={onOpen} onUnauthorized={onUnauthorized} />;
  if (!testState.runId && testState.view === "items") return <AnalysisList key="test-items" state={testState.items} setState={setPart("items")} onScopeChange={onScopeChange} onShowTestRuns={showRuns} onOpen={onOpen} onUnauthorized={onUnauthorized} />;
  return <div className="page-stack test-results-page">
    <div className="analysis-list-toolbar">{!splitNavigation && <AnalysisScopeTabs purpose="test" onChange={onScopeChange} />}{!testState.runId && <NavigationAction onClick={showAllTestItems}>테스트 문항 전체 보기</NavigationAction>}{actions && <div className="v5-list-action">{actions}</div>}</div>
    {testState.runId
      ? <TestRunDetail key={testState.runId} id={testState.runId} caseId={testState.caseId} onCaseChange={onCaseChange} onPromote={onPromote} onClone={onClone} onGroundTruth={onGroundTruth} filters={testState.filters} onFiltersChange={setPart("filters")} onBack={showRuns} onOpen={onOpenRunItem} onUnauthorized={onUnauthorized} />
      : <TestRunHistory state={testState.history} onStateChange={setPart("history")} onSelect={onSelectRun} onViewAnalyses={showAllTestItems} onUnauthorized={onUnauthorized} />}
    {!testState.runId && <p className="evaluation-footnote">테스트명 없이 저장된 이전 결과는 ‘테스트 문항 전체 보기’에서 확인할 수 있습니다. 기존 데이터를 임의의 테스트 실행으로 묶지 않습니다.</p>}
  </div>;
}

export function AnalysisList({ state, setState, onOpen, onUnauthorized, onScopeChange, onShowTestRuns, purpose = "test" }) {
  const w = useR5Words();
  const [data, setData] = useState({ items: [], total: 0 });
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [formError, setFormError] = useState("");
  const [labelRefresh, setLabelRefresh] = useState(0);
  const [attachmentOpen, setAttachmentOpen] = useState(false);
  const query = useMemo(() => ({ ...analysisQuery(state.applied, state.limit, state.offset), analysis_purpose: purpose, sort_by: state.sort_by || "created_at", sort_order: state.sort_order || "desc" }), [state.applied, state.limit, state.offset, state.sort_by, state.sort_order, purpose]);
  const selection = useAnalysisSelection(data.items, JSON.stringify(query));
  useEffect(() => {
    let active = true;
    setLoading(true); setData({ items: [], total: 0 }); setError("");
    const stop = startVisiblePolling(async signal => {
      try {
        const next = await api.analyses(query, { signal });
        if (active) {
          setData(next); setError("");
          if (query.offset > 0 && query.offset >= next.total) {
            setState((current) => ({ ...current, offset: Math.max(0, (Math.ceil(next.total / current.limit) - 1) * current.limit) }));
          }
        }
        return next.items.some(item => ["pending", "processing"].includes(item.status));
      } catch (err) {
        if (active) { setError(err.status === 401 ? "로그인이 만료되었습니다. 다시 로그인하세요." : "분석 목록을 불러오지 못했습니다. 다시 조회하세요."); if (err.status === 401) onUnauthorized(); }
        throw err;
      } finally { if (active) setLoading(false); }
    });
    return () => { active = false; stop(); };
  }, [query, onUnauthorized, setState, labelRefresh]);
  const update = (event) => { const { name, value } = event.target; setState((current) => ({ ...current, draft: { ...current.draft, [name]: value } })); };
  function apply(event) {
    event.preventDefault();
    const message = validateFilters(state.draft);
    setFormError(message);
    if (!message) setState((current) => ({ ...current, applied: { ...current.draft }, offset: 0 }));
  }
  function scope(purpose) {
    setFormError("");
    if (onScopeChange) { onScopeChange(purpose); return; }
    setState((current) => ({ ...current, offset: 0, draft: { ...current.draft, analysis_purpose: purpose }, applied: { ...current.applied, analysis_purpose: purpose } }));
  }
  function reset() {
    setFormError("");
    setState((current) => ({ ...initialListState(current.applied.analysis_purpose === "legacy_unknown" ? "" : current.applied.analysis_purpose), limit: current.limit, advanced: current.advanced }));
  }
  const select = (name, label, options) => <SelectFilter key={name} name={name} label={label} value={state.draft[name]} onChange={update} options={options} />;
  const fieldLengths = { source_system: 120, src_ip: 64, dest_ip: 64, signature: 500, event_name: 500, threat_category: 120, waf_vendor: 120, model_profile: 120, label_source_ref: 120 };
  const textField = (name, label) => <label key={name}>{label}<input name={name} value={state.draft[name]} onChange={update} maxLength={fieldLengths[name] || 255} /></label>;
  const dateTimezone = Intl.DateTimeFormat().resolvedOptions().timeZone;
  const activeTags = appliedFilterTags(state.applied, {
    status: labels, verdict: labels, review_state: labels, analysis_purpose: purposeLabels, ingest_channel: channelLabels,
    label_presence: { labeled: "있음", unlabeled: "없음" }, evaluation_outcome: evaluationOutcomes,
    reference_label: referenceVerdicts, label_source_kind: labelSources,
    label_ai_visible: { unknown: "미확인", false: "미열람 (입력자 신고)", true: "열람 · 지원 판정" },
    input_truncated: { true: "있음", false: "없음" }, waf_action: { D: "Deny · 차단", A: "Allow · 허용" },
  });
  return <div className="page-stack unified-analysis-list">
    <div className="analysis-list-toolbar">
    <button type="button" className="secondary" aria-expanded={attachmentOpen} aria-controls="analysis-label-attachment" onClick={() => setAttachmentOpen((open) => !open)}><Icon name="upload" size={16} />참고 답안 연결</button>
    </div>
    {onShowTestRuns && <div className="test-item-list-context"><button type="button" className="back" onClick={onShowTestRuns}>← 테스트 목록</button><p className="evaluation-footnote">테스트 문항 검색 · 이름 있는 실행과 실행 묶음이 없는 이전 결과를 함께 조회합니다. 아래 지표는 현재 검색 범위의 최신 참고 답안 기준입니다.</p></div>}
    <div id="analysis-label-attachment" hidden={!attachmentOpen}><LabelAttachment sourceSystem={state.applied.source_system} controlledOpen={attachmentOpen} onOpenChange={setAttachmentOpen} onAttached={() => setLabelRefresh((value) => value + 1)} /></div>
    <form className="panel filter-panel" onSubmit={apply}>
      <div className="filter-search">
        <label>검색 필드<select name="search_field" value={state.draft.search_field} onChange={update}>{searchFields.map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
        <label>검색어<input name="q" value={state.draft.q} onChange={update} maxLength={500} placeholder="회사, IP, 탐지명 등" /></label>
        <button className="primary" type="submit"><Icon name="search" size={16} />검색</button><button className="secondary" type="button" onClick={reset}>초기화</button>
      </div>
      <div className="filter-grid">
        {select("status", "처리 상태", ["pending", "processing", "completed", "failed"].map((value) => [value, labels[value]]))}
        {select("verdict", "AI 판정", ["true_positive", "false_positive", "inconclusive"].map((value) => [value, labels[value]]))}
        {select("severity", "심각도", Object.entries(severityLabels))}
        {select("evaluation_outcome", "답안 비교", Object.entries(evaluationOutcomes))}
      </div>
      <div className="filter-meta"><span>검색 조건</span><button type="button" className="text-button" aria-expanded={state.advanced} aria-controls="advanced-filters" onClick={() => setState((current) => ({ ...current, advanced: !current.advanced }))}>{state.advanced ? "필터 닫기" : "필터 더 보기"}</button></div>
      {state.advanced && <div className="filter-grid advanced-filters" id="advanced-filters">
        <label>접수 시작<input name="created_from" type="datetime-local" step="0.001" value={state.draft.created_from} onChange={update} /></label>
        <label>접수 종료<input name="created_to" type="datetime-local" step="0.001" value={state.draft.created_to} onChange={update} /><small className="ux-muted">{dateTimezone} · 시작 포함, 종료 제외</small></label>
        {select("label_presence", "참고 답안", [["labeled", "있음"], ["unlabeled", "없음"]])}
        {state.applied.analysis_purpose !== "test" && state.applied.analysis_purpose !== "production" && select("analysis_purpose", "기존 데이터", [["legacy_unknown", "기존 미분류만"]])}
        {select("ingest_channel", "유입 경로", Object.entries(channelLabels))}
        {searchFields.filter(([value]) => value !== "all").map(([value, label]) => textField(value, label))}
        {textField("waf_vendor", "WAF 벤더 (정확히)")}
        {select("waf_action", "WAF 조치", [["D", "차단 (D)"], ["A", "허용 (A)"]])}
        {select("review_state", "분석가 리뷰", ["unreviewed", "confirmed", "deferred"].map((value) => [value, labels[value]]))}
        {select("reference_label", "참고 판정", Object.entries(referenceVerdicts))}
        {select("label_source_kind", "답안 종류", Object.entries(labelSources))}
        {textField("label_source_ref", "답안 출처 / 버전 (정확히)")}
        {select("label_ai_visible", "답안 작성 시 AI 열람", [["unknown", "미확인"], ["false", "미열람 (입력자 신고)"], ["true", "열람 · 지원 판정"]])}
        {textField("model_profile", "모델 프로필 (정확히)")}
        {textField("test_run_id", "테스트 실행 ID (정확히)")}
        {textField("test_difficulty", "테스트 난이도 (정확히)")}
        {textField("test_category", "테스트 유형 (정확히)")}
        {select("input_truncated", "입력 잘림", [["true", "있음"], ["false", "없음"]])}
        {[["src_port", "출발지 포트"], ["dest_port", "목적지 포트"]].map(([name, label]) => <label key={name}>{label}<input type="number" name={name} min="0" max="65535" step="1" value={state.draft[name]} onChange={update} /></label>)}
        {[["confidence_min", "최소 신뢰도"], ["confidence_max", "최대 신뢰도"]].map(([name, label]) => <label key={name}>{label}<input type="number" name={name} min="0" max="1" step="any" value={state.draft[name]} onChange={update} placeholder="0 ~ 1" /></label>)}
      </div>}
      {formError && <div className="error" role="alert">{formError}</div>}
      {!!activeTags.length && <div className="applied-filter-tags" aria-label="적용된 검색 조건"><span>적용 중</span>{activeTags.map((tag) => <button type="button" className="filter-tag" key={tag.key} title={`${tag.label}: ${tag.value}`} aria-label={`${tag.label}: ${tag.value} 조건 해제`} onClick={() => tag.key === "analysis_purpose" && onScopeChange ? scope("") : setState((current) => removeAppliedFilter(current, tag.key))}><span>{tag.label}: {tag.value}</span><span aria-hidden="true">×</span></button>)}</div>}
    </form>
    {error && <div className="error" role="alert">{error}</div>}
    {purpose === "production" && <div className="panel initial-comparison-filter"><SelectFilter label={w("1차·심층 판정 비교", "Initial vs Deep Assessment")} name="initial_comparison" value={state.draft.initial_comparison || ""} onChange={event => { const value = event.target.value; setState(current => ({ ...current, offset: 0, draft: { ...current.draft, initial_comparison: value }, applied: { ...current.applied, initial_comparison: value } })); }} options={[["match", w("일치", "Match")], ["different", w("판정 다름", "Different")], ["final_inconclusive", w("심층 판정 보류", "Deep Assessment inconclusive")], ["unavailable", w("1차 판정 없음", "No Initial Assessment")]]} /></div>}
    <EvaluationOverview summary={data.evaluation_summary} loading={loading} error={error} />
    <AnalysisSelectionActions ids={selection.ids} onClear={selection.clear} onSaved={() => setLabelRefresh(value => value + 1)} />
    <AnalysisTable items={data.items} total={data.total} loading={loading} onOpen={onOpen} purpose={state.applied.analysis_purpose} selection={selection} sorting={serverSorting(query.sort_by, query.sort_order)} onSortingChange={update => setState(current => ({ ...current, ...changedSort(update, serverSorting(query.sort_by, query.sort_order)) }))} />
    <Pagination label="분석 결과 페이지" total={data.total} limit={state.limit} offset={state.offset} disabled={loading} onOffsetChange={offset => setState(current => ({ ...current, offset }))} onLimitChange={limit => setState(current => ({ ...current, limit, offset: 0 }))} />
  </div>;
}

export function TestAnalysisPage({ onViewTests, onOpen, selectedRunId, onSelectRun, agentMode, cloneRunId }) {
  const w = useR5Words();
  const [purpose, setPurpose] = useState("official_evaluation"), [inputMode, setInputMode] = useState("direct");
  const [candidate, setCandidate] = useState({ configuration: null, blocked: "configuration_unavailable" });
  const [submitting, setSubmitting] = useState(false), [template, setTemplate] = useState(null), [templateError, setTemplateError] = useState("");
  const [localRunId, setLocalRunId] = useState(null);
  useEffect(() => { if (!cloneRunId) return; const controller = new AbortController(); setTemplate(null); setTemplateError("");
    api.cloneTest(cloneRunId, { signal: controller.signal }).then(value => { if (!controller.signal.aborted) { setTemplate(value); setPurpose(value.test_purpose === "official_evaluation" ? "official_evaluation" : "development"); setInputMode(value.source_dataset_id ? "dataset" : value.input_source_kind === "upload" ? "file" : "direct"); } }).catch(() => { if (!controller.signal.aborted) setTemplateError(w("원래 설정을 불러오지 못했습니다.", "Could not load source configuration.")); }); return () => controller.abort();
  }, [cloneRunId]);
  const runId = selectedRunId === undefined ? localRunId : selectedRunId, selectRun = onSelectRun || setLocalRunId;
  const submissionProps = { candidateConfiguration: candidate.configuration, onBusy: setSubmitting, disabledReason: candidate.blocked };
  if (runId) return <TestRunDetail key={runId} id={runId} onBack={() => selectRun(null)} onOpen={onOpen} />;
  if (cloneRunId && !template) return <section className="panel"><p role={templateError ? "alert" : "status"}>{templateError || w("설정을 불러오는 중…", "Loading configuration…")}</p><button className="back" onClick={onViewTests}>{w("테스트로 돌아가기", "Back to Tests")}</button></section>;
  return <div className="page-stack test-workspace">
    <button type="button" className="back" onClick={onViewTests}>← {w("테스트", "Tests")}</button>
    <section className="panel"><h2 className="r5-step"><span>1</span>{w("테스트 목적", "Test purpose")}</h2><div className="r5-purpose-grid">{[["official_evaluation", w("공식 테스트", "Official Test"), w("공식 버전의 전체 문항으로 평가합니다. 완료 후 운영 반영을 검토할 수 있습니다.", "Evaluates every case in a Published Version. Enables Production Review after completion.")], ["development", w("개발 테스트", "Development Test"), w("단건·파일·데이터셋으로 확인합니다. 운영 반영 근거로 사용하지 않습니다.", "Explore a single event, file or dataset. Not eligible for Production.")]].map(([value, label, note]) => <label key={value}><input type="radio" name="test-purpose" value={value} checked={purpose === value} disabled={submitting} onChange={() => setPurpose(value)} /><strong>{label}</strong><small>{note}</small></label>)}</div></section>
    <CandidateConfiguration agentMode={agentMode} busy={submitting} onChange={setCandidate} initialConfiguration={template?.candidate_configuration} />
    {template && !template.resource_validity.valid && <p className="notice">{w("원래 설정에 사용할 수 없는 항목이 있습니다. 대체하지 않았으므로 선택을 확인하세요.", "Some source resources are unavailable. They were not replaced; review each selection.")}</p>}
    <section className="panel"><h2 className="r5-step"><span>3</span>{w("테스트 데이터", "Test data")}</h2>
      <div hidden={purpose !== "development"}><DetailTabs label={w("입력 방식", "Input source")} items={[["direct", w("단건 분석", "Single Analysis")], ["file", w("배치 파일 분석", "Batch File Analysis")], ["dataset", w("데이터셋", "Dataset")]]} value={inputMode} onChange={value => { if (!submitting) setInputMode(value); }}>{key => key === "direct" ? <SingleTest onCreated={run => selectRun(run.id)} {...submissionProps} /> : key === "file" ? <UploadPage onViewTests={onViewTests} onOpen={onOpen} onCreated={run => selectRun(run.id)} {...submissionProps} /> : purpose === "development" && <DatasetAnalysis purpose={purpose} sourceTemplate={template} onCreated={run => selectRun(run.id)} agentMode={agentMode} external={candidate.external} {...submissionProps} />}</DetailTabs></div>
      {purpose === "official_evaluation" && <DatasetAnalysis purpose={purpose} sourceTemplate={template} onCreated={run => selectRun(run.id)} agentMode={agentMode} external={candidate.external} {...submissionProps} />}
    </section>
  </div>;
}

function UploadPage({ onViewTests, onOpen, onCreated, disabledReason, candidateConfiguration, onBusy }) {
  const w = useR5Words();
  const [file, setFile] = useState(null);
  const [name, setName] = useState(""); const requestKey = useRef(null);
  const [result, setResult] = useState(null);
  useEffect(() => { requestKey.current = null; setResult(null); }, [candidateConfiguration]);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  async function upload() {
    if (busy) return;
    if (disabledReason) { setError(disabledReason); return; }
    if (!file) return;
    requestKey.current ||= newTestRequestKey();
    const testName = autoTestName(name, requestKey.current);
    const nameError = validateTestName(testName); if (nameError) { setError(nameError); return; }
    setBusy(true); onBusy?.(true); setError(""); setResult(null);
    try {
      const next = await api.uploadTestRun(file, testName, requestKey.current, candidateConfiguration);
      setResult(next);
      requestKey.current = null;
      if (!next.rejected) onCreated(next);
    } catch (err) { setError(testRunError(err)); }
    finally { setBusy(false); onBusy?.(false); }
  }
  return (
    <section className="panel upload-panel">
      <div className="section-kicker"><Icon name="upload" size={22} /><span>{w("배치 파일 분석", "Batch File Analysis")}</span></div>
      <label className="test-name-field">{w("테스트명", "Test name")} <input value={name} maxLength={120} disabled={busy} onChange={event => { setName(event.target.value); requestKey.current = null; setResult(null); }} placeholder={w("비워두면 ID 자동 생성", "Leave blank for an automatic ID")} /></label>
      <div className="ux-toolbar"><span className="ux-muted">{w("UTF-8 CSV / JSON · 입력 스키마 기준", "UTF-8 CSV / JSON · Input Schema")}</span><span className="ux-muted">{w("답안 자동 비교", "Reference comparison")}<HelpTooltip label={w("답안 자동 비교", "Reference comparison")}>expected_verdict에 true_positive, false_positive, inconclusive 중 하나를 넣으면 분석 뒤 비교합니다. null·CSV 빈칸은 답안 없음입니다. difficulty·test_category·case_name은 난이도·유형·문항명입니다. 이 정보와 답안은 모델에 보내지 않습니다. 기대 답안과의 일치가 운영 정확도를 보장하지는 않습니다.</HelpTooltip></span></div>
      <label className="dropzone">
        <div className="upload-icon"><Icon name="file" size={28} /></div>
        <strong>{file ? file.name : w("파일을 선택하세요", "Choose a file")}</strong>
        <span>{w("CSV 또는 JSON · 최대 10 MiB · 테스트로 분류", "CSV or JSON · Up to 10 MiB · Test only")}</span>
        <input type="file" accept=".csv,.json,application/json,text/csv" disabled={busy} onChange={(e) => { setFile(e.target.files?.[0] || null); setResult(null); setError(""); requestKey.current = null; }} />
      </label>
      <button className="primary" onClick={upload} disabled={!file || busy || Boolean(result) || Boolean(disabledReason)}>{busy ? w("접수 중…", "Submitting…") : w("배치 분석 시작", "Start batch analysis")}</button>
      {error && <div className="error" role="alert">{error}</div>}
      {result && <div className="notice" aria-live="polite">신규 {result.accepted}건 · 중복 {result.duplicates}건 · 거부 {result.rejected}건</div>}
      {uploadLabelNotice(result) && <p role="status">{uploadLabelNotice(result)}</p>}
      {!!result?.errors?.length && <div className="upload-errors"><h3>{w("접수하지 못한 행", "Rejected rows")}</h3><p className="muted">오류를 수정한 뒤 다시 접수하세요. 기존 답안을 수정하려면 분석 결과의 참고 답안 연결에서 미리보기·확정하세요. 오류 상세는 최대 100건 표시합니다.</p><div className="table-wrap"><Table><thead><tr><th>{w("행 번호", "Row")}</th><th>{w("오류 필드 / 코드", "Field / error code")}</th></tr></thead><tbody>{result.errors.map((item, index) => <tr key={index}><td>{item.row ?? "—"}</td><td><code>{expectedVerdictUploadError(item) || uploadErrorText(item)}</code></td></tr>)}</tbody></Table></div></div>}
      {result && <div className="action-row"><button type="button" className="primary" onClick={() => onCreated(result)}>{w("이 테스트의 진행·평가 보기", "View this Test")}</button><button type="button" className="secondary" onClick={() => { setResult(null); requestKey.current = null; }}>{w("같은 파일로 새 테스트 준비", "Prepare another Test")}</button><button type="button" className="secondary" onClick={onViewTests}>{w("테스트 분석 결과 보기", "View Tests")}</button></div>}
    </section>
  );
}

function SingleTest({ onCreated, disabledReason, candidateConfiguration, onBusy }) {
  const w = useR5Words();
  const [name, setName] = useState(""); const [expectedVerdict, setExpectedVerdict] = useState("");
  const [difficulty, setDifficulty] = useState(""); const [category, setCategory] = useState(""); const requestKey = useRef(null);
  const [form, setForm] = useState(emptySingleTest);
  const [additionalFields, setAdditionalFields] = useState("");
  useEffect(() => { requestKey.current = null; }, [candidateConfiguration]);
  const [extraOpen, setExtraOpen] = useState(false);
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const update = (key) => (event) => { setForm({ ...form, [key]: event.target.value }); requestKey.current = null; };
  async function run(event) {
    event.preventDefault(); if (busy) return; if (disabledReason) { setMessage(disabledReason); return; }
    requestKey.current ||= newTestRequestKey();
    const testName = autoTestName(name, requestKey.current);
    const nameError = validateTestName(testName); if (nameError) { setMessage(nameError); return; }
    let observation = singleTestEvent(form, requestKey.current);
    try { const extra = additionalFields.trim() ? JSON.parse(additionalFields) : {}; if (!extra || Array.isArray(extra) || typeof extra !== "object" || Object.keys(extra).some(key => Object.hasOwn(observation, key))) throw new Error(); observation = { ...observation, ...extra }; }
    catch { setMessage("추가 필드는 JSON 객체로 입력하세요. 기본 필드를 중복해서 넣을 수 없습니다."); return; }
    setMessage("등록 중…"); setBusy(true); onBusy?.(true);
    try {
      const created = await api.createTestRun({ name: testName, idempotency_key: requestKey.current, event: observation, ...(candidateConfiguration ? { candidate_configuration: candidateConfiguration } : {}), ...(expectedVerdict ? { expected_verdict: expectedVerdict } : {}), ...(difficulty.trim() ? { difficulty: difficulty.trim() } : {}), ...(category.trim() ? { test_category: category.trim() } : {}) });
      setMessage("테스트를 접수했습니다.");
      requestKey.current = null; onCreated(created);
    } catch (err) { setMessage(testRunError(err)); }
    finally { setBusy(false); onBusy?.(false); }
  }
  return (
    <form className="panel form-panel" onSubmit={run}>
      <div className="panel-head"><h2><Icon name="test" size={20} />{w("단건 분석", "Single Analysis")}</h2><span className="ux-muted">{w("HTTP 요청 1건", "One HTTP request")}</span></div>
      <fieldset className="test-submission-fields" disabled={busy}>
      <label className="test-name-field">{w("테스트명", "Test name")} <input maxLength={120} value={name} onChange={event => { setName(event.target.value); requestKey.current = null; }} placeholder={w("비워두면 ID 자동 생성", "Leave blank for an automatic ID")} /></label>
      <div className="form-grid"><label>{w("참고 라벨 (선택)", "Reference Label (optional)")}<select value={expectedVerdict} onChange={event => { setExpectedVerdict(event.target.value); requestKey.current = null; }}><option value="">{w("답안 없음", "No reference")}</option>{Object.entries(referenceVerdicts).map(([value, label]) => <option key={value} value={value}>{w(label, { true_positive: "True Positive", false_positive: "False Positive", inconclusive: "Inconclusive" }[value])}</option>)}</select></label><label>{w("난이도 (선택)", "Difficulty (optional)")}<input value={difficulty} maxLength={80} onChange={event => { setDifficulty(event.target.value); requestKey.current = null; }} placeholder={w("예: easy", "e.g. easy")} /></label><label>{w("테스트 유형 (선택)", "Category (optional)")}<input value={category} maxLength={120} onChange={event => { setCategory(event.target.value); requestKey.current = null; }} placeholder={w("예: sql_injection", "e.g. sql_injection")} /></label></div>
      <span className="ux-muted">{w("답안은 분석 후 비교하며 답안·난이도·유형은 모델에 보내지 않습니다.", "Reference labels, difficulty and category are used after analysis and never sent to models.")}</span>
      <div className="form-grid">
        <label>{w("회사명", "Company")}<input value={form.company_name} required onChange={update("company_name")} placeholder={w("예: 사내 서비스", "e.g. internal service")} /></label>
        <label>{w("WAF 벤더", "WAF vendor")}<input value={form.waf_vendor} required onChange={update("waf_vendor")} placeholder={w("예: generic", "e.g. generic")} /></label>
        <label>{w("출발지 IP", "Source IP")}<input value={form.src_ip} required onChange={update("src_ip")} placeholder={w("예: 192.0.2.10", "e.g. 192.0.2.10")} /></label>
        <label>{w("목적지 IP", "Destination IP")}<input value={form.dest_ip} required onChange={update("dest_ip")} placeholder={w("예: 198.51.100.20", "e.g. 198.51.100.20")} /></label>
        <label>{w("WAF 조치", "WAF action")}<select aria-label={w("WAF 조치", "WAF action")} value={form.waf_action} required onChange={update("waf_action")}><option value="" disabled>{w("선택하세요", "Select")}</option><option value="D">{w("차단", "Deny")}</option><option value="A">{w("허용", "Allow")}</option></select></label>
      </div>
      <label>{w("탐지명", "Signature")}<input value={form.signature} onChange={update("signature")} placeholder={w("예: SQL Injection", "e.g. SQL Injection")} /></label>
      <label>{w("HTTP 원문", "Raw HTTP")}<textarea rows="8" required value={form.payload} onChange={update("payload")} placeholder={"GET /search?q=example HTTP/1.1\nHost: example.internal\n\n"} /></label>
      <button type="button" className="text-button" aria-expanded={extraOpen} aria-controls="single-test-extra" onClick={() => setExtraOpen(value => !value)}>{extraOpen ? w("추가 입력 닫기", "Hide optional fields") : w("추가 입력", "Optional fields")}</button>
      <div className="form-grid" id="single-test-extra" hidden={!extraOpen}><label>{w("이벤트 ID", "Event ID")}<input value={form.event_id} onChange={update("event_id")} placeholder={w("비워두면 ID 자동 생성", "Leave blank for an automatic ID")} /></label><label>{w("이벤트명", "Event name")}<input value={form.event_name} onChange={update("event_name")} placeholder={w("예: 검색 요청", "e.g. search request")} /></label><label>{w("출발지 포트", "Source port")}<input type="number" min="0" max="65535" value={form.src_port} onChange={update("src_port")} placeholder={w("예: 42310", "e.g. 42310")} /></label><label>{w("목적지 포트", "Destination port")}<input type="number" min="0" max="65535" value={form.dest_port} onChange={update("dest_port")} placeholder={w("예: 443", "e.g. 443")} /></label></div>
      {extraOpen && <label>{w("스키마 추가 필드 (JSON)", "Additional schema fields (JSON)")}<textarea value={additionalFields} rows={3} placeholder={'{"vendor_score": 2}'} onChange={event => { setAdditionalFields(event.target.value); requestKey.current = null; }} /></label>}
      </fieldset><div className="action-row"><button className="primary" disabled={busy || Boolean(disabledReason)}>{busy ? w("접수 중…", "Submitting…") : w("분석 시작", "Start analysis")}</button><span aria-live="polite">{message}</span></div>
    </form>
  );
}

export function Detail({ id, onBack, onOpen, backLabel = "분석 결과", tab: controlledTab, onTabChange, onResolved }) {
  const { t } = useConsolePreferences();
  const [view, setView] = useState(() => emptyDetailState(id));
  const loader = useRef(null);
  const { detail, error, loading, runs, runsError, runsLoading, labelHistory, labelHistoryError, labelsLoading } = view;
  useEffect(() => { if (detail?.id === id) onResolved?.(detail); }, [detail?.id, detail?.analysis_purpose, detail?.test_run_id, id, onResolved]);
  const [rawRecord, setRawRecord] = useState(null);
  const raw = rawRecord?.id === id ? rawRecord.event : null;
  const [rawError, setRawError] = useState("");
  const [rawAttempt, setRawAttempt] = useState(0);
  const [localTab, setLocalTab] = useState("result");
  const tab = controlledTab ?? localTab;
  const setTab = onTabChange || setLocalTab;
  const [reportMode, setReportMode] = useState("preview");
  const [technicalOpen, setTechnicalOpen] = useState(false);
  const [inputTarget, setInputTarget] = useState(null);
  const [reportAppendix, setReportAppendix] = useState(false);
  const openInput = item => { setInputTarget({ field: item.field, excerpt: item.excerpt }); setTab("raw"); };
  useEffect(() => {
    const current = createDetailLoader({ id, api, onChange: setView });
    loader.current = current;
    setRawRecord(null); setRawError(""); setInputTarget(null); setTechnicalOpen(false); setReportAppendix(false);
    current.start();
    return () => { current.dispose(); loader.current = null; };
  }, [id]);
  useEffect(() => { loader.current?.setAgentVisible(tab === "agent"); }, [id, tab]);
  useEffect(() => {
    if (tab !== "raw" || raw) return;
    let active = true;
    const controller = new AbortController();
    setRawError("");
    api.rawEvent(id, { signal: controller.signal }).then(value => { if (active) setRawRecord({ id, event: value }); }).catch(() => { if (active) setRawError("unavailable"); });
    return () => { active = false; controller.abort(); };
  }, [id, raw, tab, rawAttempt]);
  const navigation = <div className="panel-head-inline inference-toolbar">
    <button className="back" onClick={onBack}>← {backLabel}</button>
    <div className="ux-toolbar">
      {detail && view.id === id && <RetryAnalysis key={id} detail={detail} onOpen={onOpen} />}
      {detail && view.id === id && <AnalysisDownloads id={id} status={detail.status} includeAppendix={reportAppendix} includeDecoding={Boolean(raw?.decoding)} />}
      <button type="button" className="icon-button" aria-label={t("detail.refresh")} title={t("detail.refresh")} disabled={loading || labelsLoading || runsLoading} onClick={() => loader.current?.refresh()}><Icon name="refresh" size={18} /></button>
    </div>
  </div>;
  if (!detail || view.id !== id) return <div className="page-stack">{navigation}{error ? <div className="error" role="alert">{error}</div> : <div className="loading">{t("loading")}</div>}</div>;
  const notices = analysisNotices(detail);
  const decisionReason = isMockAnalysis(detail) ? null : decisionExplanation(detail);
  const rawFailure = rawError && <div className="error" role="alert"><p>{t("detail.rawError")}</p><button type="button" className="secondary" onClick={() => setRawAttempt(value => value + 1)}>{t("retry")}</button></div>;
  const tabItems = ["result", "agent", "raw", "json", "report"].map(key => [key, t(`detail.tab.${key}`)]);
  return <div className="page-stack inference-detail">
    {navigation}
    {error && <div className="error" role="alert">{error}</div>}
    <InferenceSummary detail={detail} runs={runs} onMetadata={() => setTechnicalOpen(true)}>
      <h2>{t(isMockAnalysis(detail) ? "detail.mock" : "detail.summary")}</h2>
      <div className="snapshot-highlight" data-verdict={detail.status === "completed" ? finalValue(detail, "verdict") : detail.status}>
        <div className="snapshot-verdict"><span className="snapshot-mark"><Icon name={detail.status === "failed" ? "close" : "shield"} size={25} /></span><div><small>{t("detail.verdict")}</small>{detail.status === "completed" ? <Status value={finalValue(detail, "verdict")} /> : <Status value={detail.status} />}</div>{detail.status === "completed" && <Severity value={detail.result?.threat_analysis?.severity} describe />}</div>
        <div className="snapshot-summary"><SummaryPreview text={analystSummary(detail)} /></div>
      </div>
      {decisionReason && <small className="snapshot-reason">{decisionReason.title_ko}</small>}
    </InferenceSummary>
    <DetailTabs label={t("detail")} items={tabItems} value={tab} onChange={setTab} focusRequest={inputTarget}>{key => {
      if (key === "result") return tab === "result" && <div className="inference-result-content">
        <InitialAssessment value={detail.initial_assessment} verdict={finalValue(detail, "verdict")} confidence={finalValue(detail, "confidence_score")} />
        <section className={`panel decision-card decision-${detail.status === "completed" ? finalValue(detail, "verdict") : "pending"}`}>
          <h2>{t("detail.summary")}</h2><div className="decision-summary-line"><strong>{analystSummary(detail)}</strong></div>
          {!isMockAnalysis(detail) && <DecisionIssues detail={detail} />}
          {notices.map(notice => <small className="analyst-notice" key={notice}>{notice}</small>)}
        </section>
        {detail.status === "completed" && detail.result && <ResultView detail={detail} onViewInput={openInput} />}
        <section className="inference-evaluation">
          <div className="inference-reference-actions"><h2>{t("detail.reference")}</h2><AnalysisSelectionActions allowDataset={detail.analysis_purpose !== "test"} key={id} ids={[id]} single compact onSaved={() => loader.current?.refresh()} /></div>
          <CompactEvaluationDetail detail={detail} history={labelHistory} historyError={labelHistoryError} />
          {detail.evaluation?.outcome === "unlabeled" && !labelHistoryError && <p className="ux-muted">등록된 참고 답안이 없습니다.</p>}
        </section>
        <AdditionalChecks detail={detail} />
      </div>;
      if (key === "agent") return <section className="agent-trace-panel">
        <p className="ux-muted trace-audit">{t("detail.audit")}</p>
        {runsError && <div className="error" role="alert"><p>{t("detail.agentError")}</p><button type="button" className="secondary" disabled={runsLoading} onClick={() => loader.current?.refreshAgent()}>{t("retry")}</button></div>}
        {runs === null ? (!runsError && <p className="loading" role="status">{t("loading")}</p>) : <AgentHistory runs={runs} active={tab === "agent"} />}
      </section>;
      if (key === "raw") return rawFailure || (raw ? <RawEventView key={id} event={raw} detail={detail} target={inputTarget} active={tab === "raw"} /> : <p className="panel loading" role="status">{t("loading")}</p>);
      if (key === "json") return tab === "json" && <section className="panel detail-body"><h2>{t("detail.tab.json")}</h2><p className="ux-muted">{t("detail.jsonNote")}</p><TextInspector label={t("detail.tab.json")} value={detail.result} /></section>;
      return tab === "report" && <AnalysisReport detail={detail} decoding={raw?.decoding ?? null} mode={reportMode} onModeChange={setReportMode} includeAppendix={reportAppendix} onAppendixChange={setReportAppendix} />;
    }}</DetailTabs>
    <Dialog open={technicalOpen} title={t("detail.metadata")} className="inspection-dialog" onClose={() => setTechnicalOpen(false)}>{technicalOpen && <div className="analyst-disclosure-body">
      <section className="detail-summary">
        <div><span>회사</span><strong>{detail.company_name || t("detail.missing")}</strong></div>
        <div><span>출발지 → 목적지</span><strong>{detail.src_ip || "—"}{detail.src_port != null && `:${detail.src_port}`} → {detail.dest_ip || "—"}{detail.dest_port != null && `:${detail.dest_port}`}</strong></div>
        <div><span>연동 시스템</span><strong>{detail.source_system}</strong></div>
        <div><span>접수 경로</span><strong>{channelLabels[detail.ingest_channel] || "기존 미분류"}</strong></div>
        <div><span>분석가 검토</span><strong>{labels[detail.review_state] || "미기록"}</strong></div>
        <div><span>판정 점수<HelpTooltip label="판정 점수">모델의 자기평가에 최종 판정 정책을 적용한 참고값입니다. 실제 정탐 확률이나 정확도가 아닙니다.</HelpTooltip></span><strong>{finalValue(detail, "confidence_score") ?? "미기록"}</strong></div>
        <div><span>이벤트 ID</span><strong>{detail.event_id}</strong></div>
        <div><span>분석 ID</span><strong>{detail.id}</strong></div>
        <div><span>탐지명</span><strong>{detail.signature || "미기록"}</strong></div>
        <div><span>WAF 조치 / 벤더</span><strong>{detail.waf_action === "D" ? "차단" : detail.waf_action === "A" ? "허용" : "미기록"}</strong><small>{detail.waf_vendor || "미기록"}</small></div>
      </section>
      {detail.error_code && <p className="error">실패 코드: {detail.error_code}</p>}
      <p className="ux-muted">처리 시간은 처리 시작부터 종료까지이며 재시도·복구 대기를 포함합니다. 단계별 교정 횟수는 Agent 실행 이력에서 확인합니다.</p>
      <p>추가 검증: {detail.result?.verifier?.executed === true ? "실행됨" : detail.result?.verifier?.executed === false ? "실행하지 않음" : "실행 기록 없음"}</p>
      <InputSchemaMetadata metadata={detail.input_schema_metadata} />
    </div>}</Dialog>
  </div>;
}

export function ResultView({ detail, onViewInput }) {
  const result = detail.result;
  if (!result || detail.status !== "completed") return <section className="panel detail-body"><h2>분석 상태</h2><p>{analystSummary(detail)}</p></section>;
  const threat = result.threat_analysis;
  const signature = result.signature_assessment;
  const tuning = result.tuning_recommendation;
  const guidance = analystGuidance(detail);
  const evidence = groupedEvidence(result.evidence);
  const circumstances = analystItems(result.conflicting_evidence);
  const obfuscations = analystItems(threat?.obfuscations);
  const undecided = finalValue(detail, "verdict") === "inconclusive";
  return (
    <div className="result-stack">
      <section className="panel result-card detailed-analysis">
        <h2>세부 분석</h2>
        {undecided && <p className="analyst-notice">{provisionalAnalysisNotice}</p>}
        {threat ? <dl><dt>{threatCategoryLabel(finalValue(detail, "verdict"))}</dt><dd>{analystText(threat.category)}</dd><dt>분석 위치</dt><dd>{analystFieldLabel(analystText(threat.target))}</dd><dt>분석 내용</dt><dd>{analystText(threat.technique_ko, "저장된 설명은 기술정보에서 확인할 수 있습니다.")}</dd><dt>예상 영향</dt><dd>{analystText(threat.potential_impact_ko)}</dd>{!!obfuscations.length && <><dt>인코딩·난독화</dt><dd>{obfuscations.join(", ")}</dd></>}</dl> : <p>세부 분석 내용이 기록되지 않았습니다.</p>}
        {signature && <div className="signature-context"><h3>탐지 내용과 요청의 연관성</h3><Status value={signature.relation} /><p>{analystText(signature.explanation_ko)}</p></div>}
      </section>
      {assessmentView(result) ? <AnalystEvidence result={result} onViewInput={onViewInput} /> : <section className="panel result-card evidence-card">
        <h2>판정 근거</h2>
        {!!evidence.length && <p className="muted">{legacyEvidenceNotice}</p>}
        <div className="evidence-list">
          {evidence.map((item, index) => <article key={index}>
            <div className="evidence-heading"><span>근거 {index + 1}</span><strong>{analystFieldLabel(item.field)}</strong>{typeof item.field === "string" && analystFieldLabel(item.field) !== item.field && <small className="evidence-field-path">{item.field}</small>}</div>
            <div className="evidence-section"><span>원문 발췌</span><code>{typeof item.excerpt === "string" ? item.excerpt : "발췌문 미기록"}</code></div>
            {onViewInput && typeof item.field === "string" && typeof item.excerpt === "string" && item.excerpt && <button type="button" className="text-button evidence-input-link" onClick={() => onViewInput(item)}>입력에서 보기</button>}
            <div className="evidence-section evidence-interpretation"><span>분석 내용</span>{item.interpretations.map((interpretation, interpretationIndex) => <p key={interpretationIndex}>{analystText(interpretation, "저장된 설명은 기술정보에서 확인할 수 있습니다.")}</p>)}</div>
          </article>)}
          {!evidence.length && <p>{decisionExplanation(detail)?.code === "evidence_unverified" ? "원문 대조를 통과한 판정 근거가 남아 있지 않습니다. HTTP 원문에서 직접 확인해 주세요." : "저장된 판정 근거가 없습니다. 이것만으로 공격이 없다고 볼 수는 없습니다."}</p>}
        </div>
      </section>}
      {!!circumstances.length && <TextList title="함께 고려할 정황" items={circumstances} />}
      {!!guidance.limitations.length && <TextList title="해석 시 주의할 점" items={guidance.limitations} />}
      {hasTuningContent(tuning) && <section className="panel result-card tuning-card">
        <div className="panel-head-inline"><h2>WAF 정책 검토</h2><span className="status purpose-test">{tuning.recommended ? "검토 제안" : "주의사항"}</span></div>
        <dl>{[["범위", tuning.scope], ["제안", tuning.proposal_ko], ["변경 시 주의사항", tuning.risk_ko], ["적용 전 확인", tuning.validation_ko]].filter(([, value]) => analystText(value, "")).map(([label, value]) => <div className="definition-row" key={label}><dt>{label}</dt><dd>{analystText(value)}</dd></div>)}</dl><p className="muted">WAF 설정은 자동 변경하지 않습니다.</p>
      </section>}
    </div>
  );
}

function AdditionalChecks({ detail }) {
  const followUp = analystFollowUp(detail);
  if (!followUp.visible) return null;
  return <section className="panel result-card analyst-checks"><h2>추가 확인 사항</h2><p className="muted">{followUp.introduction_ko}</p>{followUp.checks.length ? <ol>{followUp.checks.map((check, index) => <li key={index}><span className="check-source">{check.source_ko}</span><h3>{check.check_ko}</h3><p>{check.why_ko}</p></li>)}</ol> : <p>{followUp.empty_ko}</p>}</section>;
}

function TextList({ title, items = [], empty }) {
  return <section className="panel result-card"><h2>{title}</h2>{items.length ? <ul>{items.map((item, index) => <li key={index}>{item}</li>)}</ul> : <p>{empty}</p>}</section>;
}

export function Settings({ onProductionChange, onViewDataset, tab: controlledTab, onTabChange, standalone = false }) {
  const [localTab, setLocalTab] = useState("models");
  const tab = controlledTab ?? localTab;
  const setTab = onTabChange || setLocalTab;
  return <div className="page-stack">{!standalone && <div className="tabs" role="tablist" aria-label="설정 항목">
    <button type="button" role="tab" aria-selected={tab === "models"} onClick={() => setTab("models")}>LLM 프로필</button>
    <button type="button" role="tab" aria-selected={tab === "agents"} onClick={() => setTab("agents")}>Agent 설정</button>
    <button type="button" role="tab" aria-selected={tab === "schema"} onClick={() => setTab("schema")}>입력 스키마</button>
    <button type="button" role="tab" aria-selected={tab === "egress"} onClick={() => setTab("egress")}>내부 연결 허용</button>
    <button type="button" role="tab" aria-selected={tab === "keys"} onClick={() => setTab("keys")}>서비스 API Key</button>
  </div>}{tab === "models" ? <ModelSettings onProductionChange={onProductionChange} onConfigureAgents={() => setTab("agents")} onInternalEgress={() => setTab("egress")} onViewDataset={onViewDataset} /> : ["agents", "prompts"].includes(tab) ? <ReadOnlyAgentRoles /> : tab === "instructions" ? <PromptSettings /> : tab === "concurrency" ? <ConcurrencySettings /> : tab === "schema" ? <InputSchemaSettings /> : tab === "egress" ? <InternalEgressSettings /> : <ServiceApiKeys />}</div>;
}

export function ModelSettings({ onProductionChange, onInternalEgress, onViewDataset, onConfigureAgents }) {
  const w = useR5Words();
  const [formOpen, setFormOpen] = useState(false);
  const [managedId, setManagedId] = useState(null);
  const [profiles, setProfiles] = useState([]);
  const [profilesLoading, setProfilesLoading] = useState(true); const [profilesError, setProfilesError] = useState(""); const profileRead = useRef(0);
  const [assignment, setAssignment] = useState(emptyAssignmentState); const assignmentController = useRef(null);
  const [tests, setTests] = useState({});
  const [registrySearch, setRegistrySearch] = useState(""), [registryProvider, setRegistryProvider] = useState(""), [registryValidation, setRegistryValidation] = useState("");
  const filteredProfiles = profiles.filter(profile => `${profile.name} ${profile.model_name}`.toLowerCase().includes(registrySearch.trim().toLowerCase())
    && (!registryProvider || providerOf(profile) === registryProvider)
    && (!registryValidation || (registryValidation === "running" ? ["pending", "running"].includes(tests[profile.id]?.[0]?.status) : (tests[profile.id]?.[0]?.status || "unverified") === registryValidation)));
  const [form, setForm] = useState(newProfileForm);
  const [editingProfile, setEditingProfile] = useState(null);
  const [busy, setBusy] = useState("");
  const [message, setMessage] = useState("");
  const [internalTargets, setInternalTargets] = useState({ items: null, loading: true, error: "" });
  const targetRequest = useRef(null);
  const [fullValidation, setFullValidation] = useState(emptyFullValidationState);
  const [quickValidation, setQuickValidation] = useState({ open: false, profile: null });
  const fullValidationController = useRef(null);
  const editingId = editingProfile?.id;
  const editingAssigned = roleAssigned(profiles.find(profile => profile.id === editingId));
  const openai = form.provider === "openai";
  const targetError = openai ? "" : vllmTargetError(form.base_url, internalTargets);
  const keyRequired = profileRequiresKey(form, editingProfile);
  const update = (key) => (event) => setForm({ ...form, [key]: event.target.type === "checkbox" ? event.target.checked : event.target.value });
  const changeProvider = (event) => {
    setForm(changeProfileProvider(form, event.target.value));
    setMessage("공급자를 변경하면 주소·모델·입력한 API Key·외부 전송 승인을 초기화합니다. 기존 키를 자동 재사용하지 않습니다.");
  };

  const loadProfiles = useCallback(async () => {
    const sequence = ++profileRead.current;
    try {
      const next = await api.modelProfiles();
      if (sequence !== profileRead.current) return;
      if (!Array.isArray(next)) throw new Error("invalid_profiles");
      setProfiles(next);
      setProfilesError(""); setProfilesLoading(false);
      onProductionChange?.(next.find((profile) => profile.status === "production")?.name || "미설정");
      const histories = await Promise.all(next.map(async (profile) => [profile.id, await api.modelProfileTests(profile.id)]));
      if (sequence === profileRead.current) setTests(Object.fromEntries(histories));
      return { active: histories.some(([, items]) => items.some(test => ["pending", "running"].includes(test.status) || ["waiting", "running"].includes(test.dataset_evaluation?.status))) };
    } catch (err) { if (sequence === profileRead.current) { setProfilesLoading(false); setProfilesError("프로필 또는 검증 이력을 조회하지 못했습니다."); } return { error: true }; }
  }, [onProductionChange]);

  useEffect(() => {
    const controller = createAssignmentController({ api, onChange: next => { setAssignment(next); setBusy(next.busy ? "assignment" : ""); }, onCommitted: target => {
      setMessage(target.action === "disable" ? `${target.name}을 비활성화하고 해당 프로필의 Production·Test 지정을 해제했습니다.` : target.action === "unassign_test" ? `${target.name}의 Test 지정만 해제했습니다. Production은 변경하지 않았습니다.` : `${target.name}을 ${target.action === "test" ? "Test" : "Production"}로 지정했습니다. 다른 용도의 지정은 변경하지 않았습니다.`);
      void loadProfiles();
    }, onRefresh: () => { void loadProfiles(); } });
    assignmentController.current = controller;
    return () => { controller.dispose(); if (assignmentController.current === controller) assignmentController.current = null; };
  }, [loadProfiles]);

  useEffect(() => {
    const instance = createFullValidationController({ api, onChange: next => { setFullValidation(next); setBusy(next.busy ? `f-${next.profile?.id}` : ""); }, onSubmitted: (_result, profile, includeDataset) => {
      setMessage(`${profile.name}의 연결·기능 검증${includeDataset ? " 및 150건 판정 평가" : ""}를 접수했습니다. Production·Test 지정은 변경하지 않았습니다.`);
      void loadProfiles();
    }, onRequireRefresh: () => { void loadProfiles(); } });
    fullValidationController.current = instance;
    return () => { instance.dispose(); if (fullValidationController.current === instance) fullValidationController.current = null; };
  }, [loadProfiles]);

  const loadTargets = useCallback(async () => {
    targetRequest.current?.abort();
    const request = new AbortController(); targetRequest.current = request;
    setInternalTargets((previous) => ({ ...previous, loading: true, error: "" }));
    try {
      const items = await api.internalEgress({ signal: request.signal });
      if (!Array.isArray(items)) throw new Error("invalid_internal_egress_response");
      if (!request.signal.aborted) setInternalTargets({ items, loading: false, error: "" });
    } catch (error) {
      if (!request.signal.aborted) setInternalTargets({ items: null, loading: false, error: internalEgressError(error) });
    }
  }, []);

  useEffect(() => {
    void loadTargets();
    return () => targetRequest.current?.abort();
  }, [loadTargets]);

  useEffect(() => {
    const stop = startVisiblePolling(async () => {
      const result = await loadProfiles();
      if (result?.error) throw new Error("profile_lookup_failed");
      return result?.active;
    });
    return () => { stop(); profileRead.current += 1; };
  }, [loadProfiles]);

  function edit(profile) {
    if (roleAssigned(profile)) { setMessage("Agent에 배정된 프로필은 수정할 수 없습니다. Agent 설정에서 배정을 변경하세요."); return; }
    setEditingProfile({ id: profile.id, provider: providerOf(profile), has_api_key: profile.has_api_key });
    setManagedId(null); setFormOpen(true);
    setForm(editProfileForm(profile));
    setMessage("같은 공급자에서 API Key를 비워두면 기존 값을 유지합니다. 공급자를 바꾸면 키를 자동 재사용하지 않습니다.");
  }

  function resetForm() {
    setEditingProfile(null);
    setForm(newProfileForm());
  }

  async function save(event) {
    event.preventDefault();
    if (editingAssigned || profilesError) { setMessage(editingAssigned ? "편집 중 프로필이 Production 또는 Test로 지정되었습니다. 지정 해제 후 다시 편집하세요." : "최신 프로필 상태를 확인한 뒤 다시 저장하세요."); return; }
    const error = validateProfileForm(form, editingProfile, internalTargets);
    if (error) { setMessage(error); return; }
    setBusy("save"); setMessage("");
    const payload = profilePayload(form, editingProfile);
    try {
      if (editingId) await api.updateModelProfile(editingId, payload);
      else await api.createModelProfile(payload);
      setMessage(editingId ? "프로필을 수정했습니다. 다시 전체 검증이 필요합니다. 모델은 호출하지 않았습니다." : "미검증 프로필을 등록했습니다. 모델은 호출하지 않았습니다.");
      resetForm();
      setFormOpen(false);
      await loadProfiles();
    } catch (err) { setMessage(modelProfileError(err)); if (!openai) void loadTargets(); }
    finally { setBusy(""); }
  }

  async function act(key, action) {
    setBusy(key); setMessage("");
    try { await action(); await loadProfiles(); }
    catch (err) { setMessage(modelProfileError(err)); }
    finally { setBusy(""); }
  }

  function runTest(profile, mode) {
    if (mode === "full") { fullValidationController.current?.open(profile); return; }
    setQuickValidation({ open: true, profile });
  }

  const openAssignment = (profile, action) => assignmentController.current?.open(profile, action, profiles);

  return (
    <div className="page-stack">
      <FullValidationDialog state={fullValidation} controller={fullValidationController.current} />
      <QuickValidationDialog key={`${quickValidation.profile?.id}-${quickValidation.profile?.profile_fingerprint}`} profile={quickValidation.profile} open={quickValidation.open} onClose={() => setQuickValidation(value => ({ ...value, open: false }))} onSubmitted={() => { setQuickValidation(value => ({ ...value, open: false })); setMessage("빠른 테스트를 접수했습니다. 모델 지정은 변경하지 않았습니다."); void loadProfiles(); }} />
      <ModelAssignmentDialog state={assignment} controller={assignmentController.current} />
      <div className="workspace-context"><p className="ux-muted">{w("여기서 모델 연결을 검증합니다. 운영 배정은 공식 테스트와 운영 반영 검토를 거칩니다.", "Validate model connections here. Production assignments require an Official Test and Production Review.")}</p><NavigationAction href="#connect/vllm-targets">{w("vLLM 연결", "vLLM Targets")}</NavigationAction><NavigationAction disabled={Boolean(busy)} onClick={onConfigureAgents}>{w("Agent 역할", "Agent Roles")}</NavigationAction></div>
      {message && <p className="notice" role="status">{message}</p>}
      <section className="panel profile-section">
        <div className="panel-head"><div><h2>모델 목록</h2><p className="ux-muted">현재 설정으로 전체 검증을 통과해야 운영·테스트에 지정할 수 있습니다.</p></div><div className="ux-toolbar"><button type="button" className="primary" disabled={Boolean(busy)} onClick={() => { if (editingProfile) resetForm(); setFormOpen(true); }}>모델 추가</button><button type="button" className="secondary" disabled={Boolean(busy)} onClick={loadProfiles}>새로고침</button></div></div>
        {profilesError && <p className="error" role="alert">{profilesError} 최신 상태를 확인하기 전에는 지정할 수 없습니다.</p>}
        <RegistryFilters search={registrySearch} onSearch={setRegistrySearch} placeholder="프로필명 · 모델명" summary={profilesLoading ? "조회 중…" : `${filteredProfiles.length} / ${profiles.length}개 프로필`} filters={[
          {label:"공급자 필터", value:registryProvider, onChange:setRegistryProvider, options:[["", "모든 공급자"], ["vllm", "vLLM"], ["openai", "OpenAI"]]},
          {label:"최근 검증 필터", value:registryValidation, onChange:setRegistryValidation, options:[["", "모든 상태"], ["passed", "통과"], ["failed", "실패"], ["running", "진행 중"], ["unverified", "기록 없음"]]},
        ]} />
        <DataTable label="LLM 프로필" columns={[
            { id: "name", header: "프로필", width: "17%", render: row => row.cells[0] },
            { id: "model", header: "공급자 / 모델", width: "22%", render: row => row.cells[1] },
            { id: "context", header: "컨텍스트", width: 88, className: "profile-context", render: row => <span title="프로필에 설정한 입력·출력 합산 토큰 한도">{Number.isSafeInteger(row.context) ? row.context.toLocaleString() : "—"}</span> },
            { id: "roles", header: "사용 상태", width: "17%", render: row => row.cells[3] },
            { id: "check", header: "최근 검증", width: "18%", render: row => row.cells[2] },
            { id: "actions", header: "검증 / 관리", width: 228, render: row => row.cells[4] }
          ]} data={filteredProfiles.map(profile => {
                const latest = tests[profile.id]?.[0];
                const active = ["pending", "running"].includes(latest?.status) || ["waiting", "running"].includes(latest?.dataset_evaluation?.status);
                const internalTargetIssue = providerOf(profile) === "vllm" ? vllmTargetError(profile.base_url, internalTargets) : "";
                const assignmentIssue = assignmentBlockReason(profile, { loading: profilesLoading, error: profilesError, targetError: internalTargetIssue, active });
                const validationBlocked = Boolean(busy) || active || profile.status === "disabled" || Boolean(internalTargetIssue) || (providerOf(profile) === "openai" && !profile.external_data_approved);
                return { id: profile.id, context: profile.context_window, cells: [
                  <><strong>{profile.name}</strong>{internalTargetIssue && <small className="error">연결 허용 확인 필요</small>}{providerOf(profile) === "openai" && !profile.external_data_approved && <small className="error">외부 전송 미승인</small>}</>,
                  <><span>{profile.model_name}</span><span className={`provider-badge provider-${providerOf(profile)}`}>{providerLabel(profile)}</span></>,
                  <>{latest ? <><Status value={latest.status} /><small>{latest.mode} · {latest.completed_at ? new Date(latest.completed_at).toLocaleString("ko-KR") : "진행 중"}</small></> : <span>-</span>}</>,
                  <><div className="profile-role-badges">{profile.status === "production" && <span className="status status-production">Production Primary</span>}{profile.is_test && <span className="status purpose-test">Test Primary</span>}{profile.agent_roles?.map(role => <span className="status" key={role}>{role.startsWith("test") ? "Test" : "Production"} {role.endsWith(".evidence_editor") ? "근거 정리" : "Verifier"}</span>)}</div>{profile.status !== "production" && <Status value={profile.status} />}</>,
                  <><div className="profile-row-actions"><button type="button" className="secondary small" disabled={validationBlocked} onClick={() => runTest(profile, "quick")}>빠른 테스트</button><button type="button" className="secondary small" disabled={validationBlocked} onClick={() => runTest(profile, "full")}>전체 검증</button><MoreActions label={`${profile.name} 관리`}><button type="button" onClick={() => setManagedId(profile.id)}>검증 결과·연결정보</button><button type="button" disabled={Boolean(busy) || roleAssigned(profile) || Boolean(profilesError)} onClick={() => edit(profile)}>편집</button></MoreActions></div><Dialog open={managedId === profile.id} title={`${profile.name} · 모델 관리`} onClose={() => { if (!busy) setManagedId(null); }}><section className="detail-summary"><div><span>연결 주소</span><strong>{profile.base_url}</strong></div><div><span>입력 한도 / 최대 출력</span><strong>{profile.context_window.toLocaleString()} / {profile.max_output_tokens} 토큰</strong></div><div><span>제한 시간 / 검증 동시 요청</span><strong>{profile.timeout_seconds}초 / {profile.test_concurrency}건</strong></div><div><span>API Key</span><strong>{profile.has_api_key ? "저장됨" : "없음"}</strong></div></section><div className="profile-actions">
                    <button className="secondary small" disabled={Boolean(busy) || roleAssigned(profile) || Boolean(profilesError)} title={roleAssigned(profile) ? "Agent 배정 중에는 수정할 수 없습니다." : undefined} onClick={() => edit(profile)}>편집</button>
                    <button className="secondary small" disabled={Boolean(busy)} onClick={onConfigureAgents}>Agent 모델 배정</button>
                    {profile.status === "disabled"
                      ? <button className="secondary small" disabled={Boolean(busy) || Boolean(internalTargetIssue)} onClick={() => act(`e-${profile.id}`, () => api.enableModelProfile(profile.id))}>활성화</button>
                      : <button className="secondary small" disabled={Boolean(busy) || Boolean(profilesError) || profile.status === "production" || Boolean(profile.agent_roles?.length)} onClick={() => openAssignment(profile, "disable")}>비활성화</button>}
                  </div>{assignmentIssue && <p className="profile-assignment-reason">지정 불가: {assignmentIssue}</p>}{roleAssigned(profile) && <p className="profile-assignment-reason">사용 중인 프로필은 편집할 수 없습니다. 새 프로필을 검증한 뒤 기본 테스트 설정에서 선택하세요. 운영 모델 교체에는 공식 테스트와 운영 반영 검토가 필요합니다.</p>}{message && <p className="notice" role="status">{message}</p>}{managedId === profile.id && latest && <TestResult profile={profile} test={latest} onViewDataset={onViewDataset} />}</Dialog></>
                ] };
          })} empty={profilesLoading ? "모델을 불러오는 중…" : profilesError ? "모델 목록을 확인할 수 없습니다." : profiles.length ? "검색 조건에 맞는 모델이 없습니다." : "등록된 모델이 없습니다."} />
      </section>

      <Dialog open={formOpen} title={editingId ? "모델 수정" : "모델 추가"} onClose={() => { if (!busy) setFormOpen(false); }}>
      <form className="panel form-panel profile-form" onSubmit={save}>
        <div className="panel-head"><p className="ux-muted">{w("등록·수정만으로 모델을 호출하지 않습니다. 검증과 운영 반영은 별도 작업입니다.", "Saving a profile does not call the model. Validation and applying to Production are separate actions.")}</p>{editingId && <button type="button" className="secondary small" disabled={Boolean(busy)} onClick={resetForm}>취소</button>}</div>
        {editingAssigned && <p className="error" role="alert">편집 중인 프로필이 Production 또는 Test로 지정되었습니다. 지정 중에는 수정할 수 없습니다.</p>}
        {message && <p className="notice" role="status">{message}</p>}
        <fieldset className="profile-fields" disabled={Boolean(busy) || editingAssigned}>
        <div className="form-grid">
          <label>LLM 공급자<select value={form.provider} onChange={changeProvider}><option value="vllm">vLLM</option><option value="openai">OpenAI · 외부 API</option></select></label>
          <label>프로필 이름<input value={form.name} onChange={update("name")} required maxLength={120} placeholder="llm-candidate" /></label>
          <label>Base URL<input value={form.base_url} onChange={update("base_url")} required maxLength={500} readOnly={openai} placeholder={openai ? undefined : "http://10.0.0.10:8000/v1"} aria-describedby="profile-provider-note" /></label>
          <label>모델 이름<input value={form.model_name} onChange={update("model_name")} required maxLength={255} placeholder={openai ? "사용할 OpenAI 모델 ID" : "vLLM에 등록된 모델 ID"} /></label>
          <label>API Key{keyRequired ? " (필수)" : ""}<input type="password" value={form.api_key} onChange={update("api_key")} required={keyRequired} maxLength={4096} placeholder={keyRequired ? "새 API Key 입력" : editingId && !form.key_reset_required ? "비워두면 기존 키 유지" : "필요한 경우 입력"} autoComplete="off" spellCheck={false} /></label>
          <label>제한 시간 (초)<input type="number" min="5" max="600" value={form.timeout_seconds} onChange={update("timeout_seconds")} /></label>
          <label htmlFor="model-context-window"><span>입력·출력 토큰 한도<HelpTooltip label="토큰 한도">모델이 한 번에 처리할 수 있는 전체 길이입니다. 실제 서버 설정과 모델 지원 범위에 맞게 입력하세요.</HelpTooltip></span><input id="model-context-window" type="number" min="4096" max="131072" value={form.context_window} onChange={update("context_window")} /></label>
          <label>최대 출력 토큰<input type="number" min="256" max="16384" value={form.max_output_tokens} onChange={update("max_output_tokens")} /></label>
          <label>검증 동시 요청<input type="number" min="1" max="10" value={form.test_concurrency} onChange={update("test_concurrency")} /><small className="ux-muted">전체 검증에서 시험할 요청 수입니다. Agent 설정의 서버별 호출 상한 이하여야 합니다.</small></label>
        </div>
        <p className="form-note" id="profile-provider-note">{openai ? "OpenAI 공식 주소로만 연결하며 TLS 인증서를 검증합니다. 모델 ID와 Context window·출력 토큰은 사용할 모델의 지원 범위에 맞게 입력하세요. 모델 가용성은 검증 전까지 확인되지 않습니다." : "vLLM은 내부 연결 허용에 등록한 내부 IP와 포트만 사용할 수 있습니다. 기존 vllm.internal 같은 hostname은 실제 내부 IP로 변경하세요. Gemma thinking은 비활성화합니다."}</p>
        {!openai && <div className="profile-egress">
          <label>등록된 내부 대상 선택<select disabled={internalTargets.loading || Boolean(internalTargets.error) || !internalTargets.items?.length} value={allowedInternalTarget(form.base_url, internalTargets.items)?.id || ""} onChange={(event) => { const target = internalTargets.items?.find((item) => item.id === event.target.value); if (target) setForm({ ...form, base_url: internalTargetURL(target, form.base_url) }); }}><option value="">내부 IP·포트 선택</option>{(internalTargets.items || []).map((item) => <option key={item.id} value={item.id}>{internalTargetAddress(item)}{item.description ? ` · ${item.description}` : ""}</option>)}</select></label>
          {internalTargets.error && <p role="alert" className="egress-validation">{internalTargets.error}</p>}
          {targetError ? <p role="status" className="egress-validation">{targetError}</p> : <p>등록된 IP·포트와 일치합니다. 연결 성공이나 모델 품질은 아직 확인하지 않았습니다.</p>}
          <p>목록 선택 시 Base URL에 /v1을 채웁니다. 서버에 맞게 HTTP·HTTPS를 확인하세요. 허용 경로는 빈 경로 또는 /v1입니다. HTTPS 인증서 검증에는 해당 IP를 포함한 인증서가 필요합니다.</p>
          <div className="egress-actions"><button type="button" className="secondary small" disabled={internalTargets.loading} onClick={loadTargets}>허용 목록 새로고침</button><button type="button" className="secondary small" onClick={onInternalEgress}>내부 연결 허용 설정</button></div>
        </div>}
        <label className="checkbox-row"><input type="checkbox" checked={form.tls_verify} disabled={openai} onChange={update("tls_verify")} />TLS 인증서 검증{openai ? " (필수·고정)" : ""}</label>
        {openai && <div className="external-approval"><strong>외부 전송 및 비용 승인</strong><label className="checkbox-row"><input type="checkbox" checked={form.external_data_approved} onChange={update("external_data_approved")} required /><span>{EXTERNAL_DATA_APPROVAL}</span></label><p>빠른 테스트·전체 검증도 예시 입력을 OpenAI에 전송하며 비용이 발생할 수 있습니다. 전체 검증 통과는 판정 품질을 보증하지 않습니다.</p></div>}
        <p className="form-note">API Key는 서버에 암호화해 저장하며 이 화면에서 다시 조회하지 않습니다. 같은 공급자의 편집에서만 빈 입력은 기존 키 유지로 처리합니다.</p>
        </fieldset>
        <div className="action-row"><button className="primary" disabled={Boolean(busy) || Boolean(targetError) || editingAssigned || Boolean(profilesError)}>{busy === "save" ? "저장 중…" : editingId ? "수정 저장" : "모델 등록"}</button></div>
      </form>
      </Dialog>
    </div>
  );
}

export function TestResult({ profile, test, onViewDataset }) {
  const [selected, setSelected] = useState(null);
  const [technical, setTechnical] = useState(false);
  const checks = test.checks || [];
  const failedCheck = checks.find(check => check.status === "failed");
  return <section className="panel test-result">
    <div className="panel-head"><div><h2>{test.name || profile.name} · {test.mode === "full" ? "전체 검증" : "빠른 테스트"}</h2><p className="ux-muted">저장 당시의 연결·기능 검증 결과입니다. 현재 설정이나 탐지 정확도는 별도 확인이 필요합니다.</p></div><Status value={test.status} /></div>
    {(test.error_message || test.error_code || failedCheck) && <div className="error">{failedCheck && `${modelCheckNames[failedCheck.name] || "검증"} 실패 · `}{modelValidationError(failedCheck?.error_code || test.error_code)}</div>}
    <div className="check-grid">
      {checks.map((check, index) => <article key={check.name}><div><strong>{modelCheckNames[check.name] || "기타 확인"}</strong><Status value={check.status} /></div><span>{formatDuration(check.latency_ms)}</span><button type="button" className="text-button" onClick={() => setSelected(index)}>확인 내용</button></article>)}
      {!checks.length && <div className="empty">{["pending", "running"].includes(test.status) ? "검증 시작을 기다리고 있습니다." : "기록된 검증 항목이 없습니다."}</div>}
    </div>
    <button type="button" className="secondary" onClick={() => setTechnical(true)}>검증 기술정보</button>
    <Dialog open={selected !== null} title="검증 항목 상세" onClose={() => setSelected(null)}>{selected !== null && <ModelCheckDetail key={selected} check={checks[selected]} />}</Dialog>
    <Dialog open={technical} title="검증 기술정보" onClose={() => setTechnical(false)}><TextInspector label="검증 식별정보" value={{ test_id: test.id, profile_fingerprint: test.profile_fingerprint, error_code: test.error_code, error_message: test.error_message, created_at: test.created_at, completed_at: test.completed_at }} /></Dialog>
    <DatasetEvaluation test={test} onViewDataset={onViewDataset} />
  </section>;
}

function initialNavigationSnapshot() {
  return {
    page: "dashboard", listState: initialListState(), selectedId: null,
    resultsPurpose: "", testResults: initialTestResultsState(),
    detailReturnPage: "analyses", settingsTab: "models", detailTab: "result", days: 7, serviceApiKeyId: "",
  };
}

export default function App() {
  return <ConsolePreferencesProvider><ConsoleApp /></ConsolePreferencesProvider>;
}

function ConsoleApp() {
  const { t } = useConsolePreferences();
  const [principal, setPrincipal] = useState(null);
  const [globalSearch, setGlobalSearch] = useState(null);
  const [checking, setChecking] = useState(true);
  const [logoutState, setLogoutState] = useState({ busy: false, error: "" });
  const [navigation] = useState(() => createBrowserHistory({ browser: window, initialSnapshot: initialNavigationSnapshot, readHash: readAppHash, writeHash: writeAppHash, applyRoute: applyAppRoute }));
  const screen = useSyncExternalStore(navigation.subscribe, navigation.getSnapshot, navigation.getSnapshot);
  const { page, listState, selectedId, resultsPurpose, testResults, detailReturnPage, settingsTab, detailTab, days, serviceApiKeyId = "" } = screen;
  const setListState = useCallback(update => navigation.remember(current => ({ ...current, listState: typeof update === "function" ? update(current.listState) : update })), [navigation]);
  const setTestResults = useCallback(update => navigation.remember(current => ({ ...current, testResults: typeof update === "function" ? update(current.testResults) : update })), [navigation]);
  const setDays = useCallback(value => navigation.remember(current => ({ ...current, days: value })), [navigation]);
  const [, setProductionName] = useState("미설정");
  const summaryScope = `${days}:${serviceApiKeyId}`;
  const [summaryView, setSummaryView] = useState({ scope: "", summary: null, error: "", updatedAt: null });
  const { summary, error: summaryError, updatedAt } = summaryView.scope === summaryScope ? summaryView : { summary: null, error: "", updatedAt: null };

  const onUnauthorized = useCallback(() => { navigation.reset(); setPrincipal(null); }, [navigation]);
  const logoutController = useMemo(() => createLogoutController({ logout: api.logout, onSuccess: onUnauthorized, onChange: setLogoutState }), [onUnauthorized]);
  async function checkSession() {
    try { setPrincipal(await api.me()); } catch { onUnauthorized(); }
    finally { setChecking(false); }
  }
  useEffect(() => { checkSession(); }, []);
  useEffect(() => navigation.start(), [navigation]);
  useEffect(() => {
    if (!principal) return undefined;
    setSummaryView({ scope: summaryScope, summary: null, error: "", updatedAt: null });
    return startVisiblePolling(async signal => {
      const result = await api.dashboard(days, { signal }, serviceApiKeyId);
      if (!signal.aborted) setSummaryView({ scope: summaryScope, summary: result, error: "", updatedAt: Date.now() });
      return result.counts.pending > 0 || result.counts.processing > 0;
    }, { onError: err => { setSummaryView({ scope: summaryScope, summary: null, error: "readError", updatedAt: null }); if (err.status === 401) onUnauthorized(); } });
  }, [principal, onUnauthorized, days, serviceApiKeyId, summaryScope]);
  const resolveDetail = useCallback(row => {
    const current = navigation.getSnapshot(); if (current.page !== "detail" || current.selectedId !== row.id) return;
    const purpose = row.analysis_purpose === "test" ? "test" : "production", run = purpose === "test" ? row.test_run_id : null;
    if (current.resultsPurpose === purpose && (purpose !== "test" || (current.testResults.runId || null) === (run || null)) && (purpose !== "production" || current.detailReturnPage !== "testRun")) return;
    navigation.navigate(value => ({ ...value, resultsPurpose: purpose, detailReturnPage: run ? "testRun" : "analyses", testResults: { ...value.testResults, runId: run, view: run ? "runs" : "items" } }), { replace: true });
  }, [navigation]);
  if (checking) return <div className="loading full">{t("sessionLoading")}</div>;
  if (!principal) return <Login onLogin={checkSession} />;

  function move(update) { navigation.navigate(update); window.scrollTo(0, 0); }
  function openDetail(id) { move(current => ({ ...current, selectedId: id, detailReturnPage: current.page === "dashboard" ? "dashboard" : "analyses", detailTab: "result", page: "detail" })); }
  function testList(current) { return { ...current, resultsPurpose: "test", testResults: { ...current.testResults, view: "runs", runId: null }, page: "analyses" }; }
  function viewTests() { move(testList); }
  function backToTests() { navigation.backTo(current => current.page === "analyses" && current.resultsPurpose === "test" && current.testResults.view === "runs" && !current.testResults.runId, testList); }
  function showAllTestItems() { move(current => ({ ...current, resultsPurpose: "test", testResults: { ...current.testResults, view: "items", runId: null, items: initialListState("test") }, page: "analyses" })); }
  function openTestRun(id) {
    if (!id) { backToTests(); return; }
    move(current => ({ ...current, resultsPurpose: "test", testResults: { ...current.testResults, view: "runs", runId: id, caseId: null, filters: current.testResults.runId === id ? current.testResults.filters : initialTestRunFilters() }, page: "analyses" }));
  }
  function openRunItem(id) { move(current => ({ ...current, selectedId: id, detailReturnPage: "testRun", detailTab: "result", page: "detail" })); }
  function viewDataset(source, runId) {
    if (runId) { openTestRun(runId); return; }
    const items = datasetListState(source);
    if (items) move(current => ({ ...current, resultsPurpose: "test", testResults: { ...current.testResults, view: "items", runId: null, items }, page: "analyses" }));
  }
  function openTest(id) { move(current => ({ ...current, selectedId: id, detailReturnPage: "test", detailTab: "result", page: "detail" })); }
  function filterProduction(filters) { move(current => ({ ...current, resultsPurpose: "production", listState: dashboardListState(filters), page: "analyses" })); }
  function changeResultsScope(purpose) {
    if (purpose === resultsPurpose) return;
    move(current => ({ ...current, resultsPurpose: purpose, testResults: { ...current.testResults, view: "items", runId: null }, listState: purpose === "test" ? current.listState : { ...current.listState, offset: 0, draft: { ...current.listState.draft, analysis_purpose: purpose }, applied: { ...current.listState.applied, analysis_purpose: purpose } } }));
  }
  function navigate(key) { move(current => ({ ...consoleDestination(current, key), ...(key === "run" ? { cloneRunId: null } : {}) })); }
  function search(field, query) { const result = consoleSearch(screen, field, query); if (!result.error && query.trim()) setGlobalSearch({ field, query: query.trim() }); return result.error; }
  function backFromDetail() {
    const targetPage = detailReturnPage === "testRun" ? "analyses" : detailReturnPage;
    navigation.backTo(candidate => isDetailOrigin(screen, candidate), current => ({ ...current, page: targetPage }));
  }
  const mode = summaryError ? null : summary?.runtime.agent_mode;
  const runtimeProps = { summary, error: summaryError, days, onDaysChange: setDays, serviceApiKeyId,
    onKeyChange: value => navigation.remember(current => ({ ...current, serviceApiKeyId: value })),
    updatedAt, onUnauthorized, onNavigate: navigate };
  return <ConsoleShell screen={screen} principal={principal} healthError={Boolean(summaryError)} onNavigate={navigate} onSearch={search} logoutState={logoutState} onLogout={() => logoutController.submit()}>
    {globalSearch && <GlobalSearchResults key={`${globalSearch.field}:${globalSearch.query}`} search={globalSearch} onClose={() => setGlobalSearch(null)} onOpen={row => move(current => ({ ...current, page: "detail", selectedId: row.id, detailTab: "result", resultsPurpose: row.analysis_purpose === "test" ? "test" : "production", detailReturnPage: row.analysis_purpose === "test" && row.test_run_id ? "testRun" : "analyses", testResults: { ...current.testResults, runId: row.test_run_id || null, view: row.test_run_id ? "runs" : "items" } }))} />}
    {logoutState.error && <div className="error" role="alert">{logoutState.error}</div>}
    {mode === "stub" && <div className="runtime-banner"><Icon name="test" size={18} /><div><strong>{t("stubTitle")}</strong><span>{t("stubNote")}</span></div><span className="runtime-tag">STUB</span></div>}
    {summaryError && !["dashboard", "quality"].includes(page) && <div className="error" role="alert">{t("readError")}</div>}
    {page === "dashboard" && <R3Overview onNavigate={navigate} onOpenRun={openTestRun} onPromotion={id => move(current => ({ ...current, page: "promote", promoteRunId: id, promotionOrigin: "dashboard" }))} onGroundTruth={(id, state) => move(current => ({ ...current, page: "datasets", datasetId: id, groundTruthState: state }))} onFailures={purpose => { if (purpose === "test") { move(current => ({ ...current, page: "analyses", resultsPurpose: "test", testResults: { ...current.testResults, view: "runs", runId: null, caseId: null, history: { ...current.testResults.history, query: { ...current.testResults.history.query, has_failures: true, offset: 0 } } } })); } else filterProduction({ status: "failed" }); }} />}
    {page === "quality" && <ProductionEvaluation onOpenRun={openTestRun} referenceProps={runtimeProps} />}
    {["runtime", "diagnostics"].includes(page) && <RuntimeWorkspace onOpen={openDetail} />}
    {["deployment", "changes"].includes(page) && <Activity deploymentOnly={page === "deployment"} />}
    {page === "promote" && <Promotion onOpenTest={() => openTestRun(screen.promoteRunId)} fromHome={screen.promotionOrigin === "dashboard"} initialRunId={screen.promoteRunId} onNavigate={navigate} onBack={() => screen.promotionOrigin === "dashboard" ? navigate("status") : openTestRun(screen.promoteRunId)} />}
    {page === "analyses" && <><AnalysisResultsPage actions={resultsPurpose === "test" && testResults.view === "runs" && !testResults.runId ? <><TestDefaults agentMode={mode} /><button className="primary" onClick={() => navigate("run")}>+ {t("run")}</button></> : null} onClone={id => move(current => ({ ...current, page: "test", cloneRunId: id }))} onGroundTruth={(id, caseId) => move(current => ({ ...current, page: "datasets", datasetId: id, groundTruthCaseId: caseId, groundTruthState: undefined }))} onCaseChange={caseId => caseId ? navigation.navigate(current => ({ ...current, testResults: { ...current.testResults, caseId } })) : navigation.backTo(current => current.page === "analyses" && current.testResults.runId === testResults.runId && !current.testResults.caseId, current => ({ ...current, testResults: { ...current.testResults, caseId: null } }))} onPromote={id => move(current => ({ ...current, page: "promote", promoteRunId: id, promotionOrigin: "test" }))} splitNavigation purpose={resultsPurpose} onScopeChange={changeResultsScope} state={listState} setState={setListState} testState={testResults} setTestState={setTestResults} onSelectRun={openTestRun} onOpenRunItem={openRunItem} onOpen={openDetail} onUnauthorized={onUnauthorized} onShowTestRuns={backToTests} onShowAllTestItems={showAllTestItems} /></>}
    {page === "test" && <TestAnalysisPage key={screen.cloneRunId || "new"} cloneRunId={screen.cloneRunId} onViewTests={viewTests} onOpen={openTest} selectedRunId={null} onSelectRun={openTestRun} agentMode={mode} onConfigureModels={() => navigate("agents")} />}
    {page === "apiDocs" && <ProductionApi />}
    {page === "datasets" && <GroundTruthWorkspace initialCaseId={screen.groundTruthCaseId} id={screen.datasetId} initialState={screen.groundTruthState} onSelect={id => navigation.remember(current => ({ ...current, page: "datasets", datasetId: id, groundTruthCaseId: undefined, groundTruthState: undefined }))} />}
    {page === "settings" && <Settings standalone onProductionChange={setProductionName} onViewDataset={viewDataset} tab={settingsTab} onTabChange={tab => move(current => ({ ...current, settingsTab: tab }))} />}
    {page === "detail" && <Detail onResolved={resolveDetail} key={selectedId} id={selectedId} onBack={backFromDetail} onOpen={id => move(current => ({ ...current, selectedId: id, detailTab: "result" }))} tab={detailTab} onTabChange={tab => navigation.navigate(current => ({ ...current, detailTab: tab }))} backLabel={t(detailReturnPage === "testRun" ? "runs" : detailReturnPage === "test" ? "run" : detailReturnPage === "dashboard" ? "status" : "history")} />}
  </ConsoleShell>;
}
