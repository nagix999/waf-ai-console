import { useCallback, useEffect, useMemo, useRef, useState, useSyncExternalStore } from "react";
import { api } from "./api.js";
import { Icon } from "./Icon.jsx";
import ProductionApi from "./ProductionApi.jsx";
import AnalysisReport from "./AnalysisReport.jsx";
import HelpTooltip from "./HelpTooltip.jsx";
import Dialog from "./Dialog.jsx";
import EvaluationOverview from "./EvaluationOverview.jsx";
import DashboardView from "./DashboardView.jsx";
import RetryAnalysis from "./RetryAnalysis.jsx";
import AnalysisDownloads from "./AnalysisDownloads.jsx";
import { analysisElapsedTime, analysisQuery, analysisReceivedAt, analysisRowState, appliedFilterTags, dashboardListState, executionDuration, formatDate, formatDuration, initialListState, removeAppliedFilter, searchFields, uploadErrorText, validateFilters } from "./analysisView.js";
import { EXTERNAL_DATA_APPROVAL, changeProfileProvider, editProfileForm, modelProfileError, newProfileForm, profilePayload, profileRequiresKey, providerLabel, providerOf, validateProfileForm } from "./llmProfiles.js";
import { CompactEvaluationDetail, CompactReferenceComparison, EvaluationSummary, LabelAttachment } from "./ReferenceLabels.jsx";
import { evaluationOutcomes, isMockAnalysis, labelSources, referenceVerdicts } from "./labelEvaluation.js";
import { createDetailLoader, emptyDetailState } from "./detailLoader.js";
import { analysisNotices, analystFieldLabel, analystFollowUp, analystGuidance, analystItems, analystSummary, analystText, finalValue, groupedEvidence, hasTuningContent, isTechnicalText } from "./analystView.js";
import DecodingView from "./DecodingView.jsx";
import RawEventView from "./RawEventView.jsx";
import AgentHistory from "./AgentHistory.jsx";
import QuickValidationDialog from "./QuickValidationDialog.jsx";
import TextInspector from "./TextInspector.jsx";
import { loginError } from "./inspection.js";
import PromptSettings from "./PromptSettings.jsx";
import InputSchemaSettings, { InputSchemaMetadata } from "./InputSchemaSettings.jsx";
import InternalEgressSettings from "./InternalEgressSettings.jsx";
import ServiceApiKeys from "./ServiceApiKeys.jsx";
import FullValidationDialog, { DatasetEvaluation } from "./FullValidationDialog.jsx";
import { createFullValidationController, datasetListState, emptyFullValidationState, expectedVerdictUploadError, uploadLabelNotice } from "./modelValidation.js";
import { allowedInternalTarget, internalEgressError, internalTargetAddress, internalTargetURL, vllmTargetError } from "./internalEgress.js";
import "./unifiedAnalysis.css";
import { initialTestRunFilters, initialTestRunHistoryState, TestRunDetail, TestRunHistory } from "./TestRuns.jsx";
import { autoTestName, emptySingleTest, newTestRequestKey, singleTestEvent, testRunError, validateTestName } from "./testRuns.js";
import { ModelAssignmentCards, ModelAssignmentDialog, TestModelNotice } from "./ModelAssignments.jsx";
import { assignmentBlockReason, createAssignmentController, emptyAssignmentState, roleAssigned, testModelAvailability } from "./modelAssignments.js";
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
  NONE: "위협 없음",
  UNKNOWN: "확인 필요"
};

function Status({ value }) {
  return <span className={`status status-${value}`}>{labels[value] || value || "-"}</span>;
}

function Severity({ value }) {
  const normalized = typeof value === "string" ? value.toUpperCase() : "";
  if (!severityLabels[normalized]) {
    return <span className="severity severity-unrated">미평가(이전 결과)</span>;
  }
  return <span className={`severity severity-${normalized.toLowerCase()}`}>{severityLabels[normalized]}</span>;
}

export function Login({ onLogin }) {
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
      <form className="login-card" onSubmit={submit}>
        <div className="brand-mark"><Icon name="shield" size={26} /></div>
        <span className="eyebrow">WAF AI CONSOLE</span>
        <h1>WAF AI 분석 콘솔</h1>
        <p>관리자 계정으로 로그인하세요.</p>
        <label>아이디<input required value={username} placeholder="관리자 아이디" onChange={(e) => setUsername(e.target.value)} autoComplete="username" autoCapitalize="none" spellCheck={false} /></label>
        <div><label htmlFor="login-password">비밀번호</label><div className="login-password"><input id="login-password" required type={showPassword ? "text" : "password"} value={password} placeholder="비밀번호 입력" onChange={(e) => setPassword(e.target.value)} autoComplete="current-password" /><button type="button" className="secondary" aria-label={showPassword ? "비밀번호 숨기기" : "비밀번호 표시"} aria-pressed={showPassword} onClick={() => setShowPassword(value => !value)}>{showPassword ? "숨김" : "표시"}</button></div></div>
        {error && <div className="error" role="alert">{error}</div>}
        <button className="primary" disabled={busy}>{busy ? "확인 중…" : "로그인"}<Icon name="arrow" size={17} /></button>
        <small className="login-footnote">관리자 전용</small>
      </form>
    </main>
  );
}


