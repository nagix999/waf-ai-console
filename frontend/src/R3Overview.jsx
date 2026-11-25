import NavigationAction from "./NavigationAction.jsx";
import { useEffect, useRef, useState } from "react";
import { api } from "./api.js";
import { startVisiblePolling } from "./visiblePolling.js";
import { HOME_INVALIDATED } from "./readInvalidation.js";
import { Section, MetricStrip, OfficialMetrics, ConfigurationRows, useWords } from "./LifecycleViews.jsx";
import DataTable from "./DataTable.jsx";
import { formatDate, formatDuration } from "./analysisView.js";
import SetupProgress from "./R5Setup.jsx";
import { Icon } from "./Icon.jsx";
import SummaryPreview from "./SummaryPreview.jsx";
import { dashboardAnalysisQuery } from "./dashboardView.js";
import "./overviewEvaluationDesign.css";

function useObservation(read, dependencies = [], interval = null) {
  const [state, setState] = useState({ value: null, error: false });
  useEffect(() => { setState({ value: null, error: false }); return startVisiblePolling(async signal => { const value = await read({ signal }); if (!signal.aborted) setState({ value, error: false }); return Boolean(value?.queue?.pending || value?.queue?.processing); }, { interval, activeInterval: 5000, invalidationEvent: HOME_INVALIDATED, onError: () => setState({ value: null, error: true }) }); }, dependencies);
  return state;
}
const percent = value => typeof value === "number" ? `${(value * 100).toFixed(1)}%` : "—";

export function OverviewMetric({ label, value, note, tone = "purple", icon = "chart", onClick }) {
  const Tag = onClick ? "button" : "div";
  return <Tag type={onClick ? "button" : undefined} onClick={onClick} className="oe-metric" data-tone={tone}><div className="oe-metric-top"><span>{label}</span><span className="oe-metric-icon"><Icon name={icon} size={18} /></span></div><strong>{value ?? "—"}</strong>{note && <small>{note}</small>}</Tag>;
}

export function HomeRequestVolume({ summary, words: w }) {
  const points = summary?.trend || [], max = Math.max(1, ...points.map(point => point.total || 0));
  return <div className="oe-home-volume">{points.length ? <div className="oe-home-volume-chart" role="list" aria-label={w("접수일별 운영 요청 수", "Production requests by admission day")}><div className="oe-home-volume-grid" aria-hidden="true"><span>{max}</span><span>{Math.round(max / 2)}</span><span>0</span></div><div className="oe-home-volume-bars">{points.map(point => <div key={point.date} className="oe-home-volume-column" role="listitem" aria-label={`${point.date}: ${point.total ?? 0}`}><span style={{ height: `${(point.total || 0) / max * 100}%` }} title={`${point.date}: ${point.total ?? 0}`} /><small>{point.date?.slice(5)}</small></div>)}</div></div> : <p className="oe-home-chart-empty">{summary ? w("표시할 접수 기록이 없습니다.", "No request records are available.") : w("접수 현황을 불러오는 중…", "Loading request volume…")}</p>}<p className="oe-home-chart-note"><i />{w("최초 접수 기준 · 재실행 제외 · 접수일 UTC", "Original requests only · retries excluded · admission dates in UTC")}</p></div>;
}

