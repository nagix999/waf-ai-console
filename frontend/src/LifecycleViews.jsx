import NavigationAction from "./NavigationAction.jsx";
import { useEffect, useRef, useState } from "react";
import { api } from "./api.js";
import { useConsolePreferences } from "./consolePreferences.jsx";
import { startVisiblePolling } from "./visiblePolling.js";
import { formatDate, formatDuration } from "./analysisView.js";
import DataTable from "./DataTable.jsx";
import TextInspector from "./TextInspector.jsx";
import Dialog from "./Dialog.jsx";
import Pagination from "./Pagination.jsx";
import ConcurrencySettings from "./ConcurrencySettings.jsx";
import { Diagnostics } from "./AgentSettings.jsx";
import EvaluationOverview from "./EvaluationOverview.jsx";
import { Icon } from "./Icon.jsx";
import { RuntimeFilters } from "./RuntimeViews.jsx";
import { EvaluationTrend } from "./DashboardView.jsx";
import HelpTooltip from "./HelpTooltip.jsx";
import { metricHelp } from "./evaluationMetrics.js";
import { configurationPart } from "./testConfigurationIdentity.js";
import { SettingsSectionHeader, SettingsSummary, SettingsWorkflow } from "./SettingsWorkspace.jsx";
import CompletedTimingSummary from "./TimingSummary.jsx";

