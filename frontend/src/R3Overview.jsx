import { useEffect, useState } from "react";
import { api } from "./api.js";
import { startVisiblePolling } from "./visiblePolling.js";
import { Section, MetricStrip, OfficialMetrics, ConfigurationRows, useWords } from "./LifecycleViews.jsx";
import DataTable from "./DataTable.jsx";
import { formatDate, formatDuration } from "./analysisView.js";
import { Icon } from "./Icon.jsx";

function useObservation(read, dependencies = []) {
  const [state, setState] = useState({ value: null, error: false });
  useEffect(() => { setState({ value: null, error: false }); return startVisiblePolling(async signal => { const value = await read({ signal }); if (!signal.aborted) setState({ value, error: false }); return Boolean(value?.queue?.pending || value?.queue?.processing); }, { onError: () => setState({ value: null, error: true }) }); }, dependencies);
  return state;
}
const percent = value => typeof value === "number" ? `${(value * 100).toFixed(1)}%` : "—";

export function OfficialTrend({ points }) {
  const w = useWords();
  const series = [["accuracy", "Accuracy"], ["f1", "F1"], ["coverage", "Coverage"]];
  const x = index => 45 + index * 600 / Math.max(1, points.length - 1), y = value => 165 - value * 145;
  return <><svg className="r3-evaluation-chart" viewBox="0 0 680 200" role="img" aria-label={w("같은 평가 기준의 운영 구성별 Accuracy, F1, Coverage 추이", "Accuracy, F1 and Coverage across Production configurations with matching evaluation scope")}>
    {[0, .25, .5, .75, 1].map(value => <g key={value}><line x1="45" x2="645" y1={y(value)} y2={y(value)} /><text x="35" y={y(value) + 4} textAnchor="end">{value * 100}%</text></g>)}
    {series.map(([key]) => <g key={key} className={`r3-series-${key}`}>
      {points.slice(1).map((point, i) => { const before = points[i].summary?.evaluation_summary?.metrics?.[key], after = point.summary?.evaluation_summary?.metrics?.[key]; return typeof before === "number" && typeof after === "number" ? <line key={point.id} x1={x(i)} y1={y(before)} x2={x(i + 1)} y2={y(after)} /> : null; })}
      {points.map((point, i) => { const value = point.summary?.evaluation_summary?.metrics?.[key]; return typeof value === "number" ? <circle key={point.id} cx={x(i)} cy={y(value)} r="4"><title>{point.summary?.name} · {key} {percent(value)}</title></circle> : null; })}</g>)}
    {points.map((point, index) => <text key={point.id} x={x(index)} y="190" textAnchor="middle">{new Date(point.created_at).toLocaleDateString([], { month: "short", day: "numeric" })}</text>)}
  </svg><div className="v5-chart-legend">{series.map(([key, label]) => <span key={key}><i className={`r3-series-${key}`} />{label}</span>)}</div></>;
}

