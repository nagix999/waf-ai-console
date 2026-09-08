import { useEffect, useState } from "react";
import { api } from "./api.js";
import { Icon } from "./Icon.jsx";
import HelpTooltip from "./HelpTooltip.jsx";
import Dialog from "./Dialog.jsx";
import EvaluationOverview from "./EvaluationOverview.jsx";
import { MetricHelp } from "./EvaluationMetrics.jsx";
import { metricText } from "./evaluationMetrics.js";
import { dashboardAnalysisQuery, metricSegments } from "./dashboardView.js";
import "./dashboardView.css";

export function EvaluationTrend({ trend = [] }) {
  const [metric, setMetric] = useState("accuracy"); const [tableOpen, setTableOpen] = useState(false);
  const labels = { accuracy: "Accuracy", precision: "Precision", recall: "Recall", f1: "F1 score", coverage: "판정 커버리지" };
  const segments = metricSegments(trend, metric);
  return <section className="panel trend-panel"><div className="panel-head"><h2>평가 추이</h2><label className="sr-only" htmlFor="trend-metric">추이 지표</label><select id="trend-metric" value={metric} onChange={event => setMetric(event.target.value)}>{Object.entries(labels).map(([value, label]) => <option value={value} key={value}>{label}</option>)}</select><MetricHelp metric={metric} label={labels[metric]} /></div>
    <div className="trend-body">{segments.length ? <svg className="evaluation-trend" viewBox="0 0 600 160" role="img" aria-label={`${labels[metric]} 일별 추이. 정확한 값은 일별 수치에서 확인할 수 있습니다.`}>
      {[0, 0.5, 1].map(value => <g key={value}><line x1="36" x2="586" y1={10 + (1 - value) * 130} y2={10 + (1 - value) * 130} className="trend-grid" /><text x="0" y={14 + (1 - value) * 130}>{value * 100}%</text></g>)}
      {segments.map((points, index) => <g key={index}><polyline fill="none" points={points.map(p => `${p.x},${p.y}`).join(" ")} />{points.map(point => <circle key={point.date} cx={point.x} cy={point.y} r="3"><title>{point.date}: {metricText(point.value)}</title></circle>)}</g>)}
    </svg> : <p className="trend-empty">평가 가능한 답안이 연결되면 그래프가 표시됩니다.</p>}
    <p className="ux-muted">답안 표본의 일별 지표 · 끊긴 구간은 계산할 표본 없음</p><div className="trend-foot"><span>{trend[0]?.date || "—"} ~ {trend.at(-1)?.date || "—"} · UTC</span><button className="text-button" type="button" onClick={() => setTableOpen(true)}>일별 수치</button></div></div>
    <Dialog open={tableOpen} title="일별 평가 수치" onClose={() => setTableOpen(false)}><div className="table-wrap"><table><thead><tr><th>접수일 (UTC)</th><th>전체 / 평가 / 확정</th><th>{labels[metric]}</th></tr></thead><tbody>{trend.map(day => <tr key={day.date}><td>{day.date}</td><td>{day.total} / {day.evaluation_summary?.binary_evaluable ?? 0} / {day.evaluation_summary?.binary_decided ?? 0}</td><td>{metricText(day.evaluation_summary?.metrics?.[metric])}</td></tr>)}</tbody></table></div></Dialog>
  </section>;
}