export function useWords() { const { locale } = useConsolePreferences(); return (ko, en) => locale === "en" ? en : ko; }
export function Section({ title, context, action, children, className = "" }) {
  return <section className={`v5-section ${className}`}><header className="v5-section-head"><div><h2>{title}</h2>{context && <p>{context}</p>}</div>{action}</header>{children}</section>;
}
export function MetricStrip({ items }) {
  return <dl className="v5-metric-strip">{items.map(([label, value, note, help]) => <div key={label}><dt>{label}{help && <HelpTooltip label={label}>{help}</HelpTooltip>}</dt><dd>{value ?? "—"}</dd>{note && <small>{note}</small>}</div>)}</dl>;
}
export function useRead(read, dependencies = []) {
  const [value, setValue] = useState(null), [error, setError] = useState(false), [version, refresh] = useState(0);
  useEffect(() => { setValue(null); setError(false); return startVisiblePolling(async signal => {
    const result = await read({ signal }); if (!signal.aborted) { setValue(result); setError(false); }
    return result?.queue?.pending > 0 || result?.queue?.processing > 0;
  }, { onError: () => { setError(true); setValue(null); } }); }, [...dependencies, version]);
  return { value, error, refresh: () => refresh(n => n + 1) };
}
function ReadState({ error, children }) { const w = useWords(); return error ? <p role="alert" className="error">{w("정보를 불러오지 못했습니다. 다시 조회해 주세요.", "Could not load data. Please retry.")}</p> : children; }
export function ConfigurationRows({ snapshot, names = {}, versions = {}, showRoles = true }) {
  const w = useWords();
  const name = role => snapshot?.[role]?.profile_id ? names[snapshot[role].profile_id] || w("프로필 정보 없음", "Profile unavailable") : w("미지정", "Not assigned");
  return <dl className="v5-facts">{[[w("주 분석 모델 · Primary", "Primary"), name("primary")], [w("검증 모델 · Verifier", "Verifier"), name("verifier")], [w("근거 정리 모델 · Evidence Editor", "Evidence Editor"), snapshot?.evidence_editor?.enabled ? name("evidence_editor") : w("사용 안 함", "Disabled")],
    [w("분석 지침", "Analysis instructions"), versions[snapshot?.prompt?.policy_version_id] || snapshot?.prompt?.prompt_version || "—"], [w("입력 스키마", "Input schema"), versions[snapshot?.input_schema?.version_id] || (snapshot?.input_schema?.version_id ? w("고정된 저장 버전", "Pinned saved version") : "—")], [w("파서", "Parser"), versions[snapshot?.parser?.version_id] || snapshot?.parser?.name || snapshot?.parser?.engine_version || (snapshot ? w("이전 기본 파서 · generic-http-v3", "Legacy baseline · generic-http-v3") : "—")], [w("시그니처 설명", "Signature explanations"), versions[snapshot?.signature_explanations?.version_id] || snapshot?.signature_explanations?.name || (snapshot?.signature_explanations?.version_id ? w("고정된 설명집", "Pinned explanation collection") : snapshot ? w("사용 안 함", "Disabled") : "—")]].filter((_, index) => showRoles || index >= 3).map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{value}</dd></div>)}</dl>;
}
const metricKeys = [["accuracy", "Accuracy"], ["precision", "Precision"], ["recall", "Recall"], ["f1", "F1"], ["coverage", "Coverage"]];
const actions = {
  capture_production_baseline: ["이전 운영 구성 기록", "Previous Production baseline captured"], promote_production: ["운영 반영", "Promoted to Production"],
  save_test_defaults: ["기본 테스트 설정 저장", "Default Test Configuration saved"], import_test_ground_truth_draft: ["테스트 문항 가져오기", "Test cases imported"],
  update_test_defaults: ["Test 기본값 변경", "Test defaults updated"], update_concurrency: ["동시 처리 변경", "Capacity updated"],
  create_vllm_profile: ["LLM 프로필 등록", "LLM profile created"], update_vllm_profile: ["LLM 프로필 수정", "LLM profile updated"],
  disable_vllm_profile: ["LLM 프로필 비활성화", "LLM profile disabled"], enable_vllm_profile: ["LLM 프로필 활성화", "LLM profile enabled"],
  assign_test_llm_profile: ["Test 모델 지정", "Test model assigned"], unassign_test_llm_profile: ["Test 모델 해제", "Test model unassigned"],
  create_prompt_policy: ["분석 지침 저장", "Analysis instructions saved"], create_input_schema: ["입력 스키마 저장", "Input schema saved"],
  create_signature_explanation: ["시그니처 설명 저장", "Signature explanations saved"],
  issue_service_api_key: ["API Key 발급", "API key issued"], rename_service_api_key: ["API Key 이름 변경", "API key renamed"],
  revoke_service_api_key: ["API Key 인증 차단", "API key revoked"], delete_service_api_key: ["API Key 삭제", "API key deleted"],
  create_internal_egress: ["vLLM 대상 등록", "vLLM target added"], update_internal_egress: ["vLLM 대상 수정", "vLLM target updated"], delete_internal_egress: ["vLLM 대상 삭제", "vLLM target deleted"],
};
const actionLabel = (action, w) => actions[action] ? w(...actions[action]) : w("변경 기록", "Change recorded");
export function OfficialMetrics({ evaluation, baseline, comparable, showMetricStrip = true }) {
  const w = useWords(); const summary = evaluation?.summary; const metrics = summary?.evaluation_summary?.metrics;
  if (!evaluation) return <p className="v5-empty">{w("현재 구성에 연결된 공식 평가가 없습니다.", "No official evaluation is linked to this configuration.")}</p>;
  return <><p className="v5-context">{summary?.ground_truth?.dataset_name} · r{summary?.ground_truth?.dataset_revision} · {w("평가 문항", "Published cases")} {summary?.ground_truth?.sample_count ?? summary?.ground_truth?.approved_count} · {formatDate(evaluation.created_at)}</p>
    {showMetricStrip && <MetricStrip items={metricKeys.map(([key, label]) => { const value = metrics?.[key]; const previous = baseline?.summary?.evaluation_summary?.metrics?.[key]; return [label, typeof value === "number" ? `${(value * 100).toFixed(1)}%` : "—", comparable && typeof previous === "number" && typeof value === "number" ? `${value >= previous ? "+" : ""}${((value - previous) * 100).toFixed(1)} pp` : null, metricHelp[key]]; })} />}
    <p className="v5-context">{w("평가 데이터셋의 공식 버전에 대한 평가입니다. 운영 전체 트래픽의 정확도가 아닙니다.", "Evaluation on published Evaluation Dataset cases, not live Production traffic accuracy.")}</p></>;
}
export function Overview({ onNavigate, onOpen }) {
  const w = useWords(); const [key, setKey] = useState("");
  const runtime = useRead(options => api.runtimeStatus("24h", options, { purpose: "production", ...(key ? { service_api_key_id: key } : {}) }), [key]);
  const production = useRead(api.productionConfiguration); const changes = useRead(options => api.activity({ limit: 5 }, options));
  const connections = useRead(async options => { const [keys, targets] = await Promise.all([api.serviceApiKeys(options), api.internalEgress(options)]); return { keys: keys.items?.length, productionKeys: keys.items.filter(item => (item.purpose || "production") === "production"), targets: (targets.items || targets)?.length }; });
  const r = runtime.value, p = production.value, terminal = (r?.outcome_summary?.completed || 0) + (r?.outcome_summary?.failed || 0);
  return <div className="v5-overview page-stack"><div className="ux-toolbar"><label>API Key<select value={key} onChange={e => setKey(e.target.value)}><option value="">{w("전체 운영 키", "All Production keys")}</option>{connections.value?.productionKeys?.map(item => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label><span className="v5-context">{w("요청·실패·대기만 키별로 조회합니다. 공식 평가는 운영 구성 기준입니다.", "Key filter applies to requests, failures and queue only. Official evaluation is configuration-wide.")}</span></div><ReadState error={runtime.error || production.error} />
    <section className="v5-production-state"><div><span className="eyebrow">Production</span><h2>{w("운영 구성", "Production configuration")}</h2><span className="v5-state">{p?.drifted ? w("구성 변경 확인 필요", "Configuration drift detected") : p?.configuration_id ? w("반영된 구성 사용 중", "Promoted configuration") : w("이전 운영 구성", "Previous Production configuration")}</span></div><div><strong>{p?.profile_names?.[p?.snapshot?.primary?.profile_id] || w("Primary 미지정", "Primary not assigned")}</strong><p>{p?.snapshot?.prompt?.prompt_version || "—"}</p><small>{w("모델 준비 상태", "Model readiness")}: {w("미측정", "Unknown")}</small></div><NavigationAction onClick={() => onNavigate("runtime")}>{w("실행 상태 보기", "View Runtime")}</NavigationAction></section>
    <MetricStrip items={[[w("24시간 요청", "Requests · 24h"), r?.request_count], ["p95", formatDuration(r?.latency_summary?.p95, "—")], [w("실행 실패율", "Failure rate"), terminal ? `${(r.outcome_summary.failed / terminal * 100).toFixed(1)}%` : "—"], [w("재선점 요청", "Reclaimed requests"), r?.retry_count], [w("대기", "Queue"), r?.queue?.pending]]} />
    <div className="v5-overview-columns"><div className="page-stack"><Section title={w("운영 설정 평가", "Production Evaluation")} action={<NavigationAction onClick={() => onNavigate("quality")}>{w("평가 보기", "View evaluation")}</NavigationAction>}><OfficialMetrics evaluation={p?.evaluation} /></Section>
      <Section title={w("현재 구성", "Current configuration")} action={<NavigationAction onClick={() => onNavigate("agents")}>{w("구성 보기", "View configuration")}</NavigationAction>}><ConfigurationRows snapshot={p?.snapshot} names={p?.profile_names} versions={p?.version_names} /></Section>
      <Section title={w("최근 변경", "Recent activity")} action={<NavigationAction onClick={() => onNavigate("changes")}>{w("전체 보기", "View all")}</NavigationAction>}><ReadState error={changes.error} /><ul className="v5-activity-list">{changes.value?.items.map(item => <li key={item.id}><time>{formatDate(item.created_at)}</time><strong>{actionLabel(item.action, w)}</strong><span>{item.actor}</span></li>)}</ul>{!changes.error && !changes.value?.items.length && <p className="v5-empty">{w("기록된 변경이 없습니다.", "No recorded changes.")}</p>}</Section></div>
      <aside className="page-stack"><Section title={w("확인할 항목", "Attention")}><button className="v5-attention" onClick={() => onNavigate("runtime")}><Icon name="alert" size={20} /><span>{w("실행 실패", "Failed requests")}<strong>{r?.outcome_summary?.failed ?? "—"}</strong></span><span>→</span></button>{r?.health && Object.entries(r.health).filter(([key, value]) => key.endsWith("worker") && value.status !== "healthy").map(([key, value]) => <p key={key} className="v5-context">{key === "analysis_worker" ? w("분석 worker", "Analysis worker") : w("모델 검증 worker", "Model-test worker")} · {value.status === "stale" ? w("응답 지연", "Heartbeat stale") : w("상태 미확인", "Unknown")}</p>)}</Section>
        <Section title={w("연동", "Connections")}><ReadState error={connections.error} /><dl className="v5-facts"><div><dt>API Keys</dt><dd><NavigationAction onClick={() => onNavigate("keys")}>{connections.value?.keys ?? "—"}</NavigationAction></dd></div><div><dt>vLLM Targets</dt><dd><NavigationAction onClick={() => onNavigate("targets")}>{connections.value?.targets ?? "—"}</NavigationAction></dd></div></dl></Section></aside></div>
    <small className="v5-context">{w("마지막 갱신", "Last updated")}: {formatDate(r?.updated_at)}</small></div>;
}

const checkNames = {
  candidate_snapshot_valid: ["테스트 구성 기록 확인", "Candidate snapshot integrity"],
  candidate_completed_without_failures: ["모든 문항 실행 완료", "All cases completed without failures"],
  official_approved_evaluation: ["공식 버전 평가 완료", "Published Version evaluation complete"],
  published_membership_valid: ["공식 버전의 전체 문항 확인", "Exact published revision membership"],
  official_snapshot_matches_candidate: ["평가와 테스트 설정 일치", "Evaluation matches candidate"],
  tested_profiles_still_valid: ["모델 설정·검증·전송 승인 유효", "Model fingerprints, validation and approval valid"],
  tested_instructions_still_current: ["지침, 스키마, 파서, 설명집과 처리 규칙 일치", "Instructions, schema, parser, explanations and execution rules unchanged"],
  parser_comparison_reviewed: ["파싱 비교 결과 확인 완료", "Parser comparison reviewed"],
  parser_structure_reviewed: ["파싱 비교 결과 확인 완료", "Parser comparison reviewed"],
};
const promotionErrors = {
  production_configuration_changed: ["운영 구성이 변경되었습니다. 최신 구성을 다시 조회하고 검토해 주세요.", "Production changed. Refresh and review the current configuration."],
  schema_change_ack_required: ["입력 스키마 변경과 수집기 영향을 확인해 주세요.", "Review and acknowledge the input schema change."],
  candidate_not_eligible_for_promotion: ["적용 조건이 충족되지 않았습니다. 다시 확인해 주세요.", "This test no longer qualifies. Refresh the checks."],
  test_run_not_found: ["선택한 테스트를 찾을 수 없습니다.", "The selected test was not found."],
};
export function Promotion({ initialRunId, onNavigate, onBack, onOpenTest, fromHome }) {
  const w = useWords(); const [id, setId] = useState(initialRunId || ""), [data, setData] = useState(null), [error, setError] = useState(""), [ack, setAck] = useState(false), [busy, setBusy] = useState(false), [revision, setRevision] = useState(0), [done, setDone] = useState(false), [technical, setTechnical] = useState(false);
  const inFlight = useRef(false);
  useEffect(() => setId(initialRunId || ""), [initialRunId]);
  useEffect(() => { setData(null); setError(""); setAck(false); setDone(false); if (!id) return;
    const controller = new AbortController(); api.promotionPreflight(id, { signal: controller.signal }).then(result => { if (!controller.signal.aborted) setData(result); }).catch(e => { if (!controller.signal.aborted) setError(e.message); }); return () => controller.abort();
  }, [id, revision]);
  async function promote() { if (inFlight.current || !data?.eligible || done || data.schema_changed && !ack) return; inFlight.current = true; setBusy(true); setError("");
    try { await api.promoteConfiguration({ candidate_test_run_id: id, expected_production_configuration_hash: data.current.configuration_hash, acknowledge_schema_change: ack }); setDone(true); }
    catch (e) { setError(e.message); setData(null); } finally { inFlight.current = false; setBusy(false); } }
  return <div className="page-stack v5-promotion settings-workspace"><div className="settings-route-actions"><button className="back" disabled={busy} onClick={onBack}>← {fromHome ? w("홈으로 돌아가기", "Back to Home") : w("테스트로 돌아가기", "Back to Test")}</button><NavigationAction onClick={onOpenTest || onBack}>{w("원본 테스트 열기", "Open source Test")}</NavigationAction></div><SettingsSectionHeader icon="shield" title={data?.candidate_name || w("적용 조건 확인", "Checking candidate")} description={w("이 테스트에서 실행한 구성과 공식 평가를 검토합니다. 다른 테스트를 임의로 선택하지 않습니다.", "Review this test's captured configuration and official evaluation.")} actions={<button className="secondary" disabled={busy} onClick={() => setRevision(n => n + 1)}>{w("다시 확인", "Refresh checks")}</button>}><SettingsWorkflow /></SettingsSectionHeader>
    {error && <p role="alert" className="error">{promotionErrors[error] ? w(...promotionErrors[error]) : w("적용 조건 확인에 실패했습니다. 다시 확인해 주세요.", "Could not check promotion conditions. Please refresh.")}</p>}
    {done && <p role="status" className="notice">{w("운영 반영을 완료했습니다.", "Promoted to Production.")}</p>}
    {data && <><Section title={w("운영 구성 비교", "Configuration comparison")} action={<button className="text-button" onClick={() => setTechnical(true)}>{w("버전·식별정보", "Versions & identifiers")}</button>}><div className="v5-comparison"><div><h3>{w("현재 운영 설정", "Current Production Configuration")}</h3><ConfigurationRows snapshot={data.current.snapshot} names={data.current.profile_names} versions={data.current.version_names} /></div><div><h3>{w("테스트 설정", "Test Configuration")} · {data.candidate_name}</h3><ConfigurationRows snapshot={data.candidate} names={data.current.profile_names} versions={data.current.version_names} /></div></div><div className="v5-diff-strip">{["primary", "verifier", "evidence_editor", "prompt", "input_schema", "parser", "signature_explanations"].map(key => <span key={key} className={configurationPart(data.current.snapshot, key) === configurationPart(data.candidate, key) ? "" : "v5-changed"}>{({ primary: w("주 분석 모델", "Primary"), verifier: w("검증 모델", "Verifier"), evidence_editor: w("근거 정리 모델", "Evidence Editor"), prompt: w("분석 지침", "Analysis Instructions"), input_schema: w("입력 스키마", "Input Schema"), parser: w("파서", "Parser"), signature_explanations: w("시그니처 설명", "Signature explanations") })[key]} · {configurationPart(data.current.snapshot, key) === configurationPart(data.candidate, key) ? w("동일", "Unchanged") : w("변경", "Changed")}</span>)}</div></Section>
      <Section title={w("공식 평가", "Official evaluation")}><OfficialMetrics evaluation={data.evaluation} baseline={data.baseline_evaluation} comparable={data.comparable} />{!data.comparable && <p className="v5-context">{w("같은 평가 데이터셋·버전·평가 범위의 운영 기준이 없어 증감을 계산하지 않습니다.", "Deltas require the same dataset, revision, scope and metric semantics.")}</p>}</Section>
      <Section title={w("적용 전 점검", "Readiness Checks")}><ul className="v5-preflight">{data.checks.map(check => <li key={check.code}><Icon name={check.passed ? "check" : "alert"} size={18} /><span>{checkNames[check.code] ? w(...checkNames[check.code]) : check.code}</span><strong>{check.passed ? w("확인", "Pass") : w("미충족", "Blocked")}</strong>{!check.passed && <NavigationAction onClick={() => check.remediation?.kind === "open_llm_profile" ? onNavigate("profiles") : check.remediation?.kind === "open_instructions" ? onNavigate("instructions") : check.remediation?.kind === "open_parsers" ? onNavigate("parsers") : (onOpenTest || onBack)()}>{w("확인할 곳 열기", "Resolve")}</NavigationAction>}</li>)}</ul>
        {data.schema_changed && <div className="v5-schema-change"><h3>{w("입력 API 변경", "Input API contract change")}</h3><p>{w("필수 필드나 제한이 달라지면 기존 수집기의 요청이 거절될 수 있습니다.", "Changed required fields or limits may reject existing client requests.")}</p><TextInspector label={w("필드 변경", "Field changes")} value={data.field_diff} /><NavigationAction onClick={() => onNavigate("schema")}>{w("스키마 보기", "View schema")}</NavigationAction><label className="checkbox-row"><input type="checkbox" checked={ack} disabled={busy || done} onChange={e => setAck(e.target.checked)} />{w("필드 변경과 수집기 영향을 확인했습니다.", "I reviewed the field changes and client impact.")}</label></div>}
        <div className="v5-release-footer"><p>{w("관리자 승인으로 새 운영 실행에 적용합니다. 테스트 기본값과 동시 처리 설정은 유지됩니다.", "Administrator approval applies to new Production executions. Test defaults and concurrency are retained.")}</p><button className="brand-mutation" disabled={busy || done || !data.eligible || data.schema_changed && !ack} onClick={promote}>{busy ? w("적용 중…", "Applying…") : w("운영에 반영", "Apply to Production")}</button></div></Section>
      <Dialog open={technical} title={w("구성 기록", "Configuration records")} onClose={() => setTechnical(false)}><TextInspector label="Configuration snapshots" value={{ current: data.current.snapshot, candidate: data.candidate }} /></Dialog></>}
  </div>;
}

function OfficialBreakdowns({ evaluation }) {
  const w = useWords(); const [dimension, setDimension] = useState("test_category");
  const rows = evaluation?.summary?.breakdowns?.[dimension] || [];
  return <Section title={w("유형별 평가", "Evaluation breakdown")} action={<select aria-label={w("평가 구분", "Breakdown dimension")} value={dimension} onChange={e => setDimension(e.target.value)}><option value="test_category">{w("공격 유형", "Attack category")}</option><option value="difficulty">{w("난이도", "Difficulty")}</option></select>}>
    <DataTable label={w("유형별 공식 평가", "Official evaluation breakdown")} data={rows} getRowId={row => row.name ?? "__unclassified"} empty={w("이 평가에는 유형별 기록이 없습니다.", "No category snapshot was stored for this evaluation.")} columns={[
      { id: "name", header: w("구분", "Category"), render: row => row.name || w("미분류", "Unclassified") },
      { id: "count", header: w("평가 건수", "Evaluated"), render: row => row.evaluation_summary.evaluable },
      ...metricKeys.map(([key, label]) => ({ id: key, header: label, render: row => { const value = row.evaluation_summary.metrics?.[key]; return typeof value === "number" ? <span className="v5-score-bar" style={{ "--score": `${value * 100}%` }}>{(value * 100).toFixed(1)}%</span> : "—"; } }))]} />
  </Section>;
}
export function TrafficChart({ series = [] }) {
  const w = useWords(); const total = row => ["completed", "failed", "pending", "processing"].reduce((sum, key) => sum + (row[key] || 0), 0);
  const max = Math.max(1, ...series.map(total));
  return <Section title={w("접수·처리 현황", "Request volume & outcomes")} context={w("접수 시간대별 현재 처리 상태입니다. 모델 재시도는 포함하지 않습니다.", "Current outcome grouped by admission time. Excludes model repair attempts.")}>
    {!series.length ? <p className="v5-empty">{w("표시할 관측값이 없습니다.", "No observations available.")}</p> : <><svg className="v5-volume-chart" role="img" aria-label={w("접수 시간대별 요청 수", "Requests by admission time")} viewBox="0 0 720 150">{series.map((row, index) => {
      const x = index * 720 / series.length + 8, width = 720 / series.length - 16; let y = 126;
      return <g key={row.from}><title>{formatDate(row.from)} · {total(row)}</title>{["completed", "failed", "pending", "processing"].map(key => { const height = (row[key] || 0) / max * 110; y -= height; return <rect key={key} className={`v5-volume-${key}`} x={x} y={y} width={width} height={height} rx="2" />; })}<text x={x + width / 2} y="146" textAnchor="middle">{index === 0 || index === series.length - 1 ? new Date(row.from).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }) : ""}</text></g>;
    })}<line x1="0" y1="127" x2="720" y2="127" /></svg><div className="v5-chart-legend">{[["completed", w("완료", "Completed")], ["failed", w("실패", "Failed")], ["pending", w("대기", "Pending")], ["processing", w("처리 중", "Processing")]].map(([key, label]) => <span key={key}><i className={`v5-volume-${key}`} />{label} {series.reduce((sum, row) => sum + (row[key] || 0), 0)}</span>)}</div></>}
  </Section>;
}
export function ProductionEvaluation({ onOpenRun, referenceProps }) {
  const w = useWords(); const { value, error, refresh } = useRead(api.productionEvaluations); const [selected, setSelected] = useState(null);
  const evaluation = value?.items.find(e => e.id === selected) || value?.items[0];
  const [referenceOpen, setReferenceOpen] = useState(false);
  return <div className="page-stack settings-workspace production-evaluation-workspace"><Section title={w("운영 설정 평가", "Production Evaluation")} action={<button className="text-button" onClick={refresh}>{w("새로고침", "Refresh")}</button>}><ReadState error={error} /><OfficialMetrics evaluation={evaluation} showMetricStrip={false} /></Section>
    {evaluation && <EvaluationOverview evaluationMode="ground_truth" summary={evaluation.summary?.evaluation_summary} scopeLabel={evaluation.summary?.ground_truth?.dataset_name} />}
    {evaluation && <OfficialBreakdowns evaluation={evaluation} />}
    <NavigationAction onClick={() => setReferenceOpen(true)}>{w("운영 로그의 참고 판정 비교 보기", "View reference-label comparisons for Production logs")}</NavigationAction>
    <Dialog open={referenceOpen} title={w("운영 참고 판정 비교 · 비공식", "Production reference-label comparisons · unofficial")} onClose={() => setReferenceOpen(false)} className="v5-wide-dialog"><p className="v5-context">{w("분석 결과에 입력한 참고 판정과 비교합니다. 공식 평가나 운영 반영 근거로 사용하지 않습니다.", "Compares references attached to analyses. These are not official Evaluation Dataset evaluations or promotion evidence.")}</p>{referenceProps && <><RuntimeFilters {...referenceProps} /><EvaluationOverview summary={referenceProps.summary?.evaluation_summary} error={referenceProps.error} /><EvaluationTrend trend={referenceProps.summary?.trend || []} /></>}</Dialog>
    <Section title={w("평가 이력", "Evaluation history")}><DataTable label={w("공식 평가 이력", "Official evaluation history")} data={value?.items || []} columns={[{ id: "created", header: w("평가 시각", "Evaluated"), render: row => formatDate(row.created_at) }, { id: "name", header: w("테스트", "Test"), render: row => <button className="text-button" onClick={() => setSelected(row.id)}>{row.summary?.name}</button> }, { id: "dataset", header: w("평가 데이터셋", "Evaluation Dataset"), render: row => `${row.summary?.ground_truth?.dataset_name} · r${row.summary?.ground_truth?.dataset_revision}` }, { id: "open", header: "", render: row => <NavigationAction onClick={() => onOpenRun(row.test_run_id)}>{w("테스트 보기", "View test")}</NavigationAction> }]} /></Section></div>;
}

