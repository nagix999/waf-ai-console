import { useEffect, useState } from "react";
import { api } from "./api.js";
import { Icon } from "./Icon.jsx";
import Dialog from "./Dialog.jsx";
import DataTable, { Table } from "./DataTable.jsx";
import { dashboardAnalysisQuery } from "./dashboardView.js";
import { formatDate, formatDuration } from "./analysisView.js";
import EvaluationOverview from "./EvaluationOverview.jsx";
import { EvaluationTrend } from "./DashboardView.jsx";
import { useConsolePreferences } from "./consolePreferences.jsx";
import { startVisiblePolling } from "./visiblePolling.js";
import packageInfo from "../package.json";

export function RuntimeFilters({ days, onDaysChange, serviceApiKeyId, onKeyChange, updatedAt, onUnauthorized }) {
  const { t, locale } = useConsolePreferences(); const [keys, setKeys] = useState([]); const [failed, setFailed] = useState(false);
  useEffect(() => startVisiblePolling(async signal => {
    const value = await api.serviceApiKeys({ signal });
    if (!Array.isArray(value?.items)) throw new Error("invalid_keys");
    if (!signal.aborted) { setKeys(value.items.filter(item => (item.purpose || "production") === "production")); setFailed(false); }
  }, { onError: error => { setFailed(true); if (error.status === 401) onUnauthorized(); } }), [onUnauthorized]);
  return <div className="runtime-filter-row"><div className="ux-toolbar"><label>{t("keys")}<select aria-label={t("keys")} value={serviceApiKeyId} onChange={event => onKeyChange(event.target.value)}><option value="">{t("allKeys")}</option>{serviceApiKeyId && !keys.some(key => key.id === serviceApiKeyId) && <option value={serviceApiKeyId}>{t("unavailable")}</option>}{keys.map(key => <option key={key.id} value={key.id}>{key.name}</option>)}</select></label><label>{t("period")}<select aria-label={t("period")} value={days} onChange={event => onDaysChange(Number(event.target.value))}>{[7, 30, 90].map(value => <option key={value} value={value}>{t("days", { days: value })}</option>)}</select></label></div>
    <span className="ux-muted">{failed ? t("readError") : updatedAt ? t("updated", { time: new Date(updatedAt).toLocaleTimeString(locale === "ko" ? "ko-KR" : "en-GB") }) : t("loading")}</span>
  </div>;
}

function ProductionConfiguration({ onNavigate, onUnauthorized }) {
  const { t } = useConsolePreferences(); const [data, setData] = useState(null); const [failed, setFailed] = useState(false);
  useEffect(() => startVisiblePolling(async signal => {
    const [agents, prompts, schemas, concurrency] = await Promise.all([api.agentSettings({ signal }), api.promptPolicies({ signal }), api.inputSchemas({ signal }), api.concurrencySettings({ signal })]);
    if (!signal.aborted) { setData({ agents, prompts, schemas, concurrency }); setFailed(false); }
  }, { onError: error => { setData(null); setFailed(true); if (error.status === 401) onUnauthorized(); } }), [onUnauthorized]);
  const roles = data?.agents?.assignments?.production;
  const profileName = id => !data ? "—" : data.agents.profiles.find(profile => profile.id === id)?.name || t("unassigned");
  const version = catalog => { const active = catalog?.items.find(item => item.id === catalog.active_version_id); return active ? `v${active.version_number} · ${active.name}` : t("unassigned"); };
  return <section className="panel runtime-config"><div className="panel-head"><h2>{t("currentConfig")}</h2><button type="button" className="secondary" onClick={() => onNavigate("agents")}>{t("editConfig")}</button></div>
    <div className="runtime-config-body">{failed ? <p className="error" role="alert">{t("readError")}</p> : <>
      <div className="runtime-config-highlight"><div className="runtime-primary"><span className="runtime-model-icon"><Icon name="cube" size={32} /></span><div><span>{t("primary")}</span><strong>{profileName(roles?.primary_profile_id)}</strong></div></div>
      <dl className="runtime-config-grid">{[
        ["verifier", profileName(roles?.verifier_profile_id || roles?.primary_profile_id)],
        ["editor", !roles ? "—" : roles.evidence_editor_enabled ? profileName(roles.evidence_editor_profile_id || roles.primary_profile_id) : t("disabled")],
        ["promptVersion", data ? version(data.prompts) : "—"], ["schemaVersion", data ? version(data.schemas) : "—"],
        ["concurrencyLimit", data?.concurrency.production ?? "—"],
      ].map(([key, value]) => <div key={key}><dt>{t(key)}</dt><dd>{value}</dd></div>)}</dl></div>
    </>}<p className="ux-muted">{t("configNote")}</p></div>
  </section>;
}

