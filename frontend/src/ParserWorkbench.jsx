import { useEffect, useRef, useState } from "react";
import { useConsolePreferences } from "./consolePreferences.jsx";
import DataTable from "./DataTable.jsx";
import Pagination from "./Pagination.jsx";
import Dialog from "./Dialog.jsx";
import TextInspector from "./TextInspector.jsx";
import NavigationAction from "./NavigationAction.jsx";
import { SettingsPageActions, SettingsSummary } from "./SettingsWorkspace.jsx";
import LegacyParserManagement from "./LegacyParserManagement.jsx";
import ParserWorkbenchInput from "./ParserWorkbenchInput.jsx";
import ParserWorkbenchGroups from "./ParserWorkbenchGroups.jsx";
import ParserWorkbenchReview from "./ParserWorkbenchReview.jsx";
import ParserWorkbenchJobs from "./ParserWorkbenchJobs.jsx";
import { parserWorkbenchApi as api } from "./parserWorkbenchApi.js";
import { parserSessionBusy, parserWorkbenchError } from "./parserWorkbench.js";
import { startVisiblePolling } from "./visiblePolling.js";
import "./parserManagement.css";

const stepNames = { data: ["입력 선택", "Choose inputs"], formats: ["형식 확인 / 규칙 생성", "Formats / rules"], verify: ["원문 대조 / 파싱 검증", "Review / validate"] };

export function ParserRuleCatalog({ catalog, loading, error, onRetry, onCreate }) {
  const { locale } = useConsolePreferences(), w = (ko, en) => locale === "en" ? en : ko;
  const [vendor, setVendor] = useState(""), [query, setQuery] = useState(""), [offset, setOffset] = useState(0), [selected, setSelected] = useState(null);
  const rules = catalog?.rules || [], vendors = [...new Set(rules.map(rule => rule.waf_vendor).filter(Boolean))].sort((left, right) => left.localeCompare(right, locale === "en" ? "en" : "ko"));
  const rows = rules.filter(rule => (!vendor || rule.waf_vendor === vendor) && `${rule.name} ${rule.waf_vendor}`.toLocaleLowerCase().includes(query.toLocaleLowerCase()));
  const methods = { raw_payload: w("HTTP 요청 전체", "Whole HTTP request"), between_markers: w("시작 / 종료 표식", "Start / end markers"), json_string: w("JSON 키", "JSON key"), regex_capture: w("제한된 정규식", "Bounded regex") };
  return <section className="panel pw-rule-catalog" aria-busy={loading}>
    <SettingsPageActions><button type="button" className="primary" disabled={loading || !catalog} onClick={onCreate}>{w("규칙 만들기", "Create rules")}</button></SettingsPageActions>
    <div className="pw-section-heading"><div><h2>{w("벤더별 파서 규칙", "Parser rules by vendor")}</h2><p>{w("같은 벤더라도 로그 형식이 다르면 별도의 규칙으로 관리합니다.", "Keep separate rules for different formats, even for the same vendor.")}</p></div></div>
    <div className="pw-catalog-filters"><label>{w("벤더", "Vendor")}<select value={vendor} onChange={event => { setVendor(event.target.value); setOffset(0); }}><option value="">{w("모든 벤더", "All vendors")}</option>{vendors.map(value => <option key={value} value={value}>{value}</option>)}</select></label><label>{w("규칙 검색", "Find rule")}<input type="search" maxLength={120} value={query} onChange={event => { setQuery(event.target.value); setOffset(0); }} placeholder={w("규칙 이름 또는 벤더", "Rule name or vendor")} /></label><button type="button" className="secondary" onClick={onRetry}>{w("새로고침", "Refresh")}</button></div>
    {error ? <p className="error" role="alert">{error}</p> : <><DataTable label={w("파서 규칙 목록", "Parser rule list")} data={rows.slice(offset, offset + 10)} getRowId={row => `${row.version_id}:${row.rule_id}`} columns={[
      { id: "name", header: w("로그 형식 / 규칙", "Format / rule"), width: "32%", render: row => <button type="button" className="text-button" onClick={() => setSelected(row)}>{row.name}</button> },
      { id: "waf_vendor", header: w("벤더", "Vendor"), render: row => row.waf_vendor || w("미기록", "Not recorded") },
      { id: "method", header: w("추출 방식", "Extraction"), render: row => methods[row.method] || w("상세에서 확인", "See details") },
      { id: "version_name", header: w("저장 버전", "Saved version"), render: row => row.version_name || "—" },
      { id: "use", header: w("사용 상태", "Use"), render: row => row.in_production === true ? <span className="pw-pill production">{w("운영 사용 중", "In Production")}</span> : row.in_production === false ? <span className="pw-pill">{w("저장됨", "Saved")}</span> : "—" },
    ]} empty={loading ? w("규칙을 불러오는 중…", "Loading rules…") : vendor || query ? w("조건에 맞는 규칙이 없습니다.", "No matching rules.") : w("저장된 형식별 규칙이 없습니다. 입력을 선택해 규칙을 만들어 보세요.", "No format-specific rules are saved. Choose inputs to create rules.")} /><Pagination total={rows.length} limit={10} offset={offset} onOffsetChange={setOffset} label={w("파서 규칙 페이지", "Parser rule pages")} /></>}
    <Dialog open={Boolean(selected)} title={w("규칙 상세", "Rule details")} onClose={() => setSelected(null)} className="parser-dialog"><TextInspector value={selected} label={w("저장한 규칙", "Saved rule")} /></Dialog>
  </section>;
}