export function RuntimeWorkspace({ onOpen }) {
  const w = useWords(), [window, setWindow] = useState("24h"), [purpose, setPurpose] = useState("all"), [configurationOpen, setConfigurationOpen] = useState(false);
  const { value: r, error, refresh } = useRead(options => api.runtimeStatus(window, options, { purpose }), [window, purpose]);
  const deployment = useRead(api.deployment);
  const terminal = (r?.outcome_summary?.completed || 0) + (r?.outcome_summary?.failed || 0);
  const healthNames = { api: ["API", "API"], analysis_worker: ["분석 worker", "Analysis worker"], model_worker: ["모델 검증 worker", "Model-test worker"], assigned_models: ["모델 준비 상태", "Model readiness"] };
  const config = r?.production_configuration;
  return <div className="page-stack settings-workspace runtime-settings-workspace">
    <SettingsSectionHeader title={w("실행 상태", "Runtime")} actions={<><button type="button" className="secondary" onClick={refresh}>{w("새로고침", "Refresh")}</button><NavigationAction href="#operate/inference">{w("분석 이력", "Analysis history")}</NavigationAction></>} />
    <div className="ux-toolbar settings-filter-toolbar"><label>{w("기간", "Window")}<select value={window} onChange={e => setWindow(e.target.value)}>{["1h", "6h", "24h", "7d"].map(v => <option key={v}>{v}</option>)}</select></label><label>{w("용도", "Purpose")}<select value={purpose} onChange={e => setPurpose(e.target.value)}><option value="all">{w("전체", "All")}</option><option value="production">Production</option><option value="test">Test</option></select></label><small>{w("마지막 갱신", "Updated")}: {formatDate(r?.updated_at)}</small></div><ReadState error={error} />
    <SettingsSummary label={w("처리 현황", "Processing summary")} items={[
      { label: w("처리 중", "Processing"), value: r?.queue?.processing, note: w("현재 전체 큐 기준", "Current global queue"), tone: "blue", icon: "cube" },
      { label: w("대기", "Queued"), value: r?.queue?.pending, note: w("접수 후 처리 대기", "Waiting to start"), icon: "queue" },
      { label: w("완료", "Completed"), value: r?.outcome_summary?.completed, note: w("선택한 기간과 용도 기준", "Selected window and purpose"), icon: "check" },
      { label: w("실패", "Failed"), value: r?.outcome_summary?.failed, note: w("판정 보류는 실패가 아닙니다", "Inconclusive verdicts are not failures"), tone: "teal", icon: "alert" },
    ]} />
    <CompletedTimingSummary summary={r} words={w} scopeNote={w("선택한 기간과 용도에 해당하는 전체 요청을 집계합니다. 분석 이력의 검색 조건과는 별개입니다.", "Aggregates all requests in the selected window and purpose. Analysis-history search filters are separate.")} />
    <div className="settings-detail-grid runtime-main-grid"><TrafficChart series={r?.request_volume_series} /><aside className="settings-context-stack"><section className="panel settings-context-card"><h3>{w("처리 결과", "Processing outcomes")}</h3><dl className="settings-compact-facts"><div><dt>{w("요청 수", "Requests")}</dt><dd>{r?.request_count ?? "—"}</dd></div><div><dt>{w("실패율", "Failure rate")}</dt><dd>{terminal ? (r.outcome_summary.failed / terminal * 100).toFixed(1) + "%" : "—"}</dd></div><div><dt>{w("재선점", "Reclaimed")}</dt><dd>{r?.retry_count ?? "—"}</dd></div></dl></section><section className="panel settings-context-card"><h3>{w("단계별 시간", "Step timing")}</h3><p>{w("전체 실행의 단계별 시간은 현재 API에서 제공하지 않습니다. 테스트별 실행 통계에서 큐 대기와 분석 시간을 나누어 확인하세요.", "The runtime API does not provide global step timings. Use Test execution statistics to separate queue waiting from analysis time.")}</p><NavigationAction href="#evaluate/tests">{w("테스트별 실행 통계", "Test execution statistics")}</NavigationAction></section></aside></div>
    <p className="v5-context settings-inline-notice">{w("대기와 처리 중은 기간과 관계없이 현재 큐 전체를 셉니다. 재선점은 worker가 다시 맡은 요청 수이며 LLM 출력 교정 횟수가 아닙니다.", "Queue counts include all current work, regardless of the selected window. Reclaimed requests are worker reclaims, not LLM output repairs.")}</p>
    <div className="settings-detail-grid runtime-concurrency-grid"><ConcurrencySettings /><aside className="settings-context-stack"><section className="panel settings-context-card"><h3>{w("배포 정보", "Deployment")}</h3><dl className="settings-compact-facts"><div><dt>{w("API 버전", "API version")}</dt><dd>{deployment.value?.api_version || w("미제공", "Not supplied")}</dd></div><div><dt>{w("Agent 실행", "Agent mode")}</dt><dd>{deployment.value?.agent_mode || w("미제공", "Not supplied")}</dd></div></dl><p>{w("읽기 전용입니다. 이 화면에서 서버를 재시작하거나 배포하지 않습니다.", "Read only. This page cannot restart or deploy services.")}</p><NavigationAction href="#operate/activity/deployment">{w("배포 정보 보기", "View deployment")}</NavigationAction></section><section className="panel settings-context-card"><h3>{w("현재 운영 설정", "Current Production configuration")}</h3><p>Primary · <strong>{config?.profile_names?.[config?.snapshot?.primary?.profile_id] || "—"}</strong></p><p>{config?.configuration_id ? w("운영 반영", "Applied") + " " + formatDate(config.applied_at) : w("기존 운영 설정 · 첫 운영 반영 전", "Previous Production configuration · not yet applied")}</p>{config?.drifted && <p className="error">{w("구성 변경 확인 필요", "Configuration drift detected")}</p>}<NavigationAction disabled={!config} onClick={() => setConfigurationOpen(true)}>{w("구성 상세", "Configuration details")}</NavigationAction></section></aside></div>
    <details className="settings-secondary-section"><summary>{w("상태 진단과 최근 실행", "Diagnostics and recent executions")}</summary><div className="page-stack">
      <Section title={w("완료와 실패의 종료 시간", "Terminal time including failures")} context={w("이전 통계 기준을 보존합니다. 접수부터 완료 또는 실패까지의 시간으로, 위의 완료 문항 전용 처리 시간과 분모가 다릅니다.", "Retains the legacy measure: admission to completion or failure. Its population differs from the completed-only timing above.")}><MetricStrip items={[50, 95, 99].map(percentile => [`P${percentile}`, formatDuration(r?.latency_summary?.[`p${percentile}`], "—")])} /></Section>
      <MetricStrip items={Object.entries(r?.health || { api: {}, analysis_worker: {}, model_worker: {}, assigned_models: {} }).map(([key, status]) => [w(...(healthNames[key] || [key, key])), status.status === "healthy" ? w("응답 확인", "Responding") : status.status === "stale" ? w("응답 지연", "Stale") : w("미확인", "Unknown"), status.observed_at ? formatDate(status.observed_at) : w("관측 기록 없음", "No observation")])} />
      <Section className="v5-failure-section" title={w("실패 원인", "Failure types")}><DataTable label={w("실패 원인", "Failure types")} data={r?.failure_types || []} getRowId={row => row.code || "unknown"} columns={[{ id: "code", header: w("오류 코드", "Error code"), render: row => row.code || w("미기록", "Not recorded") }, { id: "count", header: w("건수", "Count") }]} /></Section>
      <Section title={w("최근 실행", "Recent executions")}><DataTable data={r?.recent_analyses || []} label={w("최근 실행", "Recent executions")} columns={[{ id: "created", header: w("접수", "Received"), render: row => formatDate(row.created_at) }, { id: "purpose", header: w("용도", "Purpose"), render: row => row.analysis_purpose }, { id: "status", header: w("상태", "Status"), render: row => row.status }, { id: "failure", header: w("오류", "Error"), render: row => row.error_code || "—" }, { id: "view", header: "", render: row => <NavigationAction onClick={() => onOpen(row.id)}>{w("상세", "View")}</NavigationAction> }]} /></Section><Diagnostics />
    </div></details>
    <Dialog open={configurationOpen} title={w("운영 구성 · 읽기 전용", "Production configuration · read only")} onClose={() => setConfigurationOpen(false)}><ConfigurationRows snapshot={config?.snapshot} names={config?.profile_names} versions={config?.version_names} /><TextInspector label="Configuration ID / Hash" value={{ configuration_id: config?.configuration_id, configuration_hash: config?.configuration_hash, source_test_run_id: config?.source_test_run_id }} /></Dialog>
  </div>;
}
export function Activity({ deploymentOnly = false }) {
  const w = useWords(); const [category, setCategory] = useState("all"), [offset, setOffset] = useState(0), [selected, setSelected] = useState(null), [deploymentOpen, setDeploymentOpen] = useState(false);
  const { value, error } = useRead(options => api.activity({ category, offset, limit: 30 }, options), [category, offset]);
  const deployment = useRead(api.deployment);
  if (deploymentOnly) return <div className="page-stack settings-workspace deployment-workspace"><SettingsSectionHeader title={w("배포 정보", "Deployment metadata")} actions={<><NavigationAction href="#operate/runtime">{w("실행 상태", "Runtime")}</NavigationAction><button type="button" className="secondary" onClick={deployment.refresh}>{w("새로고침", "Refresh")}</button></>} /><ReadState error={deployment.error} />
    <div className="settings-page-context"><span className="status purpose-test">{w("읽기 전용", "Read only")}</span><span>{w("서버 제공 메타데이터", "Server-supplied metadata")}</span></div>
    <div className="settings-detail-grid"><Section title={w("환경 정보", "Environment")}><dl className="settings-deployment-facts">{[
      [w("API 버전", "API version"), deployment.value?.api_version], [w("Agent 실행 방식", "Agent mode"), deployment.value?.agent_mode],
      [w("DB migration", "DB migration"), deployment.value?.db_revisions?.join(", ")], [w("배포 시각", "Deployed at"), deployment.value?.deployed_at ? formatDate(deployment.value.deployed_at) : null],
    ].map(([label, content]) => <div key={label}><dt>{label}</dt><dd>{content || w("미제공", "Not supplied")}</dd></div>)}</dl><button className="text-button" type="button" onClick={() => setDeploymentOpen(true)}>{w("기술정보", "Technical details")}</button></Section>
    <aside className="settings-context-stack"><section className="panel settings-context-card"><h3>{w("이 화면에서 하지 않는 일", "Not available on this page")}</h3><p>{w("컨테이너 재시작과 배포, 환경 파일 수정, DB 변경은 실행하지 않습니다. API 키와 환경 변수의 비밀값도 표시하지 않습니다.", "This page cannot restart containers, deploy, edit environment files or change the database. API secrets and private environment values are never shown.")}</p></section><section className="panel settings-context-card"><h3>{w("설정 반영과 배포", "Configuration vs. deployment")}</h3><p>{w("모델과 분석 지침, 입력 처리의 운영 반영은 공식 테스트와 관리자 검토를 거칩니다. 서비스 이미지 배포와는 별개입니다.", "Production changes to models, instructions and input processing require an Official Test and administrator review. This is separate from deploying service images.")}</p><NavigationAction href="#operate/activity">{w("변경 이력", "Activity")}</NavigationAction></section></aside></div>
    <Section title={w("구성 요소", "Components")} context={w("구성 요소의 역할과 서버가 제공한 실행 정보를 표시합니다. 미제공 상태를 정상으로 추정하지 않습니다.", "Shows component roles and supplied runtime metadata. Missing observations are never inferred as healthy.")}><DataTable label={w("배포 구성 요소", "Deployment components")} data={[
      { id: "web", name: w("웹 UI", "Web UI"), role: w("관리자 작업 공간 제공", "Administrator workspace"), image: deployment.value?.frontend_image },
      { id: "api", name: "API", role: w("분석 접수와 조회, 관리자 권한 처리", "Analysis requests, queries and administrator authorization"), image: deployment.value?.api_image, version: deployment.value?.api_version },
      { id: "worker", name: w("분석 worker", "Analysis worker"), role: w("고정된 설정으로 비동기 분석 실행", "Asynchronous analysis with pinned configuration"), image: deployment.value?.worker_image },
      { id: "model-worker", name: w("모델 검증 worker", "Model-test worker"), role: w("명시적으로 요청한 모델 검증 처리", "Explicitly requested model verification") },
    ]} columns={[
      { id: "name", header: w("구성 요소", "Component"), minWidth: 160 },
      { id: "role", header: w("역할", "Role"), minWidth: 280 },
      { id: "version", header: w("실행 버전", "Version"), minWidth: 130, render: row => row.version || w("미제공", "Not supplied") },
      { id: "image", header: w("실행 이미지", "Image"), minWidth: 180, render: row => row.image || w("미제공", "Not supplied") },
    ]} /></Section>
    <Dialog open={deploymentOpen} title={w("배포 정보 · 읽기 전용", "Deployment · read only")} onClose={() => setDeploymentOpen(false)}><ReadState error={deployment.error} /><TextInspector label="Deployment metadata" value={deployment.value} /></Dialog></div>;
  return <div className="page-stack settings-workspace activity-workspace"><div className="ux-toolbar settings-filter-toolbar"><label>{w("변경 종류", "Change type")}<select value={category} onChange={e => { setCategory(e.target.value); setOffset(0); setSelected(null); }}>{[["all", "전체", "All"], ["promotion", "운영 반영", "Production application"], ["runtime", "실행 설정", "Runtime"], ["configuration", "모델·지침·스키마", "Configuration"], ["integration", "외부 연동", "Integration"], ["deployment", "배포", "Deployment"]].map(([key, ko, en]) => <option key={key} value={key}>{w(ko, en)}</option>)}</select></label><NavigationAction onClick={() => setDeploymentOpen(true)}>{w("배포 정보", "Deployment metadata")}</NavigationAction></div><ReadState error={error} />
    <div className="v5-workbench"><DataTable data={value?.items || []} label={w("변경 이력", "Change history")} columns={[{ id: "date", header: w("시각", "Time"), render: row => formatDate(row.created_at) }, { id: "category", header: w("종류", "Type"), render: row => w(...({ promotion: ["운영 반영", "Production application"], runtime: ["실행 설정", "Runtime"], configuration: ["구성 변경", "Configuration"], integration: ["연동", "Integration"], deployment: ["배포", "Deployment"] }[row.category] || ["변경", "Change"])) }, { id: "action", header: w("변경", "Change"), render: row => <button className="text-button" onClick={() => setSelected(row)}>{actionLabel(row.action, w)}</button> }]} /><aside className="v5-workbench-detail">{selected ? <><h2>{actionLabel(selected.action, w)}</h2><p>{selected.actor} · {formatDate(selected.created_at)}</p><TextInspector label={w("변경 전후", "Before / after")} value={{ action: selected.action, resource: { type: selected.resource_type, id: selected.resource_id }, before: selected.before, after: selected.after }} /></> : <p className="v5-empty">{w("변경 항목을 선택하세요.", "Select a change to inspect.")}</p>}</aside></div>
    <Pagination total={value?.total || 0} limit={30} offset={offset} onOffsetChange={setOffset} />
    <Dialog open={deploymentOpen} title={w("배포 정보 · 읽기 전용", "Deployment · read only")} onClose={() => setDeploymentOpen(false)}><ReadState error={deployment.error} /><TextInspector label="Deployment metadata" value={deployment.value} /><p>{w("이미지·커밋·배포 시각은 제공된 기록이 있을 때만 표시합니다.", "Images, commit and deployment time are shown only when supplied.")}</p></Dialog></div>;
}