function RequestTrend({ trend, available, failed }) {
  const { t } = useConsolePreferences(); const [open, setOpen] = useState(false);
  const maximum = Math.max(1, ...trend.map(day => day.total)); const width = 500 / Math.max(1, trend.length);
  return <section className="panel runtime-trend"><div className="panel-head"><h2>{t("requestTrend")}</h2><button type="button" className="text-button" disabled={!available} onClick={() => setOpen(true)}>{t("dailyValues")}</button></div>
    <div className="runtime-trend-body">{trend.some(day => day.total > 0) ? <svg viewBox="0 0 550 210" role="img" aria-label={t("requestTrend")}>
      {[0, .5, 1].map(ratio => <g key={ratio}><line x1="42" x2="546" y1={170 - ratio * 150} y2={170 - ratio * 150} /><text x="2" y={175 - ratio * 150}>{Math.round(maximum * ratio)}</text></g>)}
      {trend.map((day, index) => <rect key={day.date} x={44 + index * width} y={170 - day.total / maximum * 150} width={Math.max(1, width - 3)} height={day.total / maximum * 150} rx="2"><title>{day.date}: {t("count", { count: day.total })}</title></rect>)}
      <text x="44" y="198">{trend[0]?.date}</text><text x="545" y="198" textAnchor="end">{trend.at(-1)?.date}</text>
    </svg> : <p className="runtime-empty-chart">{t(failed ? "unavailable" : available ? "noRequests" : "loading")}</p>}<small className="ux-muted">UTC · {t("periodOnly")}</small></div>
    <Dialog open={open} title={t("dailyValues")} onClose={() => setOpen(false)}><div className="table-wrap"><Table><thead><tr><th>UTC</th><th>{t("requests")}</th></tr></thead><tbody>{trend.map(day => <tr key={day.date}><td>{day.date}</td><td>{day.total.toLocaleString()}</td></tr>)}</tbody></Table></div></Dialog>
  </section>;
}

export function runtimeOutcomes(counts) {
  if (!counts || !["completed", "failed", "pending", "processing"].every(key => Number.isSafeInteger(counts[key]) && counts[key] >= 0)) return null;
  const finished = counts.completed + counts.failed;
  return { finished, failureRate: finished > 0 ? counts.failed / finished : null };
}

export function RuntimeOutcomes({ counts, onFilter }) {
  const { t } = useConsolePreferences(); const outcome = runtimeOutcomes(counts);
  const rate = outcome?.failureRate;
  return <section className="panel runtime-outcomes"><div className="panel-head"><h2>{t("failureRate")}</h2><span>{t("finishedRequests")}</span></div>
    <div className="runtime-outcome-body"><div className="runtime-rate"><strong>{rate != null ? `${(rate * 100).toFixed(1)}%` : "—"}</strong><span>{outcome ? t("finishedCount", { count: outcome.finished.toLocaleString() }) : t("unavailable")}</span></div>
      {rate != null && <svg className="runtime-outcome-ring" viewBox="0 0 100 100" role="img" aria-label={`${t("failureRate")} ${(rate * 100).toFixed(1)}%`}><circle className="ring-base" cx="50" cy="50" r="38" /><circle className="ring-failed" cx="50" cy="50" r="38" pathLength="100" strokeDasharray={`${rate * 100} 100`} transform="rotate(-90 50 50)" /></svg>}
      <div className="runtime-outcome-legend">{["completed", "failed", "processing", "pending"].map(key => <button type="button" key={key} disabled={!outcome} onClick={() => onFilter({ status: key })}><span><i className={`outcome-dot outcome-${key}`} />{t(key)}</span><strong>{outcome ? counts[key].toLocaleString() : "—"}</strong></button>)}</div>
    </div><p className="runtime-footnote ux-muted">{t("failureRateNote")}</p>
  </section>;
}

function RuntimeRecent({ summary, serviceApiKeyId, onOpen, onFilter, onUnauthorized }) {
  const { t } = useConsolePreferences(); const [view, setView] = useState({ key: "", items: [], loading: true, error: false });
  const query = dashboardAnalysisQuery(summary, serviceApiKeyId);
  const key = query ? JSON.stringify(query) : "";
  useEffect(() => {
    if (!key) return;
    const controller = new AbortController();
    setView({ key, items: [], loading: true, error: false });
    api.analyses(JSON.parse(key), { signal: controller.signal }).then(result => {
      if (!Array.isArray(result?.items)) throw new Error("invalid_recent_requests");
      if (!controller.signal.aborted) setView({ key, items: result.items, loading: false, error: false });
    }).catch(error => { if (!controller.signal.aborted) { setView({ key, items: [], loading: false, error: true }); if (error.status === 401) onUnauthorized(); } });
    return () => controller.abort();
  }, [key, onUnauthorized]);
  const current = view.key === key && key ? view : { items: [], loading: Boolean(key), error: false };
  return <section className="panel runtime-recent"><div className="panel-head"><div><h2>{t("recentRequests")}</h2><p className="ux-muted">{t("recentScope")}</p></div><button type="button" className="text-button" disabled={!key} onClick={() => onFilter({})}>{t("viewHistory")} <Icon name="arrow" size={15} /></button></div>
    {current.error && <p className="error" role="alert">{t("readError")}</p>}
    <DataTable label={t("recentRequests")} data={current.items} className="runtime-recent-table" columns={[
      { id: "created_at", header: t("received"), width: "19%", render: item => formatDate(item.created_at) },
      { id: "event", header: t("event"), render: item => <strong>{item.event_name || item.signature || item.company_name || t("detail.missing")}</strong> },
      { id: "status", header: t("detail.status"), width: "12%", render: item => <span className={`runtime-status-text outcome-${item.status}`}><i />{t(`detail.${item.status}`)}</span> },
      { id: "verdict", header: t("detail.verdict"), width: "12%", render: item => item.status === "completed" && item.verdict ? t(`verdict.${item.verdict}`) : "—" },
      { id: "total_elapsed_ms", header: t("detail.total"), width: "12%", render: item => formatDuration(item.total_elapsed_ms, "—") },
      { id: "actions", header: <span className="sr-only">{t("viewDetails")}</span>, width: 86, render: item => <button type="button" className="secondary small" onClick={() => onOpen(item.id)}>{t("viewDetails")}</button> },
    ]} empty={current.error ? t("unavailable") : current.loading ? t("loading") : !key ? t("unavailable") : t("noRequests")} />
  </section>;
}