export function AnalysisTable({ items, onOpen, title = "분석 결과", subtitle, total = items.length, loading = false, purpose = "" }) {
  const showPurpose = !["test", "production", "legacy_unknown"].includes(purpose);
  return (
    <section className="panel">
      <div className="panel-head"><div><h2>{title}</h2>{subtitle && <small>{subtitle}</small>}</div><span className="count-label" aria-live="polite">{loading ? "조회 중…" : `${total.toLocaleString()}건`}</span></div>
      <div className="table-wrap">
        <table className="analyst-table unified-analysis-table">
          <thead><tr><th scope="col">판정 / 심각도</th><th scope="col">이벤트 / 요약</th><th scope="col">회사 / 연결</th><th scope="col">참고 답안 비교</th><th scope="col">전체 소요 시간</th><th scope="col">접수 시각</th></tr></thead>
          <tbody>
            {items.map((item) => {
              const state = analysisRowState(item.status);
              const receivedAt = analysisReceivedAt(item.created_at);
              return <tr key={item.id} className={state.className}>
                <td className="table-meta unified-decision">{item.status === "completed" ? <><Status value={finalValue(item, "verdict")} /><Severity value={item.severity} /><span className="sr-only">{state.label}</span></> : <span className="analysis-row-state"><Icon name={item.status === "failed" ? "alert" : "clock"} size={14} />{state.label}</span>}{isMockAnalysis(item) && <small className="label-revision">모의 판정 · LLM 아님</small>}{item.input_truncated === true && <small>입력 일부 생략</small>}</td>
                <td className="table-meta analyst-event unified-event"><button className="text-button unified-event-title" onClick={() => onOpen(item.id)}>{item.signature || item.event_name || "분석 상세 보기"}</button><p className="analysis-summary-preview">{analystSummary(item)}</p>{showPurpose && <Purpose value={item.analysis_purpose} />}</td>
                <td className="table-meta unified-company"><strong title={item.company_name || ""}>{item.company_name}</strong><small title={`${item.src_ip || "-"}${item.src_port != null ? ` : ${item.src_port}` : ""}`}>{item.src_ip || "-"}{item.src_port != null ? ` : ${item.src_port}` : ""}</small><small title={`${item.dest_ip || "-"}${item.dest_port != null ? ` : ${item.dest_port}` : ""}`}>→ {item.dest_ip || "-"}{item.dest_port != null ? ` : ${item.dest_port}` : ""}</small></td>
                <td className="unified-reference"><CompactReferenceComparison evaluation={item.evaluation} detail={item} /></td>
                <td>{analysisElapsedTime(item)}</td>
                <td><time className="analysis-received-at" dateTime={receivedAt.dateTime}><span>{receivedAt.date}</span><small>{receivedAt.time}</small></time></td>
              </tr>;
            })}
            {!items.length && <tr><td colSpan="6" className="empty"><Icon name="search" size={28} /><strong>{loading ? "분석을 불러오는 중…" : "조건에 맞는 분석이 없습니다."}</strong>{!loading && <small>검색 조건을 조정하거나 테스트 데이터를 접수해 보세요.</small>}</td></tr>}
          </tbody>
        </table>
      </div>
    </section>
  );
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
export function AnalysisResultsPage({ purpose, onScopeChange, state, setState, testState, setTestState, onSelectRun, onOpenRunItem, onOpen, onUnauthorized, onShowTestRuns, onShowAllTestItems }) {
  const setPart = key => update => setTestState(current => ({ ...current, [key]: typeof update === "function" ? update(current[key]) : update }));
  const showRuns = onShowTestRuns || (() => setTestState(current => ({ ...current, view: "runs", runId: null })));
  const showAllTestItems = onShowAllTestItems || (() => setTestState(current => ({ ...current, view: "items", runId: null, items: initialListState("test") })));
  if (purpose !== "test") return <AnalysisList key={state.applied.analysis_purpose || "all"} state={state} setState={setState} onScopeChange={onScopeChange} onOpen={onOpen} onUnauthorized={onUnauthorized} />;
  if (!testState.runId && testState.view === "items") return <AnalysisList key="test-items" state={testState.items} setState={setPart("items")} onScopeChange={onScopeChange} onShowTestRuns={showRuns} onOpen={onOpen} onUnauthorized={onUnauthorized} />;
  return <div className="page-stack test-results-page">
    <div className="analysis-list-toolbar"><AnalysisScopeTabs purpose="test" onChange={onScopeChange} />{!testState.runId && <button type="button" className="secondary" onClick={showAllTestItems}>테스트 문항 전체 보기</button>}</div>
    {testState.runId
      ? <TestRunDetail key={testState.runId} id={testState.runId} filters={testState.filters} onFiltersChange={setPart("filters")} onBack={showRuns} onOpen={onOpenRunItem} onUnauthorized={onUnauthorized} />
      : <TestRunHistory state={testState.history} onStateChange={setPart("history")} onSelect={onSelectRun} onViewAnalyses={showAllTestItems} onUnauthorized={onUnauthorized} />}
    {!testState.runId && <p className="evaluation-footnote">테스트명 없이 저장된 이전 결과는 ‘테스트 문항 전체 보기’에서 확인할 수 있습니다. 기존 데이터를 임의의 테스트 실행으로 묶지 않습니다.</p>}
  </div>;
}

export function AnalysisList({ state, setState, onOpen, onUnauthorized, onScopeChange, onShowTestRuns }) {
  const [data, setData] = useState({ items: [], total: 0 });
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [formError, setFormError] = useState("");
  const [labelRefresh, setLabelRefresh] = useState(0);
  const [attachmentOpen, setAttachmentOpen] = useState(false);
  const query = useMemo(() => analysisQuery(state.applied, state.limit, state.offset), [state.applied, state.limit, state.offset]);
  useEffect(() => {
    let active = true;
    let timer;
    const controller = new AbortController();
    setLoading(true); setData({ items: [], total: 0 }); setError("");
    async function load() {
      try {
        const next = await api.analyses(query, { signal: controller.signal });
        if (active) {
          setData(next); setError("");
          if (query.offset > 0 && query.offset >= next.total) {
            setState((current) => ({ ...current, offset: Math.max(0, (Math.ceil(next.total / current.limit) - 1) * current.limit) }));
          }
        }
      } catch (err) {
        if (active) { setError(err.message); if (err.status === 401) onUnauthorized(); }
      } finally { if (active) { setLoading(false); timer = setTimeout(load, 5000); } }
    }
    load();
    return () => { active = false; clearTimeout(timer); controller.abort(); };
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
  const currentPage = Math.floor(state.offset / state.limit) + 1;
  const pageCount = Math.max(1, Math.ceil(data.total / state.limit));
  const activeTags = appliedFilterTags(state.applied, {
    status: labels, verdict: labels, review_state: labels, analysis_purpose: purposeLabels, ingest_channel: channelLabels,
    label_presence: { labeled: "있음", unlabeled: "없음" }, evaluation_outcome: evaluationOutcomes,
    reference_label: referenceVerdicts, label_source_kind: labelSources,
    label_ai_visible: { unknown: "미확인", false: "미열람 (입력자 신고)", true: "열람 · 지원 판정" },
    input_truncated: { true: "있음", false: "없음" }, waf_action: { D: "Deny · 차단", A: "Allow · 허용" },
  });
  return <div className="page-stack unified-analysis-list">
    <div className="analysis-list-toolbar">
    <AnalysisScopeTabs purpose={state.applied.analysis_purpose} onChange={scope} />
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
    <EvaluationOverview summary={data.evaluation_summary} loading={loading} error={error} />
    <AnalysisTable items={data.items} total={data.total} loading={loading} onOpen={onOpen} purpose={state.applied.analysis_purpose} />
    <div className="pagination">
      <label>페이지당<select value={state.limit} onChange={(event) => { const limit = Number(event.target.value); setState((current) => ({ ...current, limit, offset: 0 })); }}>{[25, 50, 100].map((limit) => <option key={limit} value={limit}>{limit}건</option>)}</select></label>
      <span aria-live="polite">{data.total ? `${state.offset + 1}–${Math.min(state.offset + state.limit, data.total)} / ${data.total.toLocaleString()}건` : "0건"} · {currentPage} / {pageCount}페이지</span>
      <button type="button" className="secondary" disabled={loading || state.offset === 0} onClick={() => setState((current) => ({ ...current, offset: Math.max(0, current.offset - current.limit) }))}>이전</button>
      <button type="button" className="secondary" disabled={loading || state.offset + state.limit >= data.total} onClick={() => setState((current) => ({ ...current, offset: current.offset + current.limit }))}>다음</button>
    </div>
  </div>;
}

export function TestAnalysisPage({ onViewTests, onOpen, selectedRunId, onSelectRun, agentMode, onConfigureModels }) {
  const [inputMode, setInputMode] = useState("direct");
  const [testModels, setTestModels] = useState({ profiles: null, loading: true, error: "" }); const testModelRequest = useRef(null);
  const [localRunId, setLocalRunId] = useState(null);
  const runId = selectedRunId === undefined ? localRunId : selectedRunId;
  const selectRun = onSelectRun || setLocalRunId;
  const createdRun = run => selectRun(run.id);
  const loadTestModel = useCallback(async () => {
    testModelRequest.current?.abort(); const controller = new AbortController(); testModelRequest.current = controller;
    setTestModels(previous => ({ ...previous, loading: true, error: "" }));
    try { const profiles = await api.modelProfiles({ signal: controller.signal }); if (!Array.isArray(profiles)) throw new Error("invalid_profiles"); if (!controller.signal.aborted) setTestModels({ profiles, loading: false, error: "" }); }
    catch (error) { if (!controller.signal.aborted) setTestModels({ profiles: null, loading: false, error: "조회 실패" }); }
  }, []);
  useEffect(() => { if (runId) return undefined; void loadTestModel(); window.addEventListener("focus", loadTestModel); return () => { testModelRequest.current?.abort(); window.removeEventListener("focus", loadTestModel); }; }, [loadTestModel, runId]);
  const testBlocked = testModelAvailability({ ...testModels, agentMode }).blocked;
  if (runId) return <TestRunDetail key={runId} id={runId} onBack={() => selectRun(null)} onOpen={onOpen} />;
  return <div className="page-stack test-workspace">
    <div className="ux-toolbar"><span className="ux-grow ux-muted">새 테스트</span><button type="button" className="secondary" onClick={onViewTests}>테스트 결과 보기</button></div>
    <TestModelNotice state={testModels} agentMode={agentMode} onRefresh={loadTestModel} onConfigure={onConfigureModels} />
    <div className="tabs" role="tablist" aria-label="테스트 입력 방식">
      {[["direct", "단건 분석"], ["file", "배치 파일 분석"]].map(([value, label]) => <button key={value} id={`test-input-${value}`} type="button" role="tab" aria-controls={`test-panel-${value}`} aria-selected={inputMode === value} onClick={() => setInputMode(value)}>{label}</button>)}
    </div>
    {/* Keep both forms mounted so switching input methods retains drafts and upload results. */}
    <div id="test-panel-direct" role="tabpanel" aria-labelledby="test-input-direct" hidden={inputMode !== "direct"}><SingleTest onCreated={createdRun} disabledReason={testBlocked} /></div>
    <div id="test-panel-file" role="tabpanel" aria-labelledby="test-input-file" hidden={inputMode !== "file"}><UploadPage onViewTests={onViewTests} onOpen={onOpen} onCreated={createdRun} disabledReason={testBlocked} /></div>
  </div>;
}

function UploadPage({ onViewTests, onOpen, onCreated, disabledReason }) {
  const [file, setFile] = useState(null);
  const [name, setName] = useState(""); const requestKey = useRef(null);
  const [result, setResult] = useState(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  async function upload() {
    if (disabledReason) { setError(disabledReason); return; }
    if (!file) return;
    requestKey.current ||= newTestRequestKey();
    const testName = autoTestName(name, requestKey.current);
    const nameError = validateTestName(testName); if (nameError) { setError(nameError); return; }
    setBusy(true); setError(""); setResult(null);
    try {
      const next = await api.uploadTestRun(file, testName, requestKey.current);
      setResult(next);
      requestKey.current = null;
      if (!next.rejected) onCreated(next);
    } catch (err) { setError(testRunError(err)); }
    finally { setBusy(false); }
  }
  return (
    <section className="panel upload-panel">
      <div className="section-kicker"><Icon name="upload" size={22} /><span>배치 파일 분석</span></div>
      <label className="test-name-field">테스트명 <input value={name} maxLength={120} disabled={busy} onChange={event => { setName(event.target.value); requestKey.current = null; setResult(null); }} placeholder="비워두면 ID 자동 생성" /></label>
      <div className="ux-toolbar"><span className="ux-muted">UTF-8 CSV / JSON · 입력 스키마 기준</span><span className="ux-muted">답안 자동 비교<HelpTooltip label="답안 자동 비교">expected_verdict에 true_positive, false_positive, inconclusive 중 하나를 넣으면 분석 뒤 비교합니다. null·CSV 빈칸은 답안 없음입니다. difficulty·test_category·case_name은 난이도·유형·문항명입니다. 이 정보와 답안은 모델에 보내지 않습니다. 기대 답안과의 일치가 운영 정확도를 보장하지는 않습니다.</HelpTooltip></span></div>
      <label className="dropzone">
        <div className="upload-icon"><Icon name="file" size={28} /></div>
        <strong>{file ? file.name : "파일을 선택하세요"}</strong>
        <span>CSV 또는 JSON · 최대 10 MiB · 테스트로 분류</span>
        <input type="file" accept=".csv,.json,application/json,text/csv" disabled={busy} onChange={(e) => { setFile(e.target.files?.[0] || null); setResult(null); setError(""); requestKey.current = null; }} />
      </label>
      <button className="primary" onClick={upload} disabled={!file || busy || Boolean(result) || Boolean(disabledReason)}>{busy ? "접수 중…" : "배치 분석 시작"}</button>
      {error && <div className="error" role="alert">{error}</div>}
      {result && <div className="notice" aria-live="polite">신규 {result.accepted}건 · 중복 {result.duplicates}건 · 거부 {result.rejected}건</div>}
      {uploadLabelNotice(result) && <p role="status">{uploadLabelNotice(result)}</p>}
      {!!result?.errors?.length && <div className="upload-errors"><h3>접수하지 못한 행</h3><p className="muted">오류를 수정한 뒤 다시 접수하세요. 기존 답안을 수정하려면 분석 결과의 참고 답안 연결에서 미리보기·확정하세요. 오류 상세는 최대 100건 표시합니다.</p><div className="table-wrap"><table><thead><tr><th>행 번호</th><th>오류 필드 / 코드</th></tr></thead><tbody>{result.errors.map((item, index) => <tr key={index}><td>{item.row ?? "—"}</td><td><code>{expectedVerdictUploadError(item) || uploadErrorText(item)}</code></td></tr>)}</tbody></table></div></div>}
      {result && <div className="action-row"><button type="button" className="primary" onClick={() => onCreated(result)}>이 테스트의 진행·평가 보기</button><button type="button" className="secondary" onClick={() => { setResult(null); requestKey.current = null; }}>같은 파일로 새 테스트 준비</button><button type="button" className="secondary" onClick={onViewTests}>테스트 분석 결과 보기</button></div>}
    </section>
  );
}

function SingleTest({ onCreated, disabledReason }) {
  const [name, setName] = useState(""); const [expectedVerdict, setExpectedVerdict] = useState("");
  const [difficulty, setDifficulty] = useState(""); const [category, setCategory] = useState(""); const requestKey = useRef(null);
  const [form, setForm] = useState(emptySingleTest);
  const [extraOpen, setExtraOpen] = useState(false);
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const update = (key) => (event) => { setForm({ ...form, [key]: event.target.value }); requestKey.current = null; };
  async function run(event) {
    event.preventDefault(); if (disabledReason) { setMessage(disabledReason); return; }
    requestKey.current ||= newTestRequestKey();
    const testName = autoTestName(name, requestKey.current);
    const nameError = validateTestName(testName); if (nameError) { setMessage(nameError); return; } setMessage("등록 중…"); setBusy(true);
    try {
      const created = await api.createTestRun({ name: testName, idempotency_key: requestKey.current, event: singleTestEvent(form, requestKey.current), ...(expectedVerdict ? { expected_verdict: expectedVerdict } : {}), ...(difficulty.trim() ? { difficulty: difficulty.trim() } : {}), ...(category.trim() ? { test_category: category.trim() } : {}) });
      setMessage("테스트를 접수했습니다.");
      requestKey.current = null; onCreated(created);
    } catch (err) { setMessage(testRunError(err)); }
    finally { setBusy(false); }
  }
  return (
    <form className="panel form-panel" onSubmit={run}>
      <div className="panel-head"><h2><Icon name="test" size={20} />단건 분석</h2><span className="ux-muted">HTTP 요청 1건</span></div>
      <fieldset className="test-submission-fields" disabled={busy}>
      <label className="test-name-field">테스트명 <input maxLength={120} value={name} onChange={event => { setName(event.target.value); requestKey.current = null; }} placeholder="비워두면 ID 자동 생성" /></label>
      <div className="form-grid"><label>기대 답안 (선택)<select value={expectedVerdict} onChange={event => { setExpectedVerdict(event.target.value); requestKey.current = null; }}><option value="">답안 없음</option>{Object.entries(referenceVerdicts).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label><label>난이도 (선택)<input value={difficulty} maxLength={80} onChange={event => { setDifficulty(event.target.value); requestKey.current = null; }} placeholder="예: easy" /></label><label>테스트 유형 (선택)<input value={category} maxLength={120} onChange={event => { setCategory(event.target.value); requestKey.current = null; }} placeholder="예: sql_injection" /></label></div>
      <span className="ux-muted">답안은 분석 후 비교하며 답안·난이도·유형은 모델에 보내지 않습니다.</span>
      <div className="form-grid">
        <label>회사명<input value={form.company_name} required onChange={update("company_name")} placeholder="예: 사내 서비스" /></label>
        <label>WAF 벤더<input value={form.waf_vendor} required onChange={update("waf_vendor")} placeholder="예: generic" /></label>
        <label>출발지 IP<input value={form.src_ip} required onChange={update("src_ip")} placeholder="예: 192.0.2.10" /></label>
        <label>목적지 IP<input value={form.dest_ip} required onChange={update("dest_ip")} placeholder="예: 198.51.100.20" /></label>
        <label>WAF 조치<select aria-label="WAF 조치" value={form.waf_action} required onChange={update("waf_action")}><option value="" disabled>선택하세요</option><option value="D">차단</option><option value="A">허용</option></select></label>
      </div>
      <label>탐지명<input value={form.signature} onChange={update("signature")} placeholder="예: SQL Injection" /></label>
      <label>HTTP 원문<textarea rows="8" required value={form.payload} onChange={update("payload")} placeholder={"GET /search?q=example HTTP/1.1\nHost: example.internal\n\n"} /></label>
      <button type="button" className="text-button" aria-expanded={extraOpen} aria-controls="single-test-extra" onClick={() => setExtraOpen(value => !value)}>{extraOpen ? "추가 입력 닫기" : "추가 입력"}</button>
      <div className="form-grid" id="single-test-extra" hidden={!extraOpen}><label>이벤트 ID<input value={form.event_id} onChange={update("event_id")} placeholder="비워두면 ID 자동 생성" /></label><label>이벤트명<input value={form.event_name} onChange={update("event_name")} placeholder="예: 검색 요청" /></label><label>출발지 포트<input type="number" min="0" max="65535" value={form.src_port} onChange={update("src_port")} placeholder="예: 42310" /></label><label>목적지 포트<input type="number" min="0" max="65535" value={form.dest_port} onChange={update("dest_port")} placeholder="예: 443" /></label></div>
      </fieldset><div className="action-row"><button className="primary" disabled={busy || Boolean(disabledReason)}>{busy ? "접수 중…" : "분석 시작"}</button><span aria-live="polite">{message}</span></div>
    </form>
  );
}

function Detail({ id, onBack, onOpen, backLabel = "분석 결과", tab: controlledTab, onTabChange }) {
  const [view, setView] = useState(() => emptyDetailState(id));
  const loader = useRef(null);
  const { detail, error, loading, runs, runsError, runsLoading, labelHistory, labelHistoryError, labelsLoading } = view;
  const [raw, setRaw] = useState(null);
  const [rawError, setRawError] = useState("");
  const [rawAttempt, setRawAttempt] = useState(0);
  const [localTab, setLocalTab] = useState("result");
  const tab = controlledTab ?? localTab;
  const setTab = onTabChange || setLocalTab;
  const [reportMode, setReportMode] = useState("preview");
  const [technicalOpen, setTechnicalOpen] = useState(false);
  const [technicalTab, setTechnicalTab] = useState("meta");
  const [decodingOpen, setDecodingOpen] = useState(false);
  useEffect(() => {
    const current = createDetailLoader({ id, api, onChange: setView });
    loader.current = current;
    setRaw(null); setRawError("");
    current.start();
    return () => { current.dispose(); loader.current = null; };
  }, [id]);
  useEffect(() => { loader.current?.setAgentVisible(technicalOpen && technicalTab === "agent"); }, [id, technicalOpen, technicalTab]);
  useEffect(() => {
    if (!(tab === "raw" || (tab === "result" && decodingOpen)) || raw) return;
    let active = true;
    const controller = new AbortController();
    setRawError("");
    api.rawEvent(id, { signal: controller.signal }).then((value) => { if (active) setRaw(value); }).catch((err) => { if (active) setRawError(err.message); });
    return () => { active = false; controller.abort(); };
  }, [id, raw, tab, decodingOpen, rawAttempt]);
  const navigation = <div className="panel-head-inline"><button className="back" onClick={onBack}>← {backLabel}</button><div className="ux-toolbar">{detail && <RetryAnalysis key={id} detail={detail} onOpen={onOpen} />}{detail && <AnalysisDownloads id={id} status={detail.status} />}<button type="button" className="secondary" disabled={loading || labelsLoading || runsLoading} onClick={() => loader.current?.refresh()}>새로고침</button></div></div>;
  if (!detail || view.id !== id) return <div className="page-stack">{navigation}{error ? <div className="error" role="alert">{error}</div> : <div className="loading">불러오는 중…</div>}</div>;
  const durationFallback = ["pending", "processing"].includes(detail.status) ? "진행 중" : "측정 전 데이터";
  const notices = analysisNotices(detail);
  const rawFailure = rawError && <div className="error" role="alert"><p>원문 정보를 불러오지 못했습니다. {rawError}</p><button type="button" className="secondary" onClick={() => setRawAttempt((value) => value + 1)}>원문 다시 불러오기</button></div>;
  return (
    <div className="page-stack">
      {navigation}
      {error && <div className="error" role="alert">{error}</div>}
      <section className={`panel decision-card decision-${detail.status === "completed" ? finalValue(detail, "verdict") : "pending"}`}>
        <div><span>{isMockAnalysis(detail) ? "모의 판정" : "판정 요약"}</span>{detail.status === "completed" ? <Status value={finalValue(detail, "verdict")} /> : <Status value={detail.status} />}{detail.status === "completed" ? <Severity value={detail.result?.threat_analysis?.severity} /> : <span className="muted">심각도 미확정</span>}</div>
        <div className="decision-summary-line"><strong>{analystSummary(detail)}</strong></div>
        {notices.map((notice) => <small className="analyst-notice" key={notice}>{notice}</small>)}
      </section>
      <CompactEvaluationDetail detail={detail} history={labelHistory} historyError={labelHistoryError} />
      <section className="detail-summary panel">
        <div><span>회사</span><strong>{detail.company_name || "미기록"}</strong></div>
        <div><span>출발지 IP / 포트</span><strong>{detail.src_ip || "미기록"}</strong><small>{detail.src_port ?? "포트 미기록"}</small></div>
        <div><span>목적지 IP / 포트</span><strong>{detail.dest_ip || "미기록"}</strong><small>{detail.dest_port ?? "포트 미기록"}</small></div>
        <div><span>전체 소요 시간</span><strong>{formatDuration(detail.total_elapsed_ms, durationFallback)}</strong><small>접수부터 종료까지</small></div>
      </section>
      <div className="tabs" role="tablist" aria-label="분석 상세">
        {[['result','판정 결과'],['raw','HTTP 원문'],['report','보고서']].map(([value,label]) => <button key={value} role="tab" aria-selected={tab === value} onClick={() => setTab(value)}>{label}</button>)}
      </div>
      {tab === "result" && <><ResultView detail={detail} /><section className="panel inspection-section-head"><div><h2>인코딩·난독화 문자열</h2><p className="ux-muted">원본과 변환 결과 비교</p></div><button type="button" className="secondary" onClick={() => setDecodingOpen(true)}>문자열 비교</button></section><Dialog open={decodingOpen} title="인코딩·난독화 문자열" className="inspection-dialog" onClose={() => setDecodingOpen(false)}>{decodingOpen && (rawFailure || (raw ? <DecodingView decoding={raw.decoding} /> : <p className="loading" role="status">원문과 변환 결과를 불러오는 중…</p>))}</Dialog></>}
      {tab === "raw" && (rawFailure || (raw ? <RawEventView event={raw} /> : <p className="panel loading" role="status">원문을 불러오는 중…</p>))}
      {tab === "report" && <AnalysisReport detail={detail} decoding={raw?.decoding ?? null} mode={reportMode} onModeChange={setReportMode} />}
      <div className="ux-toolbar"><button type="button" className="secondary" onClick={() => setTechnicalOpen(true)}>이벤트·실행 정보</button></div><Dialog open={technicalOpen} title="이벤트·실행 정보" className="inspection-dialog" onClose={() => setTechnicalOpen(false)}>{technicalOpen && <div className="analyst-disclosure-body">
        <div className="tabs" role="tablist" aria-label="기술정보"><button type="button" role="tab" aria-selected={technicalTab === "meta"} onClick={() => setTechnicalTab("meta")}>실행 개요</button><button type="button" role="tab" aria-selected={technicalTab === "agent"} onClick={() => setTechnicalTab("agent")}>Agent 실행 이력</button><button type="button" role="tab" aria-selected={technicalTab === "json"} onClick={() => setTechnicalTab("json")}>결과 JSON</button></div>
        {technicalTab === "meta" && <>
          <section className="detail-summary">
            <div><span>연동 시스템</span><strong>{detail.source_system}</strong></div>
            <div><span>모델</span><strong>{detail.model_profile || "미기록"}</strong></div>
            <div><span>분석 지침</span><strong>{detail.prompt_version || "미기록"}</strong></div>
            <div><span>모델 신뢰도<HelpTooltip label="모델 신뢰도">모델이 스스로 평가한 값입니다. 보정된 정탐 확률이나 정확도가 아닙니다.</HelpTooltip></span><strong>{finalValue(detail, "confidence_score") ?? "미기록"}</strong></div>
            <div><span>대기 시간</span><strong>{formatDuration(detail.queue_wait_ms)}</strong></div>
            <div><span>처리 시간</span><strong>{formatDuration(detail.processing_duration_ms)}</strong><small className="ux-muted">처리 시작~종료 · 재시도·복구 대기 포함</small></div>
            <div><span>접수 경로</span><strong>{channelLabels[detail.ingest_channel] || "기존 미분류"}</strong></div>
            <div><span>분석가 검토</span><strong>{labels[detail.review_state] || "미기록"}</strong></div>
          </section>
          {detail.error_code && <p className="error">실패 코드: {detail.error_code}</p>}
          <span className="ux-muted">추가 검증<HelpTooltip label="추가 검증">Primary 결과를 전달받지 않고 같은 입력을 별도로 분석하는 Verifier 단계입니다. 이 단계의 판정은 최종 결과와 구분합니다. 구체적인 입출력은 Agent 실행 이력이나 결과 JSON에서 확인하세요.</HelpTooltip></span>
          <p>{detail.result?.verifier?.executed === true ? "실행됨" : detail.result?.verifier?.executed === false ? "실행하지 않음" : "실행 기록 없음"}</p>
        </>}
        {technicalTab === "meta" && <><section className="detail-summary"><div><span>이벤트 ID</span><strong>{detail.event_id}</strong></div><div><span>분석 ID</span><strong>{detail.id}</strong></div><div><span>탐지명</span><strong>{detail.signature || "미기록"}</strong></div><div><span>WAF 조치 / 벤더</span><strong>{detail.waf_action === "D" ? "차단" : detail.waf_action === "A" ? "허용" : "미기록"}</strong><small>{detail.waf_vendor || "미기록"}</small></div><div><span>처리 상태 / 구분</span><Status value={detail.status} /><Purpose value={detail.analysis_purpose} /></div></section><InputSchemaMetadata metadata={detail.input_schema_metadata} /></>}
        {technicalTab === "agent" && <>{runsError && <div className="error" role="alert"><p>Agent 실행 이력을 불러오지 못했습니다. {runsError}</p><button type="button" className="secondary" disabled={runsLoading} onClick={() => loader.current?.refreshAgent()}>이력 다시 불러오기</button></div>}{runs === null ? (!runsError && <p className="loading" role="status">Agent 실행 이력을 불러오는 중…</p>) : <AgentHistory runs={runs} />}</>}
        {technicalTab === "json" && <TextInspector label="결과 JSON" value={detail.result} />}
      </div>}</Dialog>
      {tab === "result" && <AdditionalChecks detail={detail} />}
    </div>
  );
}

export function ResultView({ detail }) {
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
        {undecided && <p className="analyst-notice">판정 보류 상태입니다. 아래 내용만으로 공격이나 피해 발생이 확정된 것은 아닙니다.</p>}
        {threat ? <dl><dt>공격 유형</dt><dd>{analystText(threat.category)}</dd><dt>분석 위치</dt><dd>{analystFieldLabel(analystText(threat.target))}</dd><dt>분석 내용</dt><dd>{analystText(threat.technique_ko, "저장된 설명은 기술정보에서 확인할 수 있습니다.")}</dd><dt>예상 영향</dt><dd>{analystText(threat.potential_impact_ko)}</dd>{!!obfuscations.length && <><dt>인코딩·난독화</dt><dd>{obfuscations.join(", ")}</dd></>}</dl> : <p>세부 분석 내용이 기록되지 않았습니다.</p>}
        {signature && <div className="signature-context"><h3>탐지 내용과 요청의 연관성</h3><Status value={signature.relation} /><p>{analystText(signature.explanation_ko)}</p></div>}
      </section>
      <section className="panel result-card evidence-card">
        <h2>판정 근거</h2>
        <div className="evidence-list">
          {evidence.map((item, index) => <article key={index}>
            <div className="evidence-heading"><span>근거 {index + 1}</span><strong>{analystFieldLabel(item.field)}</strong>{typeof item.field === "string" && analystFieldLabel(item.field) !== item.field && <small className="evidence-field-path">{item.field}</small>}</div>
            <div className="evidence-section"><span>원문 발췌</span><code>{typeof item.excerpt === "string" ? item.excerpt : "발췌문 미기록"}</code></div>
            <div className="evidence-section evidence-interpretation"><span>분석 내용</span>{item.interpretations.map((interpretation, interpretationIndex) => <p key={interpretationIndex}>{analystText(interpretation, "저장된 설명은 기술정보에서 확인할 수 있습니다.")}</p>)}</div>
          </article>)}
          {!evidence.length && <p>명시된 근거가 없습니다.</p>}
        </div>
      </section>
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

export function Settings({ onProductionChange, onViewDataset, tab: controlledTab, onTabChange }) {
  const [localTab, setLocalTab] = useState("models");
  const tab = controlledTab ?? localTab;
  const setTab = onTabChange || setLocalTab;
  return <div className="page-stack"><div className="tabs" role="tablist" aria-label="설정 항목">
    <button type="button" role="tab" aria-selected={tab === "models"} onClick={() => setTab("models")}>LLM 프로필</button>
    <button type="button" role="tab" aria-selected={tab === "prompts"} onClick={() => setTab("prompts")}>프롬프트</button>
    <button type="button" role="tab" aria-selected={tab === "schema"} onClick={() => setTab("schema")}>입력 스키마</button>
    <button type="button" role="tab" aria-selected={tab === "egress"} onClick={() => setTab("egress")}>내부 연결 허용</button>
    <button type="button" role="tab" aria-selected={tab === "keys"} onClick={() => setTab("keys")}>서비스 API Key</button>
  </div>{tab === "models" ? <ModelSettings onProductionChange={onProductionChange} onInternalEgress={() => setTab("egress")} onViewDataset={onViewDataset} /> : tab === "prompts" ? <PromptSettings /> : tab === "schema" ? <InputSchemaSettings /> : tab === "egress" ? <InternalEgressSettings /> : <ServiceApiKeys />}</div>;
}

export function ModelSettings({ onProductionChange, onInternalEgress, onViewDataset }) {
  const [formOpen, setFormOpen] = useState(false);
  const [managedId, setManagedId] = useState(null);
  const [profiles, setProfiles] = useState([]);
  const [profilesLoading, setProfilesLoading] = useState(true); const [profilesError, setProfilesError] = useState(""); const profileRead = useRef(0);
  const [assignment, setAssignment] = useState(emptyAssignmentState); const assignmentController = useRef(null);
  const [tests, setTests] = useState({});
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
    } catch (err) { if (sequence === profileRead.current) { setProfilesLoading(false); setProfilesError("프로필 또는 검증 이력을 조회하지 못했습니다."); } }
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
    loadProfiles();
    const timer = setInterval(loadProfiles, 3000);
    return () => { clearInterval(timer); profileRead.current += 1; };
  }, [loadProfiles]);

  function edit(profile) {
    if (roleAssigned(profile)) { setMessage("Production 또는 Test로 지정된 프로필은 수정할 수 없습니다. 지정을 해제하거나 비활성화하세요."); return; }
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
      <ModelAssignmentCards profiles={profiles} loading={profilesLoading} error={profilesError} busy={Boolean(busy)} onUnassignTest={profile => openAssignment(profile, "unassign_test")} />
      {message && <p className="notice" role="status">{message}</p>}
      <section className="panel profile-section">
        <div className="panel-head"><div><h2>모델 목록</h2><p className="ux-muted">현재 설정으로 전체 검증을 통과해야 운영·테스트에 지정할 수 있습니다.</p></div><div className="ux-toolbar"><button type="button" className="primary" disabled={Boolean(busy)} onClick={() => { if (editingProfile) resetForm(); setFormOpen(true); }}>모델 추가</button><button type="button" className="secondary" disabled={Boolean(busy)} onClick={loadProfiles}>새로고침</button></div></div>
        {profilesError && <p className="error" role="alert">{profilesError} 최신 상태를 확인하기 전에는 지정할 수 없습니다.</p>}
        <div className="table-wrap">
          <table className="profile-table">
            <thead><tr><th>이름</th><th>모델</th><th>최근 검증</th><th>사용 상태</th><th>관리</th></tr></thead>
            <tbody>
              {profiles.map((profile) => {
                const latest = tests[profile.id]?.[0];
                const active = ["pending", "running"].includes(latest?.status) || ["waiting", "running"].includes(latest?.dataset_evaluation?.status);
                const internalTargetIssue = providerOf(profile) === "vllm" ? vllmTargetError(profile.base_url, internalTargets) : "";
                const assignmentIssue = assignmentBlockReason(profile, { loading: profilesLoading, error: profilesError, targetError: internalTargetIssue, active });
                return <tr key={profile.id}>
                  <td><strong>{profile.name}</strong><span className={`provider-badge provider-${providerOf(profile)}`}>{providerLabel(profile)}</span>{internalTargetIssue && <small className="error">연결 허용 확인 필요</small>}{providerOf(profile) === "openai" && !profile.external_data_approved && <small className="error">외부 전송 미승인</small>}</td>
                  <td><span>{profile.model_name}</span></td>
                  <td>{latest ? <><Status value={latest.status} /><small>{latest.mode} · {latest.completed_at ? new Date(latest.completed_at).toLocaleString("ko-KR") : "진행 중"}</small></> : <span>-</span>}</td>
                  <td><div className="profile-role-badges">{profile.status === "production" && <span className="status status-production">Production 지정</span>}{profile.is_test && <span className="status purpose-test">Test 지정</span>}</div>{profile.status !== "production" && <Status value={profile.status} />}</td>
                  <td><button type="button" className="secondary" onClick={() => setManagedId(profile.id)}>관리</button><Dialog open={managedId === profile.id} title={`${profile.name} · 모델 관리`} onClose={() => { if (!busy) setManagedId(null); }}><section className="detail-summary"><div><span>연결 주소</span><strong>{profile.base_url}</strong></div><div><span>입력 한도 / 최대 출력</span><strong>{profile.context_window.toLocaleString()} / {profile.max_output_tokens} 토큰</strong></div><div><span>제한 시간 / 검증 동시 요청</span><strong>{profile.timeout_seconds}초 / {profile.test_concurrency}건</strong></div><div><span>API Key</span><strong>{profile.has_api_key ? "저장됨" : "없음"}</strong></div></section><div className="profile-actions">
                    <button className="secondary small" disabled={Boolean(busy) || active || profile.status === "disabled" || Boolean(internalTargetIssue) || (providerOf(profile) === "openai" && !profile.external_data_approved)} onClick={() => runTest(profile, "quick")}>{busy === `q-${profile.id}` ? "등록 중" : "빠른 테스트"}</button>
                    <button className="secondary small" disabled={Boolean(busy) || active || profile.status === "disabled" || Boolean(internalTargetIssue) || (providerOf(profile) === "openai" && !profile.external_data_approved)} onClick={() => runTest(profile, "full")}>{busy === `f-${profile.id}` ? "등록 중" : "전체 검증"}</button>
                    <button className="secondary small" disabled={Boolean(busy) || roleAssigned(profile) || Boolean(profilesError)} title={roleAssigned(profile) ? "Production·Test 지정 중에는 수정할 수 없습니다." : undefined} onClick={() => edit(profile)}>편집</button>
                    {profile.status !== "production" && <button className="primary small" disabled={Boolean(busy) || Boolean(assignmentIssue)} onClick={() => openAssignment(profile, "production")}>Production 지정</button>}
                    {profile.is_test ? <button className="secondary small" disabled={Boolean(busy) || Boolean(profilesError)} onClick={() => openAssignment(profile, "unassign_test")}>Test 지정 해제</button> : <button className="secondary small" disabled={Boolean(busy) || Boolean(assignmentIssue)} onClick={() => openAssignment(profile, "test")}>Test 지정</button>}
                    {profile.status === "disabled"
                      ? <button className="secondary small" disabled={Boolean(busy) || Boolean(internalTargetIssue)} onClick={() => act(`e-${profile.id}`, () => api.enableModelProfile(profile.id))}>활성화</button>
                      : <button className="secondary small" disabled={Boolean(busy) || Boolean(profilesError)} onClick={() => openAssignment(profile, "disable")}>비활성화</button>}
                  </div>{assignmentIssue && <p className="profile-assignment-reason">지정 불가: {assignmentIssue}</p>}{roleAssigned(profile) && <p className="profile-assignment-reason">지정 중에는 편집할 수 없습니다. 비활성화하면 이 모델의 모든 용도 지정을 해제합니다.</p>}{message && <p className="notice" role="status">{message}</p>}{managedId === profile.id && latest && <TestResult profile={profile} test={latest} onViewDataset={onViewDataset} />}</Dialog></td>
                </tr>;
              })}
              {!profiles.length && <tr><td colSpan="5" className="empty">{profilesLoading ? "모델을 불러오는 중…" : profilesError ? "모델 목록을 확인할 수 없습니다." : "등록된 모델이 없습니다."}</td></tr>}
            </tbody>
          </table>
        </div>
      </section>

      <Dialog open={formOpen} title={editingId ? "모델 수정" : "모델 추가"} onClose={() => { if (!busy) setFormOpen(false); }}>
      <form className="panel form-panel profile-form" onSubmit={save}>
        <div className="panel-head"><div><h2>{editingId ? "LLM 프로필 편집" : "LLM 프로필 추가"}</h2><small>등록·수정만으로 모델을 호출하지 않습니다. 전체 검증과 Production·Test 지정은 별도 작업입니다.</small></div>{editingId && <button type="button" className="secondary small" disabled={Boolean(busy)} onClick={resetForm}>취소</button>}</div>
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
          <label>검증 동시 요청<input type="number" min="1" max="10" value={form.test_concurrency} onChange={update("test_concurrency")} /></label>
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
  const names = { models: "모델 연결", basic_chat: "기본 응답", nested_json_schema: "출력 형식", waf_analysis_schema: "WAF 판정 형식", system_role: "지침 적용", near_configured_context: "긴 입력 처리", near_32k_context: "긴 입력 처리", concurrency: "동시 요청" };
  const checks = test.checks || [];
  return <section className="panel test-result">
    <div className="panel-head"><div><h2>{test.name || profile.name} · {test.mode === "full" ? "전체 검증" : "빠른 테스트"}</h2><p className="ux-muted">저장 당시의 연결·기능 검증 결과입니다. 현재 설정이나 탐지 정확도는 별도 확인이 필요합니다.</p></div><Status value={test.status} /></div>
    {test.error_message && <div className="error">검증을 완료하지 못했습니다. {test.error_message.length <= 160 ? test.error_message : "오류 상세는 검증 기술정보에서 확인하세요."}</div>}
    <div className="check-grid">
      {checks.map((check, index) => <article key={check.name}><div><strong>{names[check.name] || "기타 확인"}</strong><Status value={check.status} /></div><span>{formatDuration(check.latency_ms)}</span><button type="button" className="text-button" onClick={() => setSelected(index)}>확인 내용</button></article>)}
      {!checks.length && <div className="empty">{["pending", "running"].includes(test.status) ? "검증 시작을 기다리고 있습니다." : "기록된 검증 항목이 없습니다."}</div>}
    </div>
    <button type="button" className="secondary" onClick={() => setTechnical(true)}>검증 기술정보</button>
    <Dialog open={selected !== null} title="검증 항목 상세" onClose={() => setSelected(null)}>{selected !== null && <TextInspector label="검증 항목 기록" value={checks[selected]} />}</Dialog>
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
  const [principal, setPrincipal] = useState(null);
  const [checking, setChecking] = useState(true);
  const [navigation] = useState(() => createBrowserHistory({ browser: window, initialSnapshot: initialNavigationSnapshot, readHash: readAppHash, writeHash: writeAppHash, applyRoute: applyAppRoute }));
  const screen = useSyncExternalStore(navigation.subscribe, navigation.getSnapshot, navigation.getSnapshot);
  const { page, listState, selectedId, resultsPurpose, testResults, detailReturnPage, settingsTab, detailTab, days, serviceApiKeyId = "" } = screen;
  const setListState = useCallback(update => navigation.remember(current => ({ ...current, listState: typeof update === "function" ? update(current.listState) : update })), [navigation]);
  const setTestResults = useCallback(update => navigation.remember(current => ({ ...current, testResults: typeof update === "function" ? update(current.testResults) : update })), [navigation]);
  const setDays = useCallback(value => navigation.remember(current => ({ ...current, days: value })), [navigation]);
  const [productionName, setProductionName] = useState("미설정");
  const [summary, setSummary] = useState(null);
  const [summaryError, setSummaryError] = useState("");
  const [theme, setTheme] = useState(() => {
    try { return localStorage.getItem("waf-console-theme") === "dark" ? "dark" : "light"; }
    catch { return "light"; }
  });

  const onUnauthorized = useCallback(() => { navigation.reset(); setPrincipal(null); }, [navigation]);
  async function checkSession() {
    try { setPrincipal(await api.me()); } catch { onUnauthorized(); }
    finally { setChecking(false); }
  }
  useEffect(() => { checkSession(); }, []);
  useEffect(() => navigation.start(), [navigation]);
  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    try { localStorage.setItem("waf-console-theme", theme); } catch { /* Storage can be disabled. */ }
  }, [theme]);
  useEffect(() => {
    if (!principal) return undefined;
    let active = true;
    let timer;
    const controller = new AbortController();
    setSummary(null); setSummaryError("");
    async function load() {
      try {
        const result = await api.dashboard(days, { signal: controller.signal });
        if (active) { setSummary(result); setSummaryError(""); setProductionName(result.runtime.production_profile?.name || "미설정"); }
      } catch (err) {
        if (active) { setSummaryError(err.message); if (err.status === 401) onUnauthorized(); }
      } finally { if (active) timer = setTimeout(load, 15000); }
    }
    load();
    return () => { active = false; clearTimeout(timer); controller.abort(); };
  }, [principal, onUnauthorized, days]);
  if (checking) return <div className="loading full">세션 확인 중…</div>;
  if (!principal) return <Login onLogin={checkSession} />;

  function move(update) { navigation.navigate(update); window.scrollTo(0, 0); }
  function openDetail(id) { move(current => ({ ...current, selectedId: id, detailReturnPage: current.page === "dashboard" ? "dashboard" : "analyses", detailTab: "result", page: "detail" })); }
  function testList(current) { return { ...current, resultsPurpose: "test", testResults: { ...current.testResults, view: "runs", runId: null }, page: "analyses" }; }
  function viewTests() { move(testList); }
  function backToTests() { navigation.backTo(current => current.page === "analyses" && current.resultsPurpose === "test" && current.testResults.view === "runs" && !current.testResults.runId, testList); }
  function showAllTestItems() { move(current => ({ ...current, resultsPurpose: "test", testResults: { ...current.testResults, view: "items", runId: null, items: initialListState("test") }, page: "analyses" })); }
  function openTestRun(id) {
    if (!id) { backToTests(); return; }
    move(current => ({ ...current, resultsPurpose: "test", testResults: { ...current.testResults, view: "runs", runId: id, filters: current.testResults.runId === id ? current.testResults.filters : initialTestRunFilters() }, page: "analyses" }));
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
    move(current => ({ ...current, resultsPurpose: purpose, testResults: { ...current.testResults, view: "runs", runId: null }, listState: purpose === "test" ? current.listState : { ...current.listState, offset: 0, draft: { ...current.listState.draft, analysis_purpose: purpose }, applied: { ...current.listState.applied, analysis_purpose: purpose } } }));
  }
  function navigate(value) {
    move(current => ({ ...current, page: value, ...(value === "analyses" ? { testResults: { ...current.testResults, view: "runs", runId: null } } : {}) }));
  }
  function backFromDetail() {
    const targetPage = detailReturnPage === "testRun" ? "analyses" : detailReturnPage;
    navigation.backTo(candidate => isDetailOrigin(screen, candidate), current => ({ ...current, page: targetPage }));
  }
  const title = { dashboard: "대시보드", analyses: "분석 결과", test: "테스트 분석", apiDocs: "Production API", settings: "설정", detail: "분석 상세" }[page];
  const description = {
    dashboard: "운영 분석 현황과 평가 추이를 확인합니다.",
    analyses: resultsPurpose === "test" ? "테스트별 판정 결과와 평가 지표를 확인합니다." : "분석을 검색하고 판정과 근거를 확인합니다.",
    test: "단건 요청이나 파일을 테스트합니다.",
    apiDocs: "운영 연동에 필요한 API 사용 방법입니다.",
    settings: "모델·프롬프트·입력 형식과 접근 권한을 관리합니다.",
    detail: "판정 결과와 근거, 원문을 확인합니다.",
  }[page];
  const mode = summaryError ? null : summary?.runtime.agent_mode;
  return (
    <div className="app-shell">
      <a className="skip-link" href="#workspace-content" onClick={event => { event.preventDefault(); const target = document.getElementById("workspace-content"); target?.focus({ preventScroll: true }); target?.scrollIntoView({ block: "start" }); }}>본문으로 이동</a>
      <aside className="sidebar">
        <div className="brand"><span className="brand-mark"><Icon name="shield" size={23} /></span><div><strong>WAF AI<span>Console</span></strong><small>SECURITY OPERATIONS</small></div></div>
        <div className="nav-section-label">분석 공간</div>
        <nav aria-label="주 메뉴">
          {[['dashboard','대시보드'],['analyses','분석 결과'],['test','테스트 분석'],['apiDocs','Production API'],['settings','설정']].map(([value,label]) => <button key={value} aria-current={page === value || (page === "detail" && value === (detailReturnPage === "testRun" ? "analyses" : detailReturnPage)) ? "page" : undefined} onClick={() => navigate(value)}><Icon name={value} size={19} /><span>{label}</span></button>)}
        </nav>
        <div className="model-note"><div className="model-note-heading"><Icon name="server" size={16} /><span>지정된 운영 모델</span></div><strong>{productionName}</strong><span className="sidebar-mode">{mode === "stub" ? "모의 분석" : mode === "moduagent" ? "모델 분석 설정" : "설정 확인 중"}</span></div>
        <div className="sidebar-footer"><span>INTERNAL WORKSPACE</span><span>v0.2.0</span></div>
      </aside>
      <main className="workspace">
        <header className="app-header"><div><div className="app-heading"><h1>{title}</h1></div><p className="page-description">{description}</p></div><div className="header-actions"><button className="icon-button" aria-label={theme === "light" ? "다크 모드로 전환" : "라이트 모드로 전환"} title={theme === "light" ? "다크 모드" : "라이트 모드"} onClick={() => setTheme(theme === "light" ? "dark" : "light")}><Icon name={theme === "light" ? "moon" : "sun"} size={19} /></button><span className="admin-avatar" aria-hidden="true">A</span><button className="secondary logout-button" onClick={async () => { await api.logout(); onUnauthorized(); }}><Icon name="logout" size={16} />로그아웃</button></div></header>
        <div className="content" id="workspace-content" tabIndex={-1}>
          {mode === "stub" && <div className="runtime-banner"><Icon name="test" size={18} /><div><strong>로컬 모의 분석 모드</strong><span>API 설정이 stub입니다. 모의 결과는 실제 LLM의 보안 판정이 아니며, worker의 실제 실행 상태는 별도 확인이 필요합니다.</span></div><span className="runtime-tag">STUB</span></div>}
          {summaryError && page !== "dashboard" && <div className="error" role="alert">실행 설정을 확인하지 못했습니다. {summaryError}</div>}
          {page === "dashboard" && <DashboardView onOpen={openDetail} onUnauthorized={onUnauthorized} days={days} onDaysChange={setDays} serviceApiKeyId={serviceApiKeyId} onKeyChange={value => navigation.remember(current => ({ ...current, serviceApiKeyId: value }))} onFilter={filterProduction} Table={AnalysisTable} />}
          {page === "analyses" && <AnalysisResultsPage purpose={resultsPurpose} onScopeChange={changeResultsScope} state={listState} setState={setListState} testState={testResults} setTestState={setTestResults} onSelectRun={openTestRun} onOpenRunItem={openRunItem} onOpen={openDetail} onUnauthorized={onUnauthorized} onShowTestRuns={backToTests} onShowAllTestItems={showAllTestItems} />}
          {page === "test" && <TestAnalysisPage onViewTests={viewTests} onOpen={openTest} selectedRunId={null} onSelectRun={openTestRun} agentMode={mode} onConfigureModels={() => move(current => ({ ...current, page: "settings", settingsTab: "models" }))} />}
          {page === "apiDocs" && <ProductionApi />}
          {page === "settings" && <Settings onProductionChange={setProductionName} onViewDataset={viewDataset} tab={settingsTab} onTabChange={tab => move(current => ({ ...current, settingsTab: tab }))} />}
          {page === "detail" && <Detail key={selectedId} id={selectedId} onBack={backFromDetail} onOpen={id => move(current => ({ ...current, selectedId: id, detailTab: "result" }))} tab={detailTab} onTabChange={tab => navigation.navigate(current => ({ ...current, detailTab: tab }))} backLabel={detailReturnPage === "testRun" ? "테스트 실행 결과" : detailReturnPage === "test" ? "테스트 분석" : detailReturnPage === "dashboard" ? "대시보드" : "분석 결과"} />}
        </div>
      </main>
    </div>
  );
}