export function ParserRecentWork({ sessions, onOpen }) {
  const { locale } = useConsolePreferences(), w = (ko, en) => locale === "en" ? en : ko;
  const [offset, setOffset] = useState(0), limit = 10;
  const currentOffset = Math.min(offset, Math.max(0, Math.ceil(sessions.length / limit) - 1) * limit);
  return <section className="panel"><h3>{w("최근 규칙 작업", "Recent rule work")}</h3><DataTable label={w("파서 작업", "Parser work")} data={sessions.slice(currentOffset, currentOffset + limit)} columns={[{ id: "name", header: w("작업", "Work"), render: row => <button type="button" className="text-button" onClick={() => onOpen(row)}>{row.name}</button> }, { id: "updated_at", header: w("저장 시각", "Saved"), render: row => row.updated_at ? new Date(row.updated_at).toLocaleString() : "—" }]} /><Pagination total={sessions.length} limit={limit} offset={currentOffset} onOffsetChange={setOffset} label={w("규칙 작업 페이지", "Rule work pages")} /></section>;
}

export default function ParserWorkbench({ route: controlledRoute, onNavigate }) {
  const { locale } = useConsolePreferences(), w = (ko, en) => locale === "en" ? en : ko;
  const mounted = useRef(true), mutation = useRef(false), title = useRef(null);
  const [localRoute, setRoute] = useState({ view: "list" }), [catalog, setCatalog] = useState(null), [catalogError, setCatalogError] = useState(""), [catalogLoading, setCatalogLoading] = useState(true), [revision, reload] = useState(0);
  const [session, setSession] = useState(null), [sessionLoading, setSessionLoading] = useState(false), [busy, setBusy] = useState(false), [error, setError] = useState(""), [notice, setNotice] = useState("");
  const [modelAction, setModelAction] = useState(null), [profileId, setProfileId] = useState(""), [consent, setConsent] = useState(false), [pollStopped, setPollStopped] = useState(false);
  const [groupsDirty, setGroupsDirty] = useState(false), [discardRoute, setDiscardRoute] = useState(null);
  const route = controlledRoute || localRoute, step = route.step || "data";
  const performNavigate = value => { setGroupsDirty(false); if (onNavigate) onNavigate(value); else setRoute(value); };
  const navigate = value => { if (groupsDirty) setDiscardRoute(value); else performNavigate(value); };
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
  useEffect(() => {
    if (route.view === "legacy") return;
    const controller = new AbortController(); setCatalogLoading(true); setCatalogError("");
    api.catalog({ signal: controller.signal }).then(value => { if (!controller.signal.aborted) setCatalog(value); }).catch(err => { if (!controller.signal.aborted) setCatalogError(parserWorkbenchError(err, locale === "en")); }).finally(() => { if (!controller.signal.aborted) setCatalogLoading(false); });
    return () => controller.abort();
  }, [revision, locale, route.view === "legacy"]);
  useEffect(() => {
    setError(""); setNotice(""); title.current?.focus({ preventScroll: true });
    if (route.view !== "session" || !route.sessionId) { setSession(null); return; }
    const controller = new AbortController(); setSessionLoading(true); setSession(null);
    api.session(route.sessionId, { signal: controller.signal }).then(value => { if (!controller.signal.aborted) setSession(value); }).catch(err => { if (!controller.signal.aborted) setError(parserWorkbenchError(err, locale === "en")); }).finally(() => { if (!controller.signal.aborted) setSessionLoading(false); });
    return () => controller.abort();
  }, [route.view, route.sessionId, revision]);
  useEffect(() => { title.current?.focus({ preventScroll: true }); }, [step]);
  const active = parserSessionBusy(session);
  useEffect(() => {
    if (!session?.id || !active) return;
    let reads = 0, stop; setPollStopped(false);
    stop = startVisiblePolling(async signal => {
      const jobs = await api.jobs(session.id, { signal }); if (signal.aborted) return false;
      const running = jobs.items.some(job => ["pending", "running", "processing"].includes(job.status));
      if (!running) { const value = await api.session(session.id, { signal }); if (!signal.aborted) setSession(value); }
      if (++reads >= 120) { setPollStopped(true); stop?.(); return false; }
      return running;
    }, { interval: null, activeInterval: 2500, onError: () => { setPollStopped(true); stop?.(); } });
    return stop;
  }, [session?.id, active]);
  async function action(callback) {
    if (mutation.current) return false; mutation.current = true; setBusy(true); setError(""); setNotice("");
    try { await callback(); return true; } catch (err) { if (mounted.current) setError(parserWorkbenchError(err, locale === "en")); return false; } finally { mutation.current = false; if (mounted.current) setBusy(false); }
  }
  async function refresh() { const value = await api.session(session.id); if (mounted.current) setSession(value); }
  const profiles = catalog?.model_profiles || [], readyToVerify = session?.groups?.some(group => group.rule);
  const sessionRoute = next => navigate({ view: "session", sessionId: session.id, step: next });
  async function runModel() {
    await action(async () => { const payload = { profile_id: profileId, internal_data_confirmed: true, expected_revision: session.revision }; if (modelAction === "discover") await api.discover(session.id, payload); else await api.generate(session.id, payload); setModelAction(null); await refresh(); });
  }
  if (route.view === "legacy") return <div className="page-stack"><div className="pw-page-heading"><button className="back" type="button" onClick={() => navigate({ view: "list" })}>← {w("파서 관리", "Parser Management")}</button><p>{w("기존 버전과 비교, 개선안 및 확인 이력을 그대로 관리합니다.", "Manage legacy versions, comparisons, suggestions and review history.")}</p></div><LegacyParserManagement /></div>;
  return <div className="page-stack parser-management parser-workbench settings-workspace">
    {route.view === "list" ? <><h2 className="sr-only" ref={title} tabIndex={-1}>{w("로그 형식별 파서 규칙", "Parser rules for log formats")}</h2><SettingsPageActions><NavigationAction onClick={() => navigate({ view: "legacy" })}>{w("이전 파서 / 비교 이력", "Legacy parsers / comparisons")}</NavigationAction></SettingsPageActions></> : <div className="pw-page-heading"><div><button className="back" type="button" onClick={() => navigate({ view: "list" })}>← {w("규칙 목록", "Rule list")}</button><h2 ref={title} tabIndex={-1}>{session?.name || w("새 파서 규칙 만들기", "Create parser rules")}</h2><p>{w("원문을 보존하고 검토한 규칙만 공식 테스트를 거쳐 운영에 반영합니다.", "Preserve raw inputs. Reviewed rules proceed through Official Test and Production Review.")}</p></div><div className="parser-actions"><NavigationAction onClick={() => navigate({ view: "legacy" })}>{w("이전 파서 / 비교 이력", "Legacy parsers / comparisons")}</NavigationAction>{route.view === "session" && <button type="button" className="secondary" disabled={busy || sessionLoading} onClick={() => reload(value => value + 1)}>{w("새로고침", "Refresh")}</button>}</div></div>}
    {error && <p className="error" role="alert">{error}</p>}{notice && <p className="notice" role="status">{notice}</p>}
    {route.view === "list" ? <><SettingsSummary label={w("조회된 규칙 현황", "Loaded rule summary")} items={[
      { label: w("벤더", "Vendors"), value: catalogLoading || catalogError ? null : new Set((catalog?.rules || []).map(rule => rule.waf_vendor).filter(Boolean)).size, note: w("조회된 규칙 기준", "Within the loaded rules") },
      { label: w("저장된 규칙", "Saved rules"), value: catalogLoading || catalogError ? null : catalog?.rules?.length, note: w("벤더별 여러 형식 지원", "Multiple formats per vendor"), tone: "blue" },
      { label: w("운영 사용 규칙", "Production rules"), value: catalogLoading || catalogError ? null : (catalog?.rules || []).filter(rule => rule.in_production === true).length, note: w("운영에 승인된 규칙", "Approved for Production"), tone: "teal" },
    ]} /><ParserRuleCatalog catalog={catalog} loading={catalogLoading} error={catalogError} onRetry={() => reload(value => value + 1)} onCreate={() => navigate({ view: "new", step: "data" })} />{catalog?.sessions?.length > 0 && <ParserRecentWork sessions={catalog.sessions} onOpen={row => navigate({ view: "session", sessionId: row.id, step: "formats" })} />}</> : <>
      <ol className="pw-progress" aria-label={w("규칙 작성 순서", "Rule authoring steps")}>{Object.keys(stepNames).map((name, index) => <li key={name} aria-current={step === name ? "step" : undefined}><button type="button" disabled={!session || busy || groupsDirty || name === "verify" && !readyToVerify} onClick={() => sessionRoute(name)}><span>{index + 1}</span>{w(...stepNames[name])}</button></li>)}</ol>
      {catalogError && <p className="error" role="alert">{catalogError} <button type="button" className="text-button" onClick={() => reload(value => value + 1)}>{w("다시 조회", "Retry")}</button></p>}
      {route.view === "new" ? <ParserWorkbenchInput catalog={catalog} busy={busy || catalogLoading} onSubmit={payload => action(async () => { const value = await api.createSession(payload); if (mounted.current) navigate({ view: "session", sessionId: value.id, step: "formats" }); })} /> : sessionLoading ? <p role="status">{w("열람 이력을 기록하고 입력을 불러오는 중…", "Recording access and loading inputs…")}</p> : session && <>
        <div className="pw-session-context"><span>{w("입력 고정됨", "Inputs frozen")}</span><span>{w("제안용", "Authoring")} <strong>{session.split_summary?.authoring_count ?? "—"}</strong></span><span>{w("별도 검증용", "Validation")} <strong>{session.split_summary?.holdout_count ?? "—"}</strong></span><span>{w("기존 결과 / 운영 변경 없음", "Existing results / Production unchanged")}</span></div>
        {active && <p className="pw-running" role="status">{w("내부 모델이 작업 중입니다. 완료하면 결과를 표시합니다.", "The internal model is working. Results appear when ready.")}</p>}{pollStopped && <p className="notice" role="status">{w("자동 확인을 멈췄습니다. 새로고침으로 저장된 상태를 확인하세요. 작업을 다시 실행하지는 않습니다.", "Automatic refresh paused. Refresh to read stored status; this does not rerun the job.")}</p>}
        <ParserWorkbenchJobs jobs={session.jobs} />
        {step === "data" ? <section className="panel"><h3>{w("고정한 입력", "Frozen inputs")}</h3><p>{w("입력을 바꾸려면 새 작업을 만드세요. 이 작업의 입력은 변경하지 않습니다.", "Start new work to change inputs. This source remains frozen.")}</p><TextInspector value={session.source_summary} label={w("입력 범위", "Input scope")} /><button type="button" className="primary" onClick={() => sessionRoute("formats")}>{w("형식 확인으로 계속", "Continue to formats")}</button></section> : step === "formats" ? <><ParserWorkbenchGroups onDirty={setGroupsDirty} session={session} busy={busy || active || session.rules_locked} profiles={profiles} onModel={kind => { setModelAction(kind); setConsent(false); }} onConfirm={groups => action(async () => { await api.groups(session.id, groups, session.revision); await refresh(); })} />{readyToVerify && <div className="pw-next"><button type="button" className="primary" disabled={busy || active || groupsDirty} onClick={() => sessionRoute("verify")}>{w("원문 대조 / 검증으로 계속", "Continue to review / validation")} →</button></div>}</> : <ParserWorkbenchReview session={session} busy={busy || active} onSaveExpectation={items => action(async () => { await api.expectations(session.id, items, session.revision); await refresh(); })} onValidate={() => action(async () => { await api.validate(session.id, session.revision); await refresh(); })} onSaveCandidate={payload => action(async () => { await api.saveCandidate(session.id, { ...payload, expected_revision: session.revision }); await refresh(); reload(value => value + 1); })} />}
      </>}
    </>}
    <Dialog open={Boolean(discardRoute)} title={w("저장하지 않은 규칙 초안", "Unsaved rule draft")} onClose={() => setDiscardRoute(null)}><p>{w("확인하지 않은 규칙 수정이 있습니다. 나가면 이 수정 초안은 사라지며 저장된 규칙은 유지됩니다.", "Unconfirmed edits will be discarded if you leave. Saved rules are retained.")}</p><div className="parser-actions"><button type="button" className="secondary" onClick={() => setDiscardRoute(null)}>{w("계속 작성", "Keep editing")}</button><button type="button" className="primary" onClick={() => { const nextRoute = discardRoute; setDiscardRoute(null); performNavigate(nextRoute); }}>{w("수정 버리고 이동", "Discard edits and leave")}</button></div></Dialog>
    <p className="pw-safety-footer">{w("후보 저장은 운영 반영이 아닙니다. 공식 테스트 → 운영 반영 검토 → 운영에 반영. 파싱 결과와 LLM 제안은 정오탐 정답으로 자동 사용하지 않습니다.", "Saving is not applying to Production. Official Test → Production Review → Apply to Production. Parsed values and model suggestions never become verdict Ground Truth automatically.")}</p>
    <Dialog open={Boolean(modelAction)} title={modelAction === "discover" ? w("내부 LLM으로 형식 찾기", "Discover formats with internal LLM") : w("형식별 추출 규칙 생성", "Generate extraction rules")} onClose={() => { if (!busy) setModelAction(null); }}><div className="parser-form"><p>{w("제안용 문항의 원문과 Cookie를 내부 모델로 보냅니다. 별도 검증용 문항과 기대 판정은 보내지 않습니다. GPU와 처리 시간이 사용됩니다.", "Authoring inputs and cookies are sent to the internal model. Validation cases and expected verdicts are excluded. This uses GPU resources and processing time.")}</p><label>{w("검증된 내부 vLLM", "Validated internal vLLM")}<select value={profileId} disabled={busy} onChange={event => { setProfileId(event.target.value); setConsent(false); }}><option value="">{w("모델 선택", "Select model")}</option>{profiles.map(profile => <option key={profile.id} value={profile.id}>{profile.name}</option>)}</select></label><label className="checkbox-row"><input type="checkbox" checked={consent} disabled={busy || !profileId} onChange={event => setConsent(event.target.checked)} />{w("이 내부 모델로 원문을 보내는 것을 확인했습니다.", "I confirm sending raw input to this internal model.")}</label><p>{w("모델 제안은 초안입니다. 원문 사본과 작업 이력은 암호화되어 보존되며 자동으로 운영에 적용하지 않습니다.", "Model suggestions are drafts. Source copies and jobs are retained encrypted and never applied to Production automatically.")}</p><div className="parser-actions"><button type="button" className="secondary" disabled={busy} onClick={() => setModelAction(null)}>{w("취소", "Cancel")}</button><button type="button" className="primary" disabled={busy || !consent || !profiles.some(profile => profile.id === profileId)} onClick={runModel}>{busy ? w("접수 중…", "Submitting…") : w("확인 후 요청", "Confirm and request")}</button></div></div></Dialog>
  </div>;
}