export function RuntimeStatus({ summary, error, onNavigate, onOpen, onFilter, onUnauthorized, ...filters }) {
  const { t } = useConsolePreferences(); const stats = error ? null : summary?.counts;
  const count = key => stats?.[key]?.toLocaleString() ?? "—";
  const filter = values => summary && onFilter({ ...values, created_from: summary.window.created_from, created_to: summary.window.created_to, ...(filters.serviceApiKeyId ? { service_api_key_id: filters.serviceApiKeyId } : {}) });
  return <div className="page-stack runtime-page"><RuntimeFilters {...filters} onUnauthorized={onUnauthorized} />
    {error && <p className="error" role="alert">{t("readError")}</p>}
    <section className="runtime-health-grid" aria-label={t("status")}>{[
      ["API", error ? t("healthError") : summary ? t("apiResponding") : t("loading"), "apiNote", "globe"],
      [t("worker"), t("unmeasured"), "workerNote", "settings"], [t("modelWorker"), t("unmeasured"), "workerNote", "database"],
      [t("queue"), count("pending"), "periodOnly", "queue"],
    ].map(([label, value, note, icon]) => <article className="panel" key={label}><span className="runtime-health-icon"><Icon name={icon} size={24} /></span><div><span>{label}</span><strong>{value}</strong><small>{t(note)}</small></div></article>)}</section>
    <div className="runtime-main-grid"><ProductionConfiguration onNavigate={onNavigate} onUnauthorized={onUnauthorized} /><RequestTrend trend={error ? [] : summary?.trend || []} available={Boolean(summary) && !error} failed={Boolean(error)} /></div>
    <div className="runtime-main-grid"><section className="panel runtime-latency"><div className="panel-head"><h2>{t("latency")}</h2><span className="runtime-unmeasured">{t("unmeasured")}</span></div><dl>{["p50", "p95", "p99"].map(label => <div key={label}><dd>—</dd><dt>{label}</dt></div>)}</dl><p className="ux-muted runtime-footnote">{t("latencyPending")}</p></section><RuntimeOutcomes counts={stats} onFilter={filter} /></div>
    <RuntimeRecent summary={error ? null : summary} serviceApiKeyId={filters.serviceApiKeyId} onOpen={onOpen} onFilter={filter} onUnauthorized={onUnauthorized} />
  </div>;
}

export function RuntimeQuality({ summary, error, onNavigate, onUnauthorized, ...filters }) {
  const { t } = useConsolePreferences();
  return <div className="page-stack"><section className="panel runtime-pending"><Icon name="test" size={28} /><h2>{t("officialNotLinked")}</h2><p>{t("officialNotLinkedNote")}</p><button type="button" className="secondary" onClick={() => onNavigate("runs")}>{t("viewRuns")}</button></section>
    <div><h2>{t("referenceComparison")}</h2><p className="ux-muted">{t("referenceComparisonNote")}</p></div><RuntimeFilters {...filters} onUnauthorized={onUnauthorized} />
    <EvaluationOverview summary={error ? null : summary?.evaluation_summary} loading={!error && !summary} error={error ? t("readError") : ""} scopeLabel={t("referenceComparison")} />
    <EvaluationTrend trend={error ? [] : summary?.trend || []} />
  </div>;
}

export function RuntimeInformation({ page, summary, onNavigate }) {
  const { t } = useConsolePreferences();
  if (page === "changes") return <section className="panel runtime-pending"><Icon name="clock" size={28} /><h2>{t("changesUnavailable")}</h2><p>{t("changesNote")}</p><button type="button" className="secondary" onClick={() => onNavigate("agents")}>{t("agents")}</button></section>;
  return <section className="panel runtime-deployment"><dl className="runtime-config-grid"><div><dt>{t("uiVersion")}</dt><dd>v{packageInfo.version}</dd></div><div><dt>{t("runtimeMode")}</dt><dd>{summary?.runtime?.agent_mode || t("unavailable")}</dd></div></dl><p className="ux-muted">{t("deploymentNote")}</p></section>;
}
