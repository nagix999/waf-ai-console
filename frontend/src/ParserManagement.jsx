import { useEffect, useRef, useState } from "react";
import { api } from "./api.js";
import { useConsolePreferences } from "./consolePreferences.jsx";
import DataTable, { Table } from "./DataTable.jsx";
import Pagination from "./Pagination.jsx";
import Dialog from "./Dialog.jsx";
import TextInspector from "./TextInspector.jsx";
import NavigationAction from "./NavigationAction.jsx";
import { BASELINE_PARSER_ID, defaultParserOptions, parserCatalogVersions, parserDraft, parserDraftPayload, parserJobFinished, parserSourcePayload, parserSuggestionDraft, parserVersionLabel } from "./parserManagement.js";
import { startVisiblePolling } from "./visiblePolling.js";
import "./parserManagement.css";

const useWords = () => { const { locale } = useConsolePreferences(); return (ko, en) => locale === "en" ? en : ko; };
const stateNames = { pending: ["대기", "Pending"], running: ["실행 중", "Running"], processing: ["실행 중", "Processing"], completed: ["완료", "Completed"], failed: ["실패", "Failed"], canceled: ["중지", "Canceled"] };
const date = value => value ? new Date(value).toLocaleString() : "—";
export function ParserRetentionNotice() {
  const w = useWords();
  return <p className="ux-muted parser-retention">{w("비교·개선 이력에는 원문 사본이 암호화되어 남으며, 원본 분석을 삭제해도 별도로 보존됩니다.", "Comparison and improvement history retains encrypted source copies independently of deleting the original analysis.")}</p>;
}
function ParserOptions({ value, onChange, disabled }) {
  const w = useWords(), change = (key, next) => onChange({ ...value, [key]: next });
  return <><label>{w("줄바꿈 형식", "Line ending format")}<select value={value.line_endings} disabled={disabled} onChange={e => change("line_endings", e.target.value)}><option value="actual">{w("실제 줄바꿈", "Actual line breaks")}</option><option value="escaped_crlf">{w("문자로 저장된 \\r\\n", "Literal \\r\\n")}</option><option value="escaped_lf">{w("문자로 저장된 \\n", "Literal \\n")}</option></select></label>
    <label>{w("요청 시작 줄의 공백", "Request line whitespace")}<select value={value.request_line_whitespace} disabled={disabled} onChange={e => change("request_line_whitespace", e.target.value)}><option value="strict">{w("기존 형식", "Strict")}</option><option value="flexible">{w("탭·연속 공백 허용", "Allow tabs and repeated spaces")}</option></select></label>
    <label className="checkbox-row"><input type="checkbox" checked={value.skip_leading_blank_lines} disabled={disabled} onChange={e => change("skip_leading_blank_lines", e.target.checked)} />{w("요청 앞의 빈 줄 건너뛰기", "Skip leading blank lines")}</label></>;
}
export function ParserVersionFacts({ version }) {
  const w = useWords();
  const endings = { actual: w("실제 줄바꿈", "Actual line breaks"), escaped_crlf: w("문자 \\r\\n", "Literal \\r\\n"), escaped_lf: w("문자 \\n", "Literal \\n") };
  if (version?.options?.profiles?.length) return <div className="parser-profile-summary"><p>{w("벤더·형식별 프로필", "Vendor-format profiles")} {version.options.profiles.length} · {w("맞는 형식이 없으면 기존 기본 파서 사용", "Unmatched inputs use the legacy baseline")}</p><ul>{version.options.profiles.map((profile, index) => <li key={index}><strong>{profile.waf_vendor}</strong><span>{profile.name} · {endings[profile.line_endings]}</span></li>)}</ul></div>;
  return <dl className="parser-facts"><div><dt>{w("줄바꿈 형식", "Line ending format")}</dt><dd>{endings[version?.options?.line_endings] || "—"}</dd></div><div><dt>{w("요청 시작 줄의 공백", "Request line whitespace")}</dt><dd>{version?.options?.request_line_whitespace === "flexible" ? w("탭·연속 공백 허용", "Tabs and repeated spaces") : version?.options ? w("기존 형식", "Strict") : "—"}</dd></div><div><dt>{w("앞쪽 빈 줄", "Leading blank lines")}</dt><dd>{version?.options ? version.options.skip_leading_blank_lines ? w("건너뜀", "Skipped") : w("보존", "Preserved") : "—"}</dd></div></dl>;
}
function ParserProfilesEditor({ options, onChange, disabled }) {
  const w = useWords(), profiles = options.profiles || [];
  const update = next => { const value = { ...options }; if (next.length) value.profiles = next; else delete value.profiles; onChange(value); };
  const change = (index, value) => update(profiles.map((profile, position) => position === index ? value : profile));
  return <section className="parser-profile-editor"><div className="parser-heading"><div><h3>{w("벤더·형식별 프로필", "Vendor-format profiles")}</h3><p>{w("같은 벤더도 줄바꿈 형식별로 나눌 수 있습니다. 벤더명과 형식이 모두 맞을 때만 해당 프로필을 사용합니다.", "One vendor can have multiple line-ending formats. Both vendor and format must match.")}</p></div><button type="button" className="secondary" disabled={disabled || profiles.length >= 20} onClick={() => update([...profiles, { name: "", waf_vendor: "", ...defaultParserOptions() }])}>{w("프로필 추가", "Add profile")}</button></div>
    {profiles.length > 0 && <p className="parser-safety">{w("맞지 않는 입력은 형식을 추측하지 않고 기존 기본 파서로 처리합니다. 같은 벤더·줄바꿈 조합은 중복 등록할 수 없습니다.", "Unmatched inputs use the legacy baseline without guessing. A vendor and line-ending combination cannot be duplicated.")}</p>}
    {profiles.map((profile, index) => <article key={index} className="parser-format-profile"><div className="parser-heading"><h4>{w("프로필", "Profile")} {index + 1}</h4><button type="button" className="text-button" disabled={disabled} onClick={() => update(profiles.filter((_, position) => position !== index))}>{w("프로필 삭제", "Remove profile")}</button></div>
      <div className="parser-profile-fields"><label>{w("프로필 이름", "Profile name")}<input required maxLength={120} value={profile.name} onChange={e => change(index, { ...profile, name: e.target.value })} placeholder={w("형식을 구분할 수 있는 이름", "A name that identifies the format")} /></label><label>{w("WAF 벤더", "WAF vendor")}<input required maxLength={120} value={profile.waf_vendor} onChange={e => change(index, { ...profile, waf_vendor: e.target.value })} placeholder={w("입력 이벤트의 waf_vendor 값", "The input event's waf_vendor value")} /></label></div>
      <ParserOptions value={profile} onChange={value => change(index, value)} disabled={disabled} /></article>)}
    <small>{w("최대 20개입니다. HTTP 버전·본문·없는 헤더를 임의로 채우지 않으며, 벤더 로그 전체의 필드 매핑은 수행하지 않습니다.", "Up to 20 profiles. Missing HTTP versions, bodies or headers are not invented. Vendor envelope field mapping is not performed.")}</small>
  </section>;
}
export function ParserJobResult({ job, versions = [], onUseSuggestion, onReview, reviewBusy = false }) {
  const w = useWords(); const [offset, setOffset] = useState(0);
  const result = job?.result, items = result?.items || [], item = items[offset];
  const suggestion = result?.suggestion, draft = parserSuggestionDraft(result, versions.find(v => v.id === job?.baseline_version_id));
  if (!parserJobFinished(job)) return <p role="status">{w("작업이 진행 중입니다. 이 화면에서만 결과를 자동 확인합니다.", "This job is in progress. Results refresh while this view is open.")}</p>;
  if (job.status !== "completed") return <p role="alert" className="error">{w("작업을 완료하지 못했습니다. 모델 연결·사용 권한과 선택한 문항을 확인하세요.", "The job did not complete. Check model connectivity, permissions and selected cases.")}</p>;
  if (!result) return <p>{w("저장된 결과가 없습니다.", "No stored result is available.")}</p>;
  if (suggestion) return <div className="page-stack"><p className="parser-safety">{w("LLM의 제안입니다. 정답이나 검증된 파서가 아니며 자동 저장·운영 적용하지 않습니다.", "This is an LLM suggestion, not a validated parser or Ground Truth. It is not saved or applied automatically.")}</p>
    <p>{w("검토 문항", "Cases reviewed")}: {result.sample_count ?? job.summary?.sample_count ?? "—"}</p>
    <div className="parser-suggestion-copy">{suggestion.reason_ko}</div>
    {suggestion.limitations_ko && <div className="parser-suggestion-copy"><h3>{w("확인이 필요한 부분", "Limitations to review")}</h3>{Array.isArray(suggestion.limitations_ko) ? <ul>{suggestion.limitations_ko.map((text, index) => <li key={index}>{text}</li>)}</ul> : suggestion.limitations_ko}</div>}
    <ParserVersionFacts version={suggestion} />
    {onUseSuggestion && <button type="button" className="primary" disabled={!draft} onClick={() => onUseSuggestion(draft)}>{w("검토 후 새 버전 작성", "Review and create a version")}</button>}
    {!draft && <p className="notice">{w("지원하는 형식 옵션에 맞지 않아 저장할 수 없습니다. 개발 검토가 필요합니다.", "The suggestion is outside supported format options and needs developer review.")}</p>}
  </div>;
  return <div className="page-stack"><p className="parser-safety">{w("파싱만 비교했습니다. LLM 판정·정확도 평가나 운영 반영은 수행하지 않았습니다.", "This compares parsing only. It does not run verdicts, measure verdict accuracy or change Production.")}</p>
    {result.summary && <><dl className="parser-facts"><div><dt>{w("비교 문항", "Compared cases")}</dt><dd>{result.summary.sample_count ?? result.summary.total ?? "—"}</dd></div><div><dt>{w("인식 결과 변경", "Changed results")}</dt><dd>{result.summary.changed_cases ?? "—"}</dd></div><div><dt>{w("원문 위치 검증", "Source span validation")}</dt><dd>{result.summary.all_valid_source_spans === true ? w("통과", "Passed") : w("확인 필요", "Needs Attention")}</dd></div></dl>
      {result.summary.status_counts && <div className="table-wrap"><Table><caption className="sr-only">{w("파싱 상태 비교", "Parsing status comparison")}</caption><thead><tr><th scope="col">{w("파서", "Parser")}</th><th scope="col">{w("성공", "Success")}</th><th scope="col">{w("일부 미인식", "Partial")}</th><th scope="col">{w("인식 실패", "Failed")}</th></tr></thead><tbody>{[["baseline", w("기존 파서", "Baseline")], ["candidate", w("비교 파서", "Candidate")]].map(([key, label]) => <tr key={key}><th scope="row">{label}</th>{["success", "partial", "failed"].map(status => <td key={status}>{result.summary.status_counts[key]?.[status] ?? "—"}</td>)}</tr>)}</tbody></Table></div>}
      {result.summary.selection_limited && <p className="notice">{w("선택 범위 중 일부 문항만 비교했습니다. 이 표는 전체 테스트의 결과가 아닙니다.", "Only part of the selected scope was compared. These counts do not represent the whole Test.")}</p>}</>}
    {result.result_truncated && <p className="notice">{w("표시 한도에 따라 일부 결과가 생략됐습니다.", "Some results were omitted due to the display limit.")}</p>}
    {item ? <><Pagination total={items.length} limit={1} offset={offset} onOffsetChange={setOffset} label={w("비교 문항", "Comparison cases")} />
      <article className="parser-result-case" key={item.analysis_id || offset}><h3>{w("문항", "Case")} {offset + 1} · {w("원문과 인식 결과", "Raw input and parsed results")}</h3><p className="ux-muted">{w("WAF 벤더", "WAF vendor")}: {item.waf_vendor || w("미기록", "Not recorded")}</p><pre className="parser-source" tabIndex={0} aria-label={w("HTTP 원문", "Raw HTTP")}>{item.raw_payload ?? w("원문 미제공", "Raw input unavailable")}</pre>
        <div className="parser-compare-columns">{[["baseline", w("기존 파서", "Baseline parser")], ["candidate", w("비교 파서", "Candidate parser")]].map(([key, label]) => <section key={key}><h4>{label}</h4>{item[key]?.capture_selection?.status === "generic_fallback" ? <p className="notice">{w("맞는 벤더·형식 프로필이 없어 기존 기본 파서를 사용했습니다.", "No vendor-format profile matched. The legacy baseline was used.")}</p> : item[key]?.capture_selection?.profile_name && <p className="ux-muted">{w("선택한 형식", "Selected format")}: {item[key].capture_selection.profile_name}</p>}<TextInspector label={label} value={item[key]} /></section>)}</div>
        <TextInspector label={w("달라진 항목", "Changed fields")} value={item.changed_fields || []} /></article></> : <p>{w("비교할 문항이 없습니다.", "No cases to compare.")}</p>}
    {onReview && <div className="parser-safety"><p>{w("각 문항의 요청 줄·헤더·본문과 원문 위치를 직접 확인하세요. 이 확인은 정오탐 평가나 운영 반영을 대신하지 않습니다.", "Review each case's request line, headers, body and source spans. This confirmation does not replace verdict evaluation or Production Review.")}</p><button type="button" className="secondary" disabled={reviewBusy || job.summary?.structure_reviewed === true || result.summary?.all_valid_source_spans !== true || !(result.summary?.sample_count > 0) || !((result.summary?.status_counts?.candidate?.success || 0) + (result.summary?.status_counts?.candidate?.partial || 0))} onClick={onReview}>{job.summary?.structure_reviewed === true ? w("파싱 결과 확인 완료", "Parsing results reviewed") : w("파싱 결과 확인 완료로 기록", "Mark parsing results reviewed")}</button></div>}
  </div>;
}