export function HomeVerdictDistribution({ counts, words: w, onSelect }) {
  const rows = [["true_positive", w("정탐", "True positive"), "var(--danger)"], ["false_positive", w("오탐", "False positive"), "var(--teal)"], ["inconclusive", w("보류", "Inconclusive"), "var(--warn)"]];
  const completed = counts?.completed;
  let offset = 0;
  return <div className="oe-home-verdict"><div className="oe-verdict-ring"><svg viewBox="0 0 120 120" role="img" aria-label={rows.map(([key, label]) => `${label} ${counts?.[key] ?? "—"}`).join(", ")}><circle cx="60" cy="60" r="47" fill="none" stroke="var(--line)" strokeWidth="12" />{rows.map(([key, , color]) => { const rate = completed > 0 ? (counts[key] || 0) / completed * 100 : 0, before = offset; offset += rate; return <circle key={key} cx="60" cy="60" r="47" fill="none" stroke={color} strokeWidth="12" pathLength="100" strokeDasharray={`${rate} ${100 - rate}`} strokeDashoffset={-before} transform="rotate(-90 60 60)" />; })}</svg><div><strong>{completed?.toLocaleString() ?? "—"}</strong><small>{w("판정 완료", "Completed")}</small></div></div><dl>{rows.map(([key, label, color]) => <div key={key}><dt><i style={{ background: color }} />{onSelect ? <button type="button" disabled={!counts} onClick={() => onSelect(key)}>{label}</button> : label}</dt><dd>{counts?.[key]?.toLocaleString() ?? "—"}<small>{completed > 0 ? `${Math.round((counts[key] || 0) / completed * 100)}%` : "—"}</small></dd></div>)}</dl></div>;
}

export function RuntimeVolume({ runtime, words: w }) {
  const series = runtime?.request_volume_series || [];
  const states = [["completed", w("처리 완료", "Completed")], ["failed", w("실행 실패", "Failed")], ["processing", w("처리 중", "Processing")], ["pending", w("대기", "Queued")], ["canceled", w("중지", "Stopped")]];
  const total = point => states.reduce((sum, [key]) => sum + (Number(point[key]) || 0), 0);
  const maximum = Math.max(1, ...series.map(total));
  const empty = Boolean(runtime) && series.length === 0;
  return <div className={`oe-volume${empty ? " is-empty" : ""}`}><div className="oe-chart-legend">{states.map(([key, label]) => <span key={key}><i className={`oe-state-${key}`} />{label}</span>)}</div>{series.length ? <div className="oe-volume-bars" role="list" aria-label={w("최근 24시간 접수 요청의 현재 처리 상태", "Current status of requests admitted over the last 24 hours")}>{series.map((point, index) => <div className="oe-volume-column" role="listitem" key={point.from || index} aria-label={`${formatDate(point.from)}: ${states.map(([key, label]) => `${label} ${point[key] || 0}`).join(", ")}`}><div className="oe-volume-stack">{states.map(([key]) => <span key={key} className={`oe-state-${key}`} style={{ height: `${(Number(point[key]) || 0) / maximum * 100}%` }} />)}</div><span className="oe-volume-label">{index % 3 === 0 || index === series.length - 1 ? new Date(point.from).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }) : ""}</span></div>)}</div> : <p className="oe-chart-empty">{!runtime ? w("접수 현황을 불러오는 중입니다.", "Loading request volume.") : runtime.request_count === 0 ? w("최근 24시간에 접수한 요청이 없습니다.", "No requests were admitted in the last 24 hours.") : w("시간대별 집계 정보가 없습니다.", "Hourly request data is not available.")}</p>}<p className="v5-context">{w("접수 시간대별 요청 수와 현재 상태입니다. 완료된 시간대의 처리량을 뜻하지 않습니다.", "Requests are grouped by admission time and current status, not by completion time.")}</p></div>;
}

export function RuntimeDistribution({ runtime, words: w }) {
  const rows = [["completed", w("처리 완료", "Completed")], ["processing", w("처리 중", "Processing")], ["pending", w("대기", "Queued")], ["failed", w("실행 실패", "Failed")], ["canceled", w("중지", "Stopped")]];
  const count = runtime?.request_count;
  return <div className="oe-distribution"><div className="oe-distribution-total"><Icon name="shield" size={22} /><strong>{count?.toLocaleString() ?? "—"}</strong><span>{w("최근 24시간 접수", "Admitted · last 24h")}</span></div><div className="oe-distribution-stack" aria-hidden="true">{rows.map(([key]) => <span key={key} className={`oe-state-${key}`} style={{ width: `${count ? (runtime.outcome_summary?.[key] || 0) / count * 100 : 0}%` }} />)}</div><dl>{rows.map(([key, label]) => <div key={key}><dt><i className={`oe-state-${key}`} />{label}</dt><dd>{runtime?.outcome_summary?.[key]?.toLocaleString() ?? "—"}</dd></div>)}</dl></div>;
}