export default function DashboardView({ onOpen, onUnauthorized, days, onDaysChange, onFilter, serviceApiKeyId = "", onKeyChange, Table }) {
  const [keys, setKeys] = useState([]); const [keyError, setKeyError] = useState("");
  const [view, setView] = useState({ key: "", summary: null, items: [], error: "", loading: true });
  const requestKey = `${days}:${serviceApiKeyId}`;
  const current = view.key === requestKey ? view : { summary: null, items: [], error: "", loading: true };
  const { summary, items, error, loading } = current;
  useEffect(() => {
    let active = true; const controller = new AbortController(); let timer;
    async function loadKeys() {
      try { const result = await api.serviceApiKeys({ signal: controller.signal }); if (!Array.isArray(result?.items)) throw new Error("invalid_keys"); if (active) { setKeys(result.items); setKeyError(""); } }
      catch (err) { if (active) { setKeyError("키 목록을 불러오지 못했습니다."); if (err.status === 401) onUnauthorized(); } }
      finally { if (active) timer = setTimeout(loadKeys, 15000); }
    }
    void loadKeys(); return () => { active = false; controller.abort(); clearTimeout(timer); };
  }, [onUnauthorized]);
  useEffect(() => {
    let active = true; let timer; const controller = new AbortController();
    setView({ key: requestKey, summary: null, items: [], error: "", loading: true });
    async function load() {
      try {
        const next = await api.dashboard(days, { signal: controller.signal }, serviceApiKeyId);
        const query = dashboardAnalysisQuery(next, serviceApiKeyId);
        if (!query) throw new Error("invalid_summary");
        const recent = await api.analyses(query, { signal: controller.signal });
        if (active) setView({ key: requestKey, summary: next, items: recent.items, error: "", loading: false });
      } catch (err) { if (active) { setView({ key: requestKey, summary: null, items: [], error: err.status === 404 && serviceApiKeyId ? "삭제되었거나 사용할 수 없는 키입니다. 다른 키를 선택하세요." : "대시보드를 불러오지 못했습니다. 잠시 후 다시 확인하세요.", loading: false }); if (err.status === 401) onUnauthorized(); } }
      finally { if (active) timer = setTimeout(load, 15000); }
    }
    void load(); return () => { active = false; controller.abort(); clearTimeout(timer); };
  }, [requestKey, days, serviceApiKeyId, onUnauthorized]);
  const stats = summary?.counts;
  const number = key => stats?.[key]?.toLocaleString() ?? "—";
  const filter = values => summary && onFilter({ ...values, created_from: summary.window.created_from, created_to: summary.window.created_to, ...(serviceApiKeyId ? { service_api_key_id: serviceApiKeyId } : {}) });
  return <div className="page-stack dashboard-workspace">
    <div className="dashboard-filters"><label>연동 키<select aria-label="연동 키" value={serviceApiKeyId} onChange={event => onKeyChange(event.target.value)}><option value="">전체 프로덕션</option>{serviceApiKeyId && !keys.some(key => key.id === serviceApiKeyId) && <option value={serviceApiKeyId}>선택한 키 · 확인 필요</option>}{keys.map(key => <option key={key.id} value={key.id}>{key.name}</option>)}</select></label><label>기간<select value={days} onChange={event => onDaysChange(Number(event.target.value))}>{[7, 30, 90].map(value => <option key={value} value={value}>최근 {value}일</option>)}</select></label><span className="ux-muted">프로덕션 분석<HelpTooltip label="키별 집계">최초 접수에 사용한 API Key로 구분합니다. 과거 키 정보가 없는 분석과 삭제한 키의 분석은 전체에서만 확인합니다. 재실행은 원본과 중복 집계하지 않습니다.</HelpTooltip></span></div>
    {(error || keyError) && <p className="error" role="alert">{error || keyError}</p>}
    <section className="stats-grid">{[["total", "접수", "analyses"], ["completed", "완료", "check"], ["inconclusive", "보류", "clock"], ["failed", "실패", "alert"]].map(([key, label, icon]) => <article key={key}><div className="stat-top"><span>{label}</span><i className="stat-icon"><Icon name={icon} /></i></div><strong>{number(key)}<small>건</small></strong>{key === "total" && <small>대기 {number("pending")} · 진행 {number("processing")}</small>}{["inconclusive", "failed"].includes(key) && <button type="button" className="text-button" disabled={!stats} onClick={() => filter(key === "failed" ? { status: "failed" } : { verdict: "inconclusive", status: "completed" })}>결과 보기 →</button>}</article>)}</section>
    <div className="dashboard-priorities"><strong>우선 확인</strong><button type="button" disabled={!stats} onClick={() => filter({ verdict: "false_positive", waf_action: "D", status: "completed" })}>정상 요청 차단 <b>{number("false_positive_denied")}</b></button>{["CRITICAL", "HIGH"].map(severity => <button type="button" key={severity} disabled={!stats} onClick={() => filter({ verdict: "true_positive", waf_action: "A", status: "completed", severity })}>{severity} 허용 요청 →</button>)}<span className="ux-muted">고위험 허용 {number("critical_high_allowed")}건</span></div>
    <EvaluationOverview summary={summary?.evaluation_summary} loading={loading} error={error} scopeLabel="선택한 키·기간" />
    <div className="dashboard-grid"><section className="panel distribution-panel"><div className="panel-head"><h2>판정 분포</h2><span>완료 {number("completed")}건</span></div><div className="distribution-body">{[["true_positive", "정탐"], ["false_positive", "오탐"], ["inconclusive", "보류"]].map(([key, label]) => <button type="button" className={`distribution-row distribution-${key}`} key={key} disabled={!stats} onClick={() => filter({ verdict: key, status: "completed" })}><div><span><i />{label}</span><strong>{number(key)}건</strong></div><div className="distribution-track"><div style={{ width: `${stats?.completed ? stats[key] / stats.completed * 100 : 0}%` }} /></div></button>)}</div></section><EvaluationTrend trend={summary?.trend} /></div>
    <Table items={items} onOpen={onOpen} purpose="production" title="최근 분석" subtitle="선택한 키·기간의 최근 10건" loading={loading} />
    {!!summary?.attribution_unknown_count && !serviceApiKeyId && <p className="ux-muted">접수 키 미기록 {summary.attribution_unknown_count}건 포함</p>}
  </div>;
}