export default function R3Overview({ onNavigate, onOpenRun, onPromotion, onGroundTruth, onFailures }) {
  const w = useWords(); const [key, setKey] = useState("");
  const overview = useObservation(api.overview), activity = useObservation(options => api.activity({ limit: 4 }, options));
  const keys = useObservation(api.serviceApiKeys);
  const runtime = useObservation(options => api.runtimeStatus("24h", options, { purpose: "production", ...(key ? { service_api_key_id: key } : {}) }), [key]);
  const data = overview.value, production = data?.production, r = runtime.value, draft = data?.ground_truth_working_draft;
  const terminal = (r?.outcome_summary?.completed || 0) + (r?.outcome_summary?.failed || 0);
  const comparable = Boolean(data?.comparison_key);
  const points = (data?.trend || []).filter(point => point.summary?.ground_truth?.comparison_key === data?.comparison_key);
  const actionNames = { ground_truth: ["답안·입력 확인", "Check Ground Truth cases"], failed_tests: ["실패한 테스트 실행", "Failed test executions"], failed_inference: ["실패한 운영 추론", "Failed Production inference"], promotion: ["운영 승격 검토", "Review Production promotion"] };
  function openAction(action) { if (action.kind === "ground_truth") onGroundTruth(action.dataset_id, "needs_attention"); else if (action.kind === "promotion") onPromotion(action.test_run_id); else onFailures(action.kind === "failed_tests" ? "test" : "production"); }
  return <div className="page-stack r3-overview">
    {(overview.error || runtime.error) && <p className="error" role="alert">{w("운영 정보를 불러오지 못했습니다. 자동으로 다시 확인합니다.", "Could not load Production information. Retrying automatically.")}</p>}
    <section className="r3-production-strip"><div><span className="eyebrow">Production</span><strong>{production ? production.profile_names?.[production.snapshot?.primary?.profile_id] || w("모델 미지정", "No model assigned") : w("미확인", "Unknown")}</strong><small>{production?.drifted ? w("구성 확인 필요", "Configuration drift") : production?.configuration_id ? w("승격된 구성", "Promoted configuration") : w("기존 운영 구성", "Previous Production configuration")}</small></div><div><span>{w("분석 worker", "Analysis worker")}</span><strong className={r?.health?.analysis_worker?.status === "healthy" ? "r3-good" : ""}>{r?.health?.analysis_worker?.status === "healthy" ? w("응답 확인", "Responding") : r?.health?.analysis_worker?.status === "stale" ? w("응답 지연", "Stale") : w("미확인", "Unknown")}</strong><small>{formatDate(r?.health?.analysis_worker?.observed_at)}</small></div><div><span>{w("대기 / 처리 중", "Queue / processing")}</span><strong>{r?.queue?.pending ?? "—"} / {r?.queue?.processing ?? "—"}</strong></div><div><span>{w("최근 24시간 요청", "Requests · 24h")}</span><strong>{r?.request_count ?? "—"}</strong><small>p95 {formatDuration(r?.latency_summary?.p95, "—")} · {w("실패", "Failed")} {terminal ? percent(r.outcome_summary.failed / terminal) : "—"}</small></div><button className="text-button" onClick={() => onNavigate("runtime")}>Runtime →</button></section>
    <div className="r3-overview-toolbar"><label>API Key<select value={key} onChange={e => setKey(e.target.value)}><option value="">{w("전체 운영 키", "All Production keys")}</option>{keys.value?.items.filter(row => row.purpose === "production").map(row => <option key={row.id} value={row.id}>{row.name}</option>)}</select></label><span className="v5-context">{w("키 선택은 요청·실패·대기에만 적용합니다.", "Key selection affects request, failure and queue counts only.")}</span><small className="ux-grow">{w("갱신", "Updated")} {formatDate(r?.updated_at)}</small></div>
    <div className="r3-overview-grid"><Section title={points.length >= 2 ? w("운영 평가 추이", "Production Evaluation Trend") : w("현재 공식 평가", "Current Official Evaluation")} action={<button className="text-button" onClick={() => onNavigate("quality")}>{w("평가 상세", "View evaluation")} →</button>}>
      {points.length >= 2 ? <><p className="v5-context">{data.evaluation?.summary?.ground_truth?.dataset_name} · r{data.evaluation?.summary?.ground_truth?.dataset_revision} · {w("같은 문항·평가 범위의 운영 구성만 비교", "Matching revision and evaluation scope only")}</p><OfficialTrend points={points} /></> : <><OfficialMetrics evaluation={data?.evaluation} /><p className="v5-context">{w("같은 평가 기준의 운영 구성이 두 개 이상일 때 추이를 표시합니다.", "A trend requires at least two Production configurations evaluated on the same basis.")}</p></>}
    </Section><Section title={w("Ground Truth 작업 초안", "Ground Truth Working Draft")} action={draft && <button className="text-button" onClick={() => onGroundTruth(draft.id)}>{w("초안 열기", "Open draft")} →</button>}>
      {!draft ? <p className="v5-empty">{w("현재 공식 평가에 연결된 데이터셋이 없습니다. 다른 데이터셋을 임의로 표시하지 않습니다.", "No dataset is linked to the current official evaluation. No substitute dataset is selected.")}</p> : <><h3 className="r3-draft-title">{draft.name}</h3><div className="r3-stacked-bar" role="img" aria-label={w(`준비됨 ${draft.counts.ready}, 확인 필요 ${draft.counts.needs_attention}, 제외 ${draft.counts.excluded}`, `Ready ${draft.counts.ready}, needs attention ${draft.counts.needs_attention}, excluded ${draft.counts.excluded}`)}>{["ready", "needs_attention", "excluded"].map(state => <span key={state} className={state} style={{ width: `${draft.total ? draft.counts[state] / draft.total * 100 : 0}%` }} />)}</div><dl className="r3-quality-counts">{[["ready", "준비됨", "Ready"], ["needs_attention", "확인 필요", "Needs attention"], ["excluded", "제외", "Excluded"]].map(([state, ko, en]) => <div key={state}><dt><i className={state} />{w(ko, en)}</dt><dd>{draft.counts[state]}</dd></div>)}</dl><p className="v5-context">{w("초안 변경", "Working changes")} {draft.working_changes_count} · {w("최근 발행", "Latest published")} {draft.published_revisions[0] ? `r${draft.published_revisions[0].revision}` : "—"}</p></>}
    </Section></div>
    <Section title={w("같은 기준의 최근 테스트", "Recent Comparable Tests")} action={<button className="text-button" onClick={() => onNavigate("runs")}>{w("전체 테스트", "All tests")} →</button>}><DataTable label={w("비교 가능한 테스트", "Comparable tests")} data={comparable ? data.recent_comparable_tests.filter(row => row.summary?.ground_truth?.comparison_key === data.comparison_key) : []} columns={[
      { id: "name", header: w("테스트", "Test"), render: row => <button className="text-button" onClick={() => onOpenRun(row.test_run_id)}>{row.summary?.name}</button> },
      { id: "configuration", header: w("구성", "Configuration"), render: row => row.summary?.profile_metadata?.model_name || "—" },
      { id: "f1", header: "F1", render: row => <span className="r3-mini-metric"><i style={{ width: `${(row.summary?.evaluation_summary?.metrics?.f1 || 0) * 100}%` }} />{percent(row.summary?.evaluation_summary?.metrics?.f1)}</span> },
      { id: "accuracy", header: "Accuracy", render: row => percent(row.summary?.evaluation_summary?.metrics?.accuracy) },
      { id: "coverage", header: "Coverage", render: row => percent(row.summary?.evaluation_summary?.metrics?.coverage) },
      { id: "time", header: w("평가 시각", "Evaluated"), render: row => formatDate(row.created_at) },
    ]} empty={w("같은 발행 리비전·평가 범위의 테스트가 없습니다.", "No tests share this published revision and evaluation scope.")} /></Section>
    <div className="r3-overview-grid"><Section title="Action Center"><ul className="r3-action-list">{data?.actions.map((action, index) => <li key={index}><button className={action.kind === "promotion" ? "positive" : ""} onClick={() => openAction(action)}><Icon name={action.kind === "promotion" ? "check" : "alert"} size={18} /><span>{w(...actionNames[action.kind])}<small>{action.name}</small></span><strong>{action.count ?? ""}</strong><span>→</span></button></li>)}</ul>{data && !data.actions.length && <p className="v5-empty">{w("지금 확인할 항목이 없습니다.", "No actions need attention.")}</p>}</Section><Section title={w("최근 변경", "Recent Activity")} action={<button className="text-button" onClick={() => onNavigate("changes")}>{w("전체 보기", "View all")} →</button>}><ul className="v5-activity-list">{activity.value?.items.map(row => <li key={row.id}><time>{formatDate(row.created_at)}</time><strong>{({ promotion: w("운영 승격", "Promotion"), configuration: w("구성 변경", "Configuration"), runtime: "Runtime", integration: w("연동 변경", "Integration") })[row.category] || w("변경 기록", "Change")}</strong></li>)}</ul>{activity.error && <p className="error">{w("변경 이력을 조회하지 못했습니다.", "Could not load activity.")}</p>}{activity.value?.items.length === 0 && <p className="v5-empty">{w("기록된 변경이 없습니다.", "No changes recorded.")}</p>}</Section></div>
    <Section title={w("현재 운영 구성", "Current Production Configuration")} action={<button className="text-button" onClick={() => onNavigate("agents")}>{w("구성 상세", "Configuration details")} →</button>}><ConfigurationRows snapshot={production?.snapshot} names={production?.profile_names} versions={production?.version_names} /></Section>
  </div>;
}