export function OfficialTrend({ points }) {
  const w = useWords();
  const chart = useRef(null); const [width, setWidth] = useState(680);
  useEffect(() => {
    if (!chart.current || typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(([entry]) => setWidth(Math.max(240, Math.round(entry.contentRect.width))));
    observer.observe(chart.current); return () => observer.disconnect();
  }, []);
  const series = [["accuracy", "Accuracy"], ["f1", "F1"], ["coverage", "Coverage"]];
  const x = index => 45 + index * (width - 80) / Math.max(1, points.length - 1), y = value => 165 - value * 145;
  const labelEvery = Math.max(1, Math.ceil(points.length / Math.max(2, Math.floor(width / 90))));
  return <><svg ref={chart} className="r3-evaluation-chart" viewBox={`0 0 ${width} 200`} role="img" aria-label={w("같은 평가 기준의 운영 구성별 Accuracy, F1, Coverage 추이", "Accuracy, F1 and Coverage across Production configurations with matching evaluation scope")}>
    {[0, .25, .5, .75, 1].map(value => <g key={value}><line x1="45" x2={width - 35} y1={y(value)} y2={y(value)} /><text x="35" y={y(value) + 4} textAnchor="end">{value * 100}%</text></g>)}
    {series.map(([key]) => <g key={key} className={`r3-series-${key}`}>
      {points.slice(1).map((point, i) => { const before = points[i].summary?.evaluation_summary?.metrics?.[key], after = point.summary?.evaluation_summary?.metrics?.[key]; return typeof before === "number" && typeof after === "number" ? <line key={point.id} x1={x(i)} y1={y(before)} x2={x(i + 1)} y2={y(after)} /> : null; })}
      {points.map((point, i) => { const value = point.summary?.evaluation_summary?.metrics?.[key]; return typeof value === "number" ? <circle key={point.id} cx={x(i)} cy={y(value)} r="4"><title>{point.summary?.name} · {key} {percent(value)}</title></circle> : null; })}</g>)}
    {points.map((point, index) => index % labelEvery === 0 || index === points.length - 1 ? <text key={point.id} x={x(index)} y="190" textAnchor="middle">{new Date(point.created_at).toLocaleDateString([], { month: "short", day: "numeric" })}</text> : null)}
  </svg><div className="v5-chart-legend">{series.map(([key, label]) => <span key={key}><i className={`r3-series-${key}`} />{label}</span>)}</div></>;
}

export default function R3Overview({ onNavigate, onOpenRun, onPromotion, onGroundTruth, onFailures, onOpenAnalysis, onNewTest, onFilterProduction }) {
  const w = useWords(); const [key, setKey] = useState(""), [days, setDays] = useState(7);
  const [refresh, setRefresh] = useState(0), [contextRevision, setContextRevision] = useState(0);
  const observedRevision = useRef(null);
  const overview = useObservation(api.overview, [refresh, contextRevision]);
  const activity = useObservation(options => api.activity({ limit: 4, include_home_revision: true }, options), [refresh], 60000);
  useEffect(() => {
    const revision = activity.value?.home_revision;
    if (!revision) return;
    if (observedRevision.current && observedRevision.current !== revision) setContextRevision(value => value + 1);
    observedRevision.current = revision;
  }, [activity.value?.home_revision]);
  const keys = useObservation(api.serviceApiKeys, [refresh]);
  const live = useObservation(async options => {
    const summary = await api.dashboard(days, options, key), query = dashboardAnalysisQuery(summary, key);
    if (!query || !summary.counts) throw Error("invalid_dashboard_summary");
    const recent = await api.analyses({ ...query, include_retries: false, limit: 6 }, options);
    if (!Array.isArray(recent?.items)) throw Error("invalid_recent_analyses");
    return { summary, recent: recent.items, queue: { pending: summary.counts.pending, processing: summary.counts.processing } };
  }, [key, days, refresh], 30000);
  const data = overview.value, production = data?.production, draft = data?.ground_truth_working_draft;
  const summary = live.value?.summary, counts = summary?.counts, recent = live.value?.recent || [];
  const points = (data?.trend || []).filter(point => point.summary?.ground_truth?.comparison_key === data?.comparison_key);
  const actionNames = { production_credentials: ["운영 API 키 발급", "Issue a Production API key"], production_traffic: ["운영 API 요청 확인", "Check Production API traffic"], ground_truth: ["기대 판정과 입력 확인", "Check dataset cases"], failed_tests: ["실패한 테스트 실행", "Failed test executions"], failed_inference: ["실패한 운영 추론", "Failed Production inference"], promotion: ["운영 반영 검토", "Production Review"] };
  const number = name => counts?.[name]?.toLocaleString() ?? "—";
  const filter = values => {
    if (!summary) return;
    if (onFilterProduction) onFilterProduction({ ...values, analysis_purpose: "production", include_retries: false, created_from: summary.window.created_from, created_to: summary.window.created_to, ...(key ? { service_api_key_id: key } : {}) });
    else onNavigate("history");
  };
  function openAction(action) {
    if (action.kind === "production_credentials") onNavigate("keys");
    else if (action.kind === "production_traffic") onNavigate("api");
    else if (action.kind === "ground_truth") onGroundTruth(action.dataset_id, "needs_attention");
    else if (action.kind === "promotion") onPromotion(action.test_run_id);
    else onFailures(action.kind === "failed_tests" ? "test" : "production");
  }
  const modelName = role => production?.snapshot?.[role]?.profile_id ? production.profile_names?.[production.snapshot[role].profile_id] || w("프로필 정보 없음", "Profile unavailable") : w("미지정", "Not assigned");
  const configuration = [
    [w("주 분석 모델", "Primary"), modelName("primary")], [w("검증 모델", "Verifier"), modelName("verifier")],
    [w("분석 지침", "Analysis Instructions"), production?.version_names?.[production?.snapshot?.prompt?.policy_version_id] || production?.snapshot?.prompt?.prompt_version || "—"],
    [w("입력 스키마", "Input Schema"), production?.version_names?.[production?.snapshot?.input_schema?.version_id] || w("미확인", "Unknown")],
  ];
  const openAnalysis = row => onOpenAnalysis ? onOpenAnalysis(row.id) : onNavigate("history");
  const completionRate = counts?.total > 0 ? counts.completed / counts.total : null;
  const showSetupFirst = data?.setup && (data.setup.production_state !== "promoted" || data.setup.drifted);
  return <div className="page-stack r3-overview oe-overview oe-home-v2">
    <header className="oe-home-heading"><div><span className="eyebrow">YOUR SECURITY WORKSPACE</span><h1>{w("운영 현황", "Production overview")}</h1><p>{w("판정과 처리 상태를 확인하고, 필요한 분석으로 바로 이동하세요.", "Review decisions and processing, then open the analyses that need attention.")}</p></div><div className="oe-home-heading-actions"><label><span className="sr-only">{w("기간", "Period")}</span><select aria-label={w("기간", "Period")} value={days} onChange={event => setDays(Number(event.target.value))}>{[1, 7, 30].map(value => <option key={value} value={value}>{value === 1 ? w("최근 24시간", "Last 24 hours") : w("최근 " + value + "일", "Last " + value + " days")}</option>)}</select></label><label><span className="sr-only">{w("API 키", "API key")}</span><select aria-label={w("API 키", "API key")} value={key} onChange={event => setKey(event.target.value)}><option value="">{w("모든 운영 API 키", "All Production keys")}</option>{keys.value?.items?.filter(row => row.purpose === "production").map(row => <option key={row.id} value={row.id}>{row.name}</option>)}</select></label><button type="button" className="primary" onClick={() => onNewTest ? onNewTest() : onNavigate("run")}><Icon name="test" size={16} />{w("새 테스트", "New Test")}</button></div></header>
    {showSetupFirst && <SetupProgress setup={data.setup} onNavigate={onNavigate} onPromotion={onPromotion} />}
    {(overview.error || live.error || keys.error) && <p className="error" role="alert">{w("현황을 불러오지 못했습니다. 다시 조회하거나 키 선택을 확인해 주세요.", "Could not load the overview. Refresh or check the selected key.")}<button type="button" className="text-button" onClick={() => setRefresh(value => value + 1)}>{w("다시 조회", "Retry")}</button></p>}
    <div className="oe-metrics">
      <OverviewMetric label={w("접수한 분석", "Admitted analyses")} value={number("total")} note={w("처리 완료 " + number("completed") + "건", number("completed") + " completed")} tone="blue" icon="shield" onClick={counts ? () => filter({}) : undefined} />
      <OverviewMetric label={w("판정 보류", "Inconclusive")} value={number("inconclusive")} note={w("확정에 필요한 조건을 확인하세요.", "Review the conditions needed for a decision.")} tone="amber" icon="clock" onClick={counts ? () => filter({ status: "completed", verdict: "inconclusive" }) : undefined} />
      <OverviewMetric label={w("실행 실패", "Execution failures")} value={number("failed")} note={w("실패 당시 설정으로 재실행할 수 있습니다.", "Retry using the saved configuration.")} tone="red" icon="alert" onClick={counts ? () => filter({ status: "failed" }) : undefined} />
      <OverviewMetric label={w("처리 완료율", "Completion rate")} value={percent(completionRate)} note={w("선택한 기간에 접수한 분석 기준", "Based on requests admitted in the selected period")} tone="teal" icon="check" />
    </div>
    <p className="oe-home-scope">{w("선택한 키와 기간의 운영 분석입니다. 재실행은 원본과 중복 집계하지 않습니다. 완료율은 판정 정확도가 아닙니다.", "Production analyses for the selected key and period. Retries are not counted again. Completion rate is not classification accuracy.")}</p>
    <div className="oe-home-chart-grid"><Section title={w("접수 추이", "Request volume")} action={<span className="oe-inline-count">{number("total")}{w("건", " requests")}</span>}><HomeRequestVolume summary={summary} words={w} /></Section><Section title={w("판정 분포", "Verdict distribution")} context={w("처리 완료 문항 기준", "Completed cases only")}><HomeVerdictDistribution counts={counts} words={w} onSelect={value => filter({ status: "completed", verdict: value })} /></Section></div>
    <div className="oe-home-bottom"><Section className="oe-home-recent" title={w("최근 분석", "Recent analyses")} action={<NavigationAction onClick={() => filter({})}>{w("전체 분석", "All analyses")}</NavigationAction>}><DataTable label={w("최근 운영 분석", "Recent Production analyses")} data={recent} columns={[
      { id: "request", header: w("요청과 분석 요약", "Request and summary"), width: "38%", render: row => <><button type="button" className="text-button" onClick={() => openAnalysis(row)}>{row.signature || row.event_name || row.company_name || w("분석 결과", "Analysis result")}</button><SummaryPreview text={row.summary_ko} /></> },
      { id: "company", header: w("회사", "Company"), render: row => row.company_name || "—" },
      { id: "verdict", header: w("판정", "Verdict"), width: 112, render: row => row.status === "completed" ? <span className={"oe-verdict-badge oe-verdict-" + row.verdict}>{({ true_positive: w("정탐", "True positive"), false_positive: w("오탐", "False positive"), inconclusive: w("보류", "Inconclusive") })[row.verdict] || "—"}</span> : <span className={"status status-" + row.status}>{({ failed: w("실행 실패", "Failed"), pending: w("대기", "Queued"), processing: w("처리 중", "Processing"), canceled: w("중지", "Stopped") })[row.status] || row.status}</span> },
      { id: "severity", header: w("심각도", "Severity"), width: 94, render: row => row.severity ? <span className={"severity severity-" + row.severity.toLowerCase()}>{row.severity}</span> : "—" },
      { id: "time", header: w("접수 → 판정 완료", "Admission → decision"), width: 126, className: "numeric", render: row => row.status === "completed" ? formatDuration(row.total_elapsed_ms, "—") : "—" },
    ]} empty={live.error ? w("최근 분석을 조회하지 못했습니다.", "Could not load recent analyses.") : summary ? w("선택한 조건에 맞는 분석이 없습니다.", "No analyses match this scope.") : w("최근 분석을 불러오는 중…", "Loading recent analyses…")} /><p className="oe-home-table-note">{w("최근 6건입니다. 시간은 초기 대기를 포함한 경과 시간이며 HTTP 요청부터 응답까지의 시간은 아닙니다.", "Shows the 6 most recent requests. Duration includes initial waiting and is not HTTP request-to-response time.")}</p></Section>
      <aside className="oe-home-aside"><Section title={w("할 일", "Action Center")}><ul className="oe-home-tasks">
        <li><span className="oe-task-icon red"><Icon name="refresh" size={17} /></span><div><strong>{w("완료하지 못한 분석", "Unfinished analyses")} {number("failed")}{w("건", "")}</strong><p>{w("실패 이유와 재실행 대상을 확인하세요.", "Review failures and choose cases to retry.")}</p></div><button type="button" className="oe-task-open" aria-label={w("실패한 분석 열기", "Open failed analyses")} disabled={!counts} onClick={() => filter({ status: "failed" })}><Icon name="arrow" size={17} /></button></li>
        <li><span className="oe-task-icon"><Icon name="file" size={17} /></span><div><strong>{w("접수된 피드백", "Submitted feedback")}</strong><p>{w("근거와 설명에 대한 의견을 확인하세요.", "Review feedback on evidence and explanations.")}</p></div><button type="button" className="oe-task-open" aria-label={w("운영 피드백 열기", "Open Production feedback")} onClick={() => onNavigate("feedback")}><Icon name="arrow" size={17} /></button></li>
        <li><span className="oe-task-icon amber"><Icon name="clock" size={17} /></span><div><strong>{w("판정 보류", "Inconclusive")} {number("inconclusive")}{w("건", "")}</strong><p>{w("확정에 필요한 조건을 확인하세요.", "Review the conditions needed for a decision.")}</p></div><button type="button" className="oe-task-open" aria-label={w("보류 분석 열기", "Open inconclusive analyses")} disabled={!counts} onClick={() => filter({ status: "completed", verdict: "inconclusive" })}><Icon name="arrow" size={17} /></button></li>
      </ul>{Boolean(data?.actions?.length) && <details className="oe-home-review-actions"><summary>{w("설정과 평가 확인", "Configuration and evaluation actions")}</summary><ul className="r3-action-list">{data.actions.map((action, index) => <li key={index}>{action.kind === "ground_truth" && action.datasets?.length ? action.datasets.map(dataset => <button key={dataset.dataset_id} type="button" onClick={() => onGroundTruth(dataset.dataset_id, "needs_attention")}><span>{dataset.name}</span><strong>{dataset.count}</strong><Icon name="arrow" size={15} /></button>) : <button type="button" onClick={() => openAction(action)}><span>{w(...(actionNames[action.kind] || [w("실행 상태 확인", "Review execution"), "Review execution"]))}<small>{action.name}</small></span><strong>{action.count ?? ""}</strong><Icon name="arrow" size={15} /></button>}</li>)}</ul></details>}</Section>
      <Section title={w("현재 운영 설정", "Current Production configuration")}><dl className="oe-compact-config">{configuration.map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{value}</dd></div>)}</dl>{production?.drifted && <p className="notice">{w("운영 설정과 반영 기록을 확인하세요.", "Review the current configuration and application record.")}</p>}<NavigationAction onClick={() => onNavigate("agents")}>{w("설정 보기", "View configuration")}</NavigationAction><details className="oe-home-all-config"><summary>{w("모든 설정", "All configuration fields")}</summary><ConfigurationRows snapshot={production?.snapshot} names={production?.profile_names} versions={production?.version_names} />{!showSetupFirst && <SetupProgress setup={data?.setup} onNavigate={onNavigate} onPromotion={onPromotion} />}</details></Section></aside>
    </div>
    <details className="oe-home-official"><summary><Icon name="chart" size={17} />{w("공식 평가와 변경 이력", "Official evaluation and activity")}</summary><div className="page-stack">
      <div className="r3-overview-grid"><Section title={points.length >= 2 ? w("운영 평가 추세", "Production evaluation trend") : w("현재 공식 평가", "Current official evaluation")} action={<NavigationAction onClick={() => onNavigate("quality")}>{w("평가 상세", "Evaluation details")}</NavigationAction>}>{points.length >= 2 ? <OfficialTrend points={points} /> : <OfficialMetrics evaluation={data?.evaluation} />}</Section>{draft && <Section title={w("평가 데이터셋 상태", "Evaluation Dataset status")} action={<NavigationAction onClick={() => onGroundTruth(draft.id)}>{w("초안 열기", "Open Draft")}</NavigationAction>}><h3 className="r3-draft-title">{draft.name}</h3><dl className="r3-quality-counts">{[["ready", "포함 가능", "Ready"], ["needs_attention", "확인 필요", "Needs attention"], ["excluded", "제외", "Excluded"]].map(([state, ko, en]) => <div key={state}><dt>{w(ko, en)}</dt><dd>{draft.counts[state]}</dd></div>)}</dl><p className="v5-context">{w("초안 변경", "Working changes")} {draft.working_changes_count} · {w("최근 공식 버전", "Latest published")} {draft.published_revisions?.[0] ? "r" + draft.published_revisions[0].revision : "—"}</p></Section>}</div>
      {data?.comparison_key && <Section title={w("최근 동일 기준 테스트", "Recent tests on the same baseline")}><DataTable label={w("비교 가능한 테스트", "Comparable tests")} data={(data.recent_comparable_tests || []).filter(row => row.summary?.ground_truth?.comparison_key === data.comparison_key)} columns={[{ id: "name", header: w("테스트", "Test"), render: row => <button className="text-button" onClick={() => onOpenRun(row.test_run_id)}>{row.summary?.name}</button> }, ...[["accuracy", "Accuracy"], ["f1", "F1"], ["coverage", "Coverage"]].map(([field, label]) => ({ id: field, header: label, className: "numeric", render: row => percent(row.summary?.evaluation_summary?.metrics?.[field]) })), { id: "date", header: w("평가 시각", "Evaluated"), render: row => formatDate(row.created_at) }]} /></Section>}
      <Section title={w("최근 변경", "Recent activity")} action={<NavigationAction onClick={() => onNavigate("changes")}>{w("전체 보기", "View all")}</NavigationAction>}><ul className="v5-activity-list">{activity.value?.items.map(row => <li key={row.id}><time>{formatDate(row.created_at)}</time><strong>{({ promotion: w("운영 반영", "Production application"), configuration: w("구성 변경", "Configuration"), runtime: "Runtime", integration: w("연동 변경", "Integration") })[row.category] || w("변경 기록", "Change")}</strong></li>)}</ul>{activity.error && <p className="error">{w("변경 이력을 조회하지 못했습니다.", "Could not load activity.")}</p>}</Section>
    </div></details>
  </div>;
}