export default function ParserManagement() {
  const w = useWords(), mounted = useRef(true), pending = useRef(false), jobRequest = useRef(null), draftName = useRef(null);
  const [catalog, setCatalog] = useState(null), [profiles, setProfiles] = useState([]), [runs, setRuns] = useState([]), [jobs, setJobs] = useState([]);
  const [loading, setLoading] = useState(true), [reload, setReload] = useState(0), [error, setError] = useState(""), [notice, setNotice] = useState(""), [busy, setBusy] = useState(false);
  const [selectedId, setSelectedId] = useState(""), [baselineId, setBaselineId] = useState(""), [versionsOffset, setVersionsOffset] = useState(0), [jobsOffset, setJobsOffset] = useState(0);
  const [jobsTotal, setJobsTotal] = useState(0), [jobsLoading, setJobsLoading] = useState(true), [jobsError, setJobsError] = useState(false);
  const [draft, setDraft] = useState(null), [technical, setTechnical] = useState(false), [confirm, setConfirm] = useState(false), [job, setJob] = useState(null), [jobOpen, setJobOpen] = useState(false), [jobLoading, setJobLoading] = useState(false), [jobError, setJobError] = useState(false);
  const [source, setSource] = useState("test"), [testRunId, setTestRunId] = useState(""), [analysisIds, setAnalysisIds] = useState(""), [maxCases, setMaxCases] = useState(20), [profileId, setProfileId] = useState(""), [internalConfirmed, setInternalConfirmed] = useState(false);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; jobRequest.current?.abort(); }; }, []);
  useEffect(() => { if (draft) draftName.current?.focus(); }, [Boolean(draft)]);
  useEffect(() => {
    const controller = new AbortController(); setLoading(true); setError("");
    Promise.all([api.parsers({ signal: controller.signal }), api.agentSettings({ signal: controller.signal }), api.testRuns({ limit: 100, offset: 0 }, { signal: controller.signal })]).then(([parsers, agents, tests]) => {
      if (controller.signal.aborted) return;
      setCatalog(parsers); setProfiles((agents.profiles || []).filter(p => p.provider === "vllm" && p.can_assign)); setRuns(tests.items || []);
      setSelectedId(old => old || parsers.items.find(v => v.id !== parsers.active_version_id)?.id || parsers.active_version_id || BASELINE_PARSER_ID);
      setBaselineId(old => old || parsers.active_version_id || BASELINE_PARSER_ID); setLoading(false);
    }).catch(() => { if (!controller.signal.aborted) { setError("load"); setLoading(false); } });
    return () => controller.abort();
  }, [reload]);
  useEffect(() => {
    const controller = new AbortController(); setJobsLoading(true); setJobsError(false);
    api.parserJobs({ limit: 5, offset: jobsOffset }, { signal: controller.signal }).then(history => {
      if (controller.signal.aborted) return; setJobs(history.items || []); setJobsTotal(history.total ?? history.items?.length ?? 0); setJobsLoading(false);
    }).catch(() => { if (!controller.signal.aborted) { setJobsError(true); setJobsLoading(false); } });
    return () => controller.abort();
  }, [reload, jobsOffset]);
  useEffect(() => { if (job?.id) setJobs(old => old.map(row => row.id === job.id ? { ...row, status: job.status, completed_at: job.completed_at, summary: job.summary } : row)); }, [job?.id, job?.status, job?.summary?.structure_reviewed]);
  useEffect(() => {
    if (!jobOpen || !job?.id || parserJobFinished(job)) return;
    return startVisiblePolling(async signal => { const value = await api.parserJob(job.id, { signal }); if (!signal.aborted) { setJob(value); setJobError(false); } return !parserJobFinished(value); }, { interval: null, activeInterval: 2500, onError: () => setJobError(true) });
  }, [jobOpen, job?.id, parserJobFinished(job)]);
  const versions = parserCatalogVersions(catalog), selected = versions.find(v => v.id === selectedId), active = versions.find(v => v.id === catalog?.active_version_id);
  const baseline = versions.find(v => v.id === baselineId);
  let sourcePayload = null; try { sourcePayload = parserSourcePayload({ source, testRunId, analysisIds, maxCases }); } catch { /* Keep the form editable. Server validates again. */ }
  const canSuggest = Boolean(sourcePayload && Number(maxCases) <= 20 && profiles.some(p => p.id === profileId) && internalConfirmed && !busy);
  const newDraft = () => { setDraft(parserDraft(selected || active)); setError(""); };
  async function mutate(action) {
    if (pending.current) return; pending.current = true; setBusy(true); setError(""); setNotice("");
    try { await action(); } catch { if (mounted.current) setError("action"); } finally { pending.current = false; if (mounted.current) setBusy(false); }
  }
  async function openJob(id) {
    jobRequest.current?.abort(); const controller = new AbortController(); jobRequest.current = controller;
    setJob(null); setJobLoading(true); setJobError(false); setJobOpen(true);
    try { const value = await api.parserJob(id, { signal: controller.signal }); if (mounted.current && !controller.signal.aborted) setJob(value); } catch { if (mounted.current && !controller.signal.aborted) setJobError(true); } finally { if (mounted.current && !controller.signal.aborted) setJobLoading(false); }
  }
  function runJob(kind) {
    return mutate(async () => {
      const sample = parserSourcePayload({ source, testRunId, analysisIds, maxCases }, kind);
      const payload = { ...sample, baseline_version_id: baselineId };
      const value = kind === "suggestion" ? await api.suggestParser({ ...payload, profile_id: profileId, internal_data_confirmed: true }) : await api.compareParsers({ ...payload, candidate_version_id: selectedId });
      if (!mounted.current) return; setConfirm(false); setReload(n => n + 1); await openJob(value.id);
    });
  }
  async function saveVersion(event) {
    event.preventDefault(); await mutate(async () => {
      const value = await api.createParser(parserDraftPayload(draft));
      if (!mounted.current) return; setSelectedId(value.id); setDraft(null); setReload(n => n + 1); setNotice(w("새 버전을 저장했습니다. 운영은 바뀌지 않았습니다.", "Version saved. Production is unchanged."));
    });
  }
  return <div className="page-stack parser-management" aria-busy={loading}>
    <section className="panel parser-current"><div className="parser-heading"><div><h2>{w("현재 운영 파서", "Current Production parser")}</h2><p>{w("공식 테스트 → 운영 반영 검토 → 운영에 반영을 통해서만 변경합니다.", "Changes require Official Test → Production Review → Apply to Production.")}</p></div><div className="parser-actions"><button type="button" className="secondary" disabled={loading || busy} onClick={() => setReload(n => n + 1)}>{w("새로고침", "Refresh")}</button><NavigationAction href="#evaluate/tests/new">{w("공식 테스트 만들기", "Create Official Test")}</NavigationAction></div></div>
      <strong>{active ? parserVersionLabel(active) : loading ? w("불러오는 중…", "Loading…") : w("확인할 수 없음", "Unavailable")}</strong><p className="ux-muted">{w("원문은 보존하며 인식 결과와 원문 위치를 별도로 기록합니다. 파싱 성공은 로그 완전성이나 판정 정확도를 보장하지 않습니다.", "Raw input is preserved with separate parsed values and source spans. Parsing success does not guarantee completeness or verdict accuracy.")}</p>
      <ParserVersionFacts version={active} />
    </section>
    {error && <p role="alert" className="error">{error === "load" ? w("파서 정보를 불러오지 못했습니다. 다시 조회하세요.", "Could not load parser information. Please refresh.") : w("작업을 처리하지 못했습니다. 선택한 버전·문항·모델 상태를 확인하세요.", "Could not complete the request. Check the selected versions, cases and model readiness.")}</p>}{notice && <p role="status" className="notice">{notice}</p>}
    <section className="panel"><div className="parser-heading"><div><h2>{w("저장 버전", "Saved versions")}</h2><p>{w("저장한 버전은 수정하지 않습니다. 변경할 때 새 버전을 만드세요.", "Saved versions are immutable. Create a new version for changes.")}</p></div><button type="button" className="primary" disabled={!catalog || busy} onClick={newDraft}>{w("새 버전 작성", "Create version")}</button></div>
      {catalog?.limited && <p className="notice">{w("최근", "Showing the latest")} {catalog.recent_limit ?? 200}{w("개 버전과 현재 운영 버전을 표시합니다. 이전 버전은 삭제되지 않았습니다.", " versions plus the current Production version. Older versions have not been deleted.")}</p>}
      <DataTable data={versions.slice(versionsOffset, versionsOffset + 5)} label={w("파서 버전", "Parser versions")} empty={loading ? w("불러오는 중…", "Loading…") : w("저장 버전이 없습니다.", "No saved versions.")} rowClassName={row => row.id === selectedId ? "parser-version-selected" : ""} columns={[
        { id: "name", header: w("버전", "Version"), render: row => <><button type="button" className="text-button" aria-pressed={row.id === selectedId} onClick={() => setSelectedId(row.id)}>{parserVersionLabel(row)}</button>{row.id === catalog?.active_version_id && <small>{w("운영 적용 중", "In Production")}</small>}</> },
        { id: "description", header: w("변경 내용", "Changes"), render: row => row.description || "—" },
        { id: "created_at", header: w("등록 시각", "Created"), render: row => date(row.created_at) },
      ]} /><Pagination total={versions.length} limit={5} offset={versionsOffset} onOffsetChange={setVersionsOffset} label={w("파서 버전 페이지", "Parser version pages")} />
    </section>
    <div className="parser-workspace"><section className="panel"><div className="parser-heading"><div><h2>{w("기존 입력으로 비교", "Compare existing inputs")}</h2><p>{w("선택한 입력만 재파싱합니다. LLM 호출 없이 기존 분석을 보존합니다.", "Reparse selected inputs without LLM calls or changes to existing analyses.")}</p></div>{selected && <button type="button" className="text-button" onClick={() => setTechnical(true)}>{w("기술정보", "Technical details")}</button>}</div>
      <div className="parser-form"><label>{w("기존 파서", "Baseline parser")}<select value={baselineId} disabled={!catalog || busy} onChange={e => setBaselineId(e.target.value)}>{versions.map(v => <option key={v.id} value={v.id}>{parserVersionLabel(v)}</option>)}</select></label>
        <label>{w("비교 파서", "Candidate parser")}<select value={selectedId} disabled={!catalog || busy} onChange={e => setSelectedId(e.target.value)}>{versions.map(v => <option key={v.id} value={v.id}>{parserVersionLabel(v)}</option>)}</select></label>
        {selected && <ParserVersionFacts version={selected} />}
        <label>{w("입력 선택", "Input source")}<select value={source} disabled={busy} onChange={e => setSource(e.target.value)}><option value="test">{w("테스트에서 선택", "Select a Test")}</option><option value="analyses">{w("분석 ID로 선택", "Select by analysis IDs")}</option></select></label>
        {source === "test" ? <label>{w("테스트", "Test")}<select aria-label={w("테스트", "Test")} value={testRunId} disabled={busy} onChange={e => setTestRunId(e.target.value)}><option value="">{w("테스트 선택", "Select Test")}</option>{runs.map(run => <option key={run.id} value={run.id}>{run.name || w("이름 없는 테스트", "Unnamed Test")}</option>)}</select><small>{w("최근 100개 테스트에서 선택합니다. 더 오래된 입력은 분석 ID로 지정할 수 있습니다.", "Select from the latest 100 Tests. Use analysis IDs for older inputs.")}</small></label> : <label>{w("분석 ID", "Analysis IDs")}<textarea rows={3} value={analysisIds} disabled={busy} onChange={e => setAnalysisIds(e.target.value)} placeholder={w("쉼표 또는 줄바꿈으로 구분", "Separate with commas or line breaks")} spellCheck={false} /></label>}
        {source === "test" && runs.some(run => run.id === testRunId) && <NavigationAction href={`#evaluate/tests/${testRunId}/statistics`}>{w("선택 테스트 통계 보기", "View test statistics")}</NavigationAction>}
        <ParserRetentionNotice />
        <label>{w("최대 문항 수", "Maximum cases")}<input aria-label={w("최대 문항 수", "Maximum cases")} type="number" min={1} max={100} step={1} value={maxCases} disabled={busy} onChange={e => setMaxCases(e.target.value)} /><small>{w("파싱 비교는 최대 100건, LLM 개선안은 최대 20건입니다. 서버가 유효한 문항을 고정해 처리합니다.", "Up to 100 cases for parsing comparisons and 20 for LLM suggestions. The server pins eligible cases.")}</small></label>
        <button type="button" className="primary" disabled={loading || busy || !sourcePayload || !selectedId || baselineId === selectedId} onClick={() => runJob("comparison")}>{w("파싱 비교 실행", "Compare parsing")}</button>
        <p className="ux-muted">{w("검증 조건은 바꾸지 않습니다. 경고 감소만으로 Verifier를 생략하거나 운영에 적용하지 않습니다.", "Verifier rules are unchanged. Fewer warnings do not automatically skip verification or apply a parser to Production.")}</p>
      </div>
    </section>
    <section className="panel"><div className="parser-heading"><div><h2>{w("LLM으로 개선안 검토", "Review improvements with an LLM")}</h2><p>{w("왼쪽에서 선택한 입력의 형식을 내부 모델로 검토합니다.", "Review the selected input format using an internal model.")}</p></div></div>
      <p className="parser-safety">{w("지원하는 형식 옵션만 제안합니다. Python·정규식 코드를 생성해 실행하지 않으며, 제안은 자동으로 저장·적용하지 않습니다.", "Suggestions are limited to supported format options. No generated Python or regex is executed, and suggestions are never saved or applied automatically.")}</p>
      <div className="parser-form"><label>{w("내부 vLLM 프로필", "Internal vLLM profile")}<select value={profileId} disabled={busy} onChange={e => { setProfileId(e.target.value); setInternalConfirmed(false); }}><option value="">{w("검증된 내부 모델 선택", "Select a validated internal model")}</option>{profiles.map(p => <option key={p.id} value={p.id}>{p.name}</option>)}</select></label>
        {!profiles.length && !loading && <p className="notice">{w("사용 가능한 내부 vLLM 프로필이 없습니다. 모델 등록과 전체 검증이 필요합니다.", "No validated internal vLLM profile is available. Register and fully validate a model first.")}</p>}
        <label className="checkbox-row"><input type="checkbox" checked={internalConfirmed} disabled={busy || !profileId} onChange={e => setInternalConfirmed(e.target.checked)} />{w("선택한 원문·Cookie를 이 내부 모델로 보내는 것을 확인했습니다.", "I confirm sending the selected raw input and cookies to this internal model.")}</label>
        <small>{w("OpenAI·외부 모델은 사용하지 않습니다. GPU 자원과 처리 시간이 사용되며 원문 열람·작업 이력이 남습니다.", "OpenAI and external models are not used. This consumes GPU resources and processing time; raw-data access and job history are audited.")}</small>
        <button type="button" className="secondary" disabled={!canSuggest} onClick={() => setConfirm(true)}>{w("개선안 생성", "Generate suggestion")}</button><NavigationAction href="#configure/llm-profiles">{w("LLM 프로필 보기", "View LLM Profiles")}</NavigationAction>
      </div>
    </section></div>
    <section className="panel"><div className="parser-heading"><div><h2>{w("비교·개선 이력", "Comparison & suggestion history")}</h2><p>{w("상세를 열 때 원문 열람 이력을 남깁니다. 이력 조회만으로 모델을 호출하지 않습니다.", "Opening details records raw-data access. Viewing history does not call a model.")}</p></div></div>
      {jobsError && <p className="error" role="alert">{w("작업 이력을 불러오지 못했습니다. 다시 조회하세요.", "Could not load job history. Please refresh.")}</p>}
      <DataTable label={w("파서 작업 이력", "Parser jobs")} data={jobs} empty={jobsLoading ? w("불러오는 중…", "Loading…") : jobsError ? w("이력 확인 불가", "History unavailable") : w("아직 실행한 작업이 없습니다.", "No jobs yet.")} columns={[
        { id: "kind", header: w("작업", "Job"), render: row => <button type="button" className="text-button" onClick={() => openJob(row.id)}>{row.kind === "suggestion" ? w("LLM 개선안", "LLM suggestion") : w("파싱 비교", "Parsing comparison")}</button> },
        { id: "status", header: w("상태", "Status"), render: row => <span className="parser-job-state" data-status={row.status}>{stateNames[row.status] ? w(...stateNames[row.status]) : w("확인 필요", "Needs Attention")}</span> },
        { id: "created_at", header: w("접수 시각", "Created"), render: row => date(row.created_at) },
      ]} /><Pagination total={jobsTotal} limit={5} offset={jobsOffset} onOffsetChange={setJobsOffset} disabled={jobsLoading} label={w("파서 작업 페이지", "Parser job pages")} />
    </section>
    <Dialog open={Boolean(draft)} title={w("새 파서 버전", "New parser version")} onClose={() => { if (!busy) setDraft(null); }} className="parser-dialog">{draft && <form className="parser-form" onSubmit={saveVersion}>
      <p className="parser-safety">{w("이 저장은 운영을 변경하지 않습니다. 같은 입력으로 파싱을 비교한 뒤 공식 테스트에서 판정·근거 품질을 검증하세요.", "Saving does not change Production. Compare parsing on the same inputs, then evaluate verdicts and evidence with an Official Test.")}</p>
      <fieldset disabled={busy}><label>{w("버전 이름", "Version name")}<input ref={draftName} required maxLength={120} value={draft.name} onChange={e => setDraft({ ...draft, name: e.target.value })} placeholder={w("변경 목적을 알 수 있는 이름", "A name that describes the change")} /></label>
        <label>{w("변경 설명", "Change description")}<textarea rows={3} maxLength={2000} value={draft.description} onChange={e => setDraft({ ...draft, description: e.target.value })} placeholder={w("형식과 검토할 내용을 기록하세요. 실제 원문·비밀값은 넣지 마세요.", "Describe the format and review points. Do not include raw logs or secrets.")} /></label>
        <div><h3>{w("단일 형식 옵션", "Single-format options")}</h3><p className="ux-muted">{draft.options.profiles?.length ? w("아래 벤더·형식별 프로필을 사용합니다. 위의 단일 옵션은 적용하지 않으며, 기존 프로필은 유지됩니다. LLM 제안을 사용할 프로필의 옵션을 직접 확인·수정하세요.", "The vendor-format profiles below take precedence; the single-format options do not apply. Existing profiles are retained. Review and edit the intended profile to use an LLM suggestion.") : w("벤더·형식별 프로필이 없을 때 이 옵션을 사용합니다.", "These options apply when no vendor-format profiles are defined.")}</p></div>
        <ParserOptions value={draft.options} onChange={options => setDraft({ ...draft, options })} disabled={busy || Boolean(draft.options.profiles?.length)} />
        <ParserProfilesEditor options={draft.options} onChange={options => setDraft({ ...draft, options })} disabled={busy} />
      </fieldset>{error && <p role="alert" className="error">{w("버전을 저장하지 못했습니다. 입력값을 확인하세요.", "Could not save the version. Check the fields.")}</p>}
      <div className="parser-actions"><button type="submit" className="primary" disabled={busy || !draft.name.trim()}>{w("새 버전 저장", "Save new version")}</button><button type="button" className="secondary" disabled={busy} onClick={() => setDraft(null)}>{w("취소", "Cancel")}</button></div>
    </form>}</Dialog>
    <Dialog open={confirm} title={w("내부 모델로 개선안 생성", "Generate a suggestion with an internal model")} onClose={() => { if (!busy) setConfirm(false); }}>
      <p>{w("선택한 입력을 최대", "Send up to")} {Number(maxCases)}{w("건까지 내부 모델로 보냅니다. 판정은 실행하지 않으며 기존 결과·운영 파서는 바뀌지 않습니다.", " selected inputs to the internal model. No verdict is run, and existing results and the Production parser are unchanged.")}</p>
      <ParserRetentionNotice /><p>{profiles.find(p => p.id === profileId)?.name}</p>
      <div className="action-row"><button type="button" className="secondary" disabled={busy} onClick={() => setConfirm(false)}>{w("취소", "Cancel")}</button><button type="button" className="primary" disabled={!canSuggest} onClick={() => runJob("suggestion")}>{busy ? w("접수 중…", "Submitting…") : w("확인 후 생성", "Confirm and generate")}</button></div>
    </Dialog>
    <Dialog open={technical} title={w("파서 기술정보", "Parser technical details")} onClose={() => setTechnical(false)} className="parser-dialog"><TextInspector label={w("선택한 버전", "Selected version")} value={selected} /><p>{w("분석마다 파서 버전과 원문 위치를 고정합니다. 과거 결과는 다시 쓰지 않습니다.", "Parser versions and source spans are pinned per execution. Historical results are not rewritten.")}</p></Dialog>
    <Dialog open={jobOpen} title={job?.kind === "suggestion" ? w("LLM 개선안", "LLM suggestion") : w("파싱 비교 결과", "Parsing comparison result")} onClose={() => { if (!busy) { jobRequest.current?.abort(); setJobOpen(false); setJob(null); } }} className="parser-dialog">{jobLoading ? <p role="status">{w("열람 이력을 기록하고 불러오는 중…", "Recording access and loading…")}</p> : jobError ? <p role="alert" className="error">{w("결과를 읽지 못했습니다. 이력을 다시 열어주세요.", "Could not read the result. Open the history item again.")}</p> : job && <ParserJobResult key={job.id} job={job} versions={versions} reviewBusy={busy} onReview={job.kind === "comparison" ? () => mutate(async () => { await api.reviewParserComparison(job.id); await openJob(job.id); setReload(n => n + 1); }) : undefined} onUseSuggestion={value => { setJobOpen(false); setJob(null); setDraft(value); }} />}{error && <p role="alert" className="error">{w("작업을 처리하지 못했습니다. 이력을 다시 열어 확인하세요.", "Could not complete the request. Reopen the history item to check.")}</p>}</Dialog>
  </div>;
}
