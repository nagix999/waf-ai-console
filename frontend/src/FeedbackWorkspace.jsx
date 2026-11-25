import { useEffect, useRef, useState } from "react";
import { api } from "./api.js";
import { useConsolePreferences } from "./consolePreferences.jsx";
import DataTable from "./TabulatorTable.jsx";
import Pagination from "./Pagination.jsx";
import Dialog from "./Dialog.jsx";
import { Icon } from "./Icon.jsx";
import { feedbackCategories, feedbackCategoriesOf, feedbackCategoryLabel, feedbackCount, feedbackDateIssue, feedbackDateTime,
  feedbackQuery, feedbackVersionLabel, initialFeedbackState, normalizeFeedbackState, recentFeedbackRange,
  unrecordedFeedbackVersion, validFeedbackSummary } from "./feedbackView.js";
import "./feedbackWorkspace.css";

const text = value => typeof value === "string" ? value : "";
const emptyView = key => ({ key, loading: true, list: null, summary: null, versions: null, errors: {} });

function FeedbackCategories({ item, locale }) {
  const categories = feedbackCategoriesOf(item);
  return <div className="feedback-categories">{categories.length ? categories.map(category => <span className="feedback-category-badge" key={category}>{feedbackCategoryLabel(category, locale)}</span>)
    : <span className="muted">{locale === "en" ? text(item.comment).trim() ? "Comment only" : "No category selected" : text(item.comment).trim() ? "의견만 등록" : "유형 선택 없음"}</span>}</div>;
}

function FeedbackDetail({ id, onClose, onOpen, onUnauthorized, locale }) {
  const w = (ko, en) => locale === "en" ? en : ko;
  const [value, setValue] = useState({ id: null, item: null, loading: false, error: "" });
  const [reload, setReload] = useState(0);
  const current = value.id === id ? value : { item: null, loading: Boolean(id), error: "" };
  const unauthorized = useRef(onUnauthorized); unauthorized.current = onUnauthorized;
  useEffect(() => {
    if (!id) return;
    let active = true; const controller = new AbortController();
    setValue({ id, item: null, loading: true, error: "" });
    api.feedbackDetail(id, { signal: controller.signal }).then(item => {
      if (!item || item.id !== id || typeof item.analysis_id !== "string") throw new Error("invalid_feedback");
      if (active) setValue({ id, item, loading: false, error: "" });
    }).catch(error => {
      if (!active) return;
      setValue({ id, item: null, loading: false, error: error.status === 404 ? "missing" : error.status === 403 ? "forbidden" : "read" });
      if (error.status === 401) unauthorized.current?.();
    });
    return () => { active = false; controller.abort(); };
  }, [id, reload]);
  const item = current.item;
  return <Dialog open={Boolean(id)} title={w("피드백 상세", "Feedback details")} onClose={onClose} className="feedback-detail-dialog">
    {current.loading && <p role="status" className="feedback-loading">{w("피드백을 불러오는 중입니다…", "Loading feedback…")}</p>}
    {current.error && <div className="feedback-read-error" role="alert"><p>{current.error === "missing" ? w("삭제되었거나 찾을 수 없는 피드백입니다.", "This feedback no longer exists or could not be found.") : current.error === "forbidden" ? w("관리자 권한으로만 피드백을 조회할 수 있습니다.", "Administrator access is required to view feedback.") : w("피드백을 불러오지 못했습니다. 다시 조회해 주세요.", "Could not load feedback. Please retry.")}</p>{current.error !== "forbidden" && <button type="button" className="secondary" onClick={() => setReload(value => value + 1)}>{w("다시 조회", "Retry")}</button>}</div>}
    {item && <>
      <article className="feedback-detail-entry"><div className="feedback-detail-byline"><strong>{text(item.analyst_id) || w("작성자 미기록", "Author not recorded")}</strong><time dateTime={item.created_at}>{feedbackDateTime(item.created_at, locale)} <span>KST</span></time></div>
        <FeedbackCategories item={item} locale={locale} /><p className="feedback-detail-comment">{text(item.comment) || w("추가 의견 없음", "No additional comment")}</p></article>
      <dl className="feedback-detail-facts">{[
        [w("회사", "Company"), item.company_name], [w("탐지명", "Signature"), item.signature],
        [w("분석 용도", "Analysis purpose"), item.analysis_purpose === "production" ? "Production" : item.analysis_purpose === "test" ? "Test" : w("미기록", "Not recorded")],
        [w("운영 버전", "Production version"), feedbackVersionLabel(item, locale)],
        [w("이벤트 ID", "Event ID"), item.event_id], [w("분석 ID", "Analysis ID"), item.analysis_id],
        [w("연동 시스템", "Source system"), item.source_system], [w("피드백 ID", "Feedback ID"), item.id],
      ].map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{text(value) || w("미기록", "Not recorded")}</dd></div>)}</dl>
      <p className="feedback-detail-note">{w("피드백은 추가형 이력으로 보존합니다. 모델 입력, 참고 판정과 공식 평가 정답에 자동 반영하지 않습니다.", "Feedback is preserved as append-only history. It is not automatically used in model inputs, reference verdicts or official evaluation answers.")}</p>
    </>}
    <div className="feedback-dialog-actions"><button type="button" className="secondary" onClick={onClose}>{w("닫기", "Close")}</button><button type="button" className="primary" disabled={!item || !onOpen} onClick={() => { onClose(); onOpen?.(item.analysis_id, item.analysis_purpose, item.test_run_id); }}>{w("원 분석 보기", "Open original analysis")}<Icon name="arrow" size={15} /></button></div>
  </Dialog>;
}

export default function FeedbackWorkspace({ state, onStateChange, onOpen, onUnauthorized }) {
  const { locale } = useConsolePreferences();
  const w = (ko, en) => locale === "en" ? en : ko;
  const [localState, setLocalState] = useState(initialFeedbackState);
  const applied = normalizeFeedbackState(state || localState);
  const [draft, setDraft] = useState(applied);
  const [formError, setFormError] = useState("");
  const [reload, setReload] = useState(0);
  const startRef = useRef(null);
  const unauthorized = useRef(onUnauthorized); unauthorized.current = onUnauthorized;
  const scopeKey = JSON.stringify([applied.start, applied.end, applied.purpose, applied.version, applied.category, applied.analysis, applied.query]);
  const scopeQuery = feedbackQuery(applied), listQuery = feedbackQuery(applied, true);
  const requestKey = JSON.stringify(listQuery);
  const [view, setView] = useState(() => emptyView(requestKey));
  const current = view.key === requestKey ? view : emptyView(requestKey);
  useEffect(() => { setDraft(applied); setFormError(""); }, [scopeKey]);
  useEffect(() => {
    if (!listQuery) { setView({ ...emptyView(requestKey), loading: false }); return; }
    let active = true; const controller = new AbortController();
    setView(emptyView(requestKey));
    const options = { signal: controller.signal };
    Promise.allSettled([api.feedback(listQuery, options), api.feedbackSummary(scopeQuery, options), api.feedbackVersions(scopeQuery, options)]).then(results => {
      if (!active) return;
      const next = { ...emptyView(requestKey), loading: false };
      let sessionExpired = false;
      results.forEach((result, index) => {
        const key = ["list", "summary", "versions"][index];
        if (result.status === "rejected") {
          next.errors[key] = result.reason?.status === 403 ? "forbidden" : "read";
          if (result.reason?.status === 401) sessionExpired = true;
          return;
        }
        const value = result.value;
        const valid = key === "summary" ? validFeedbackSummary(value)
          : Array.isArray(value?.items) && (key !== "list" || feedbackCount(value.total) !== null);
        if (valid) next[key] = value; else next.errors[key] = "read";
      });
      setView(next);
      if (sessionExpired) unauthorized.current?.();
    });
    return () => { active = false; controller.abort(); };
  }, [requestKey, reload]);

  function update(next) {
    const normalized = normalizeFeedbackState({ ...applied, ...next });
    if (onStateChange) onStateChange(normalized); else setLocalState(normalized);
  }
  function apply(next = draft) {
    const issue = feedbackDateIssue(next);
    if (issue) { setFormError(issue); startRef.current?.focus(); return; }
    setFormError(""); update({ ...next, query: next.query.trim(), offset: 0, selectedId: null });
  }
  function patchDraft(key, value) {
    setDraft(current => ({ ...current, [key]: value, ...(key === "purpose" && value === "test" ? { version: "" } : {}), ...(key === "version" && value ? { purpose: "production" } : {}) }));
    setFormError("");
  }
  const summary = current.summary, versions = current.versions?.items || [];
  const versionOptions = versions.filter(item => item.id !== unrecordedFeedbackVersion);
  const list = current.list;
  const number = value => feedbackCount(value) === null ? "—" : value.toLocaleString(locale === "en" ? "en-US" : "ko-KR");
  const count = value => `${number(value)}${locale === "en" ? "" : "건"}`;
  const issue = formError || feedbackDateIssue(applied);
  const issueText = issue === "date_order" ? w("시작일은 종료일보다 늦을 수 없습니다. 조회 조건이 적용되지 않았습니다.", "The start date must be on or before the end date. These filters have not been applied.") : w("시작일과 종료일을 올바르게 입력해 주세요. 조회 조건이 적용되지 않았습니다.", "Enter valid start and end dates. These filters have not been applied.");
  const errorText = Object.values(current.errors).includes("forbidden") ? w("관리자 권한으로만 피드백을 조회할 수 있습니다.", "Administrator access is required to view feedback.") : w("일부 피드백 정보를 불러오지 못했습니다. 다시 조회해 주세요.", "Some feedback information could not be loaded. Please retry.");

  return <div className="page-stack feedback-workspace">
    <section className="panel feedback-filter-panel" aria-label={w("피드백 조회 조건", "Feedback filters")}><form className="feedback-filters" onSubmit={event => { event.preventDefault(); apply(); }}>
      <label>{w("등록 시작일", "Registration start date")}<input ref={startRef} type="date" required value={draft.start} aria-invalid={Boolean(issue)} aria-describedby={issue ? "feedback-filter-error feedback-period-note" : "feedback-period-note"} onChange={event => patchDraft("start", event.target.value)} /></label>
      <label>{w("등록 종료일", "Registration end date")}<input type="date" required value={draft.end} aria-invalid={Boolean(issue)} aria-describedby={issue ? "feedback-filter-error feedback-period-note" : "feedback-period-note"} onChange={event => patchDraft("end", event.target.value)} /></label>
      <label>{w("분석 용도", "Analysis purpose")}<select value={draft.purpose} onChange={event => patchDraft("purpose", event.target.value)}><option value="production">Production</option><option value="test">Test</option><option value="all">{w("모든 용도", "All purposes")}</option></select></label>
      <label>{w("운영 버전", "Production version")}<select value={draft.version} disabled={draft.purpose === "test"} aria-describedby="feedback-version-note" onChange={event => patchDraft("version", event.target.value)}>
        <option value="">{draft.purpose === "test" ? w("Test / 적용 안 함", "Test / not applicable") : w("모든 운영 버전", "All Production versions")}</option>
        {draft.version && draft.version !== unrecordedFeedbackVersion && !versionOptions.some(item => item.id === draft.version) && <option value={draft.version}>{w("선택한 버전 / 현재 조건에 없음", "Selected version / outside current scope")}</option>}
        {versionOptions.map(item => <option key={item.id} value={item.id}>{feedbackVersionLabel(item, locale)}</option>)}<option value={unrecordedFeedbackVersion}>{w("버전 미기록", "Version not recorded")}</option>
      </select></label>
      <label className="feedback-query">{w("검색", "Search")}<input type="search" maxLength={200} value={draft.query} onChange={event => patchDraft("query", event.target.value)} placeholder={w("회사, 탐지명, 작성자 또는 이벤트 ID", "Company, signature, author or event ID")} aria-describedby="feedback-search-note" /></label>
      <label>{w("피드백 유형", "Feedback category")}<select value={draft.category} onChange={event => patchDraft("category", event.target.value)}><option value="">{w("모든 유형", "All categories")}</option>{feedbackCategories.map(([id]) => <option key={id} value={id}>{feedbackCategoryLabel(id, locale)}</option>)}</select></label>
      <div className="feedback-filter-actions"><button type="submit" className="primary">{w("조회", "Apply")}</button><button type="button" className="secondary" onClick={() => { const next = initialFeedbackState(); setDraft(next); setFormError(""); update(next); }}>{w("초기화", "Reset")}</button></div>
      <div className="feedback-filter-bottom"><div><p id="feedback-period-note">{w("피드백 등록일 기준, 한국 시간(KST). 시작일과 종료일을 모두 포함합니다.", "Based on feedback registration dates in Korea Standard Time (KST), including both dates.")}</p><p id="feedback-version-note">{w("운영 버전은 접수 당시 연결된 운영 구성입니다. 실제 실행 설정은 원 분석에서 확인하며 Test에는 적용하지 않습니다.", "Versions identify the Production configuration at admission. See the original analysis for actual execution settings. Versions do not apply to Test.")}</p><p id="feedback-search-note">{w("검색은 이벤트와 작성자 정보에 적용합니다. 의견 본문은 검색하지 않습니다.", "Search covers event and author metadata. Comment text is excluded.")}</p></div><div className="feedback-quick-range"><span>{w("최근", "Last")}</span>{[7, 30].map(days => <button type="button" className="secondary" key={days} onClick={() => apply({ ...draft, ...recentFeedbackRange(days) })}>{days}{w("일", " days")}</button>)}</div></div>
      {issue && <p className="error feedback-filter-error" id="feedback-filter-error" role="alert">{issueText}</p>}
    </form></section>
    {applied.analysis && <div className="feedback-analysis-scope"><span>{w("선택한 분석의 피드백만 표시 중입니다.", "Showing feedback for the selected analysis.")}</span><button type="button" className="text-button" onClick={() => update({ analysis: "", offset: 0, selectedId: null })}>{w("전체 분석으로 확대", "Show all analyses")}<Icon name="arrow" size={14} /></button></div>}
    {Object.keys(current.errors).length > 0 && <div className="feedback-read-error" role="alert"><p>{errorText}</p><button type="button" className="secondary" onClick={() => setReload(value => value + 1)}>{w("다시 조회", "Retry")}</button></div>}
    {current.loading && <p className="feedback-loading" role="status">{w("피드백을 불러오는 중입니다…", "Loading feedback…")}</p>}
    <section className="feedback-metrics" aria-label={w("피드백 집계", "Feedback summary")} aria-busy={current.loading}>
      {[
        ["registration_count", w("피드백", "Feedback registrations"), w("조회 조건에 맞는 등록 이력", "Registration history matching these filters"), "analyses", "accent"],
        ["analysis_count", w("피드백이 달린 분석", "Analyses with feedback"), w("같은 분석의 여러 의견은 한 번 집계", "Each analysis is counted once"), "layers", "blue"],
        ["multi_category_count", w("복수 유형 피드백", "Multiple categories"), w("두 가지 이상 유형을 선택한 이력", "Registrations with two or more categories"), "chart", "amber"],
      ].map(([key, title, note, icon, tone]) => <article className={`panel feedback-metric feedback-metric-${tone}`} key={key}><div><span>{title}</span><i><Icon name={icon} size={18} /></i></div><strong>{number(summary?.[key])}{locale !== "en" && <small>건</small>}</strong><p>{note}</p></article>)}
    </section>
    <div className="feedback-charts">
      <section className="panel feedback-category-panel" aria-busy={current.loading}><header className="panel-head"><div><h2>{w("유형별 피드백", "Feedback by category")}</h2><p>{w("유형을 누르면 해당 피드백만 확인합니다.", "Select a category to filter the feedback history.")}</p></div><span className="feedback-category-badge">{w("복수 선택", "Multiple selections")}</span></header><div className="feedback-panel-body">
        {current.errors.summary && <p className="feedback-section-empty">{w("유형별 집계를 불러오지 못했습니다.", "Could not load category counts.")}</p>}
        {!current.errors.summary && <div className="feedback-category-chart">{feedbackCategories.map(([id], index) => {
          const value = summary?.category_counts.find(row => row.category === id)?.count;
          const percent = summary?.registration_count ? value / summary.registration_count * 100 : null;
          return <button type="button" className={`feedback-category-bar${applied.category === id ? " active" : ""}`} key={id} disabled={!summary} aria-pressed={applied.category === id} onClick={() => update({ category: applied.category === id ? "" : id, offset: 0, selectedId: null })}>
            <span className="feedback-category-name"><i className={`feedback-category-dot feedback-tone-${index}`} />{feedbackCategoryLabel(id, locale)}</span><span className="feedback-category-track" aria-hidden="true"><span className={`feedback-tone-${index}`} style={{ width: `${percent === null ? 0 : Math.min(100, percent)}%` }} /></span><span className="feedback-category-value"><strong>{count(value)}</strong><small>{percent === null ? "—" : `${percent.toFixed(1)}%`}</small></span>
          </button>;
        })}</div>}
        <p className="feedback-count-note">{w(`비율의 분모는 조회된 피드백 ${count(summary?.registration_count)}입니다. 여러 유형을 선택할 수 있어 합계는 100%를 넘을 수 있습니다.`, `Percentages use ${number(summary?.registration_count)} feedback registrations as the denominator. Multiple selections can make the total exceed 100%.`)}{Boolean(summary?.comment_only_count) && <> {w(`유형 없이 의견만 등록한 피드백 ${count(summary.comment_only_count)}은 막대에 포함하지 않습니다.`, `${number(summary.comment_only_count)} comment-only registrations are excluded from the bars.`)}</>}</p>
      </div></section>
      <section className="panel feedback-version-panel" aria-busy={current.loading}><header className="panel-head"><div><h2>{w("운영 버전별", "By Production version")}</h2><p>{w("접수 당시 연결된 운영 구성 기준", "Based on the Production configuration at admission")}</p></div><Icon name="layers" size={18} /></header><div className="feedback-panel-body"><div className="feedback-version-summary">
        {versions.map(item => <button type="button" className={`feedback-version-item${applied.version === item.id ? " active" : ""}`} key={item.id} aria-pressed={applied.version === item.id} onClick={() => update({ purpose: "production", version: applied.version === item.id ? "" : item.id, offset: 0, selectedId: null })}><span><strong>{feedbackVersionLabel(item, locale)}</strong><small>{w(`대상 분석 ${count(item.analysis_count)}`, `${number(item.analysis_count)} analyses`)}</small></span><span className="feedback-version-count">{number(item.count)}{locale !== "en" && <small>건</small>}<Icon name="arrow" size={14} /></span></button>)}
        {!versions.length && <p className="feedback-section-empty">{current.loading ? w("집계를 불러오는 중입니다…", "Loading version counts…") : current.errors.versions ? w("운영 버전 집계를 불러오지 못했습니다.", "Could not load Production version counts.") : issue ? w("조회 기간을 확인해 주세요.", "Check the registration date range.") : applied.purpose === "test" ? w("Test에는 운영 버전을 적용하지 않습니다.", "Production versions do not apply to Test.") : w("조회 조건에 해당하는 운영 피드백이 없습니다.", "No Production feedback matches these filters.")}</p>}
      </div><p className="feedback-count-note">{w("Test는 제외하며 운영 버전 선택 전의 집계입니다. 피드백 건수만으로 버전의 정확도나 오류율을 판단하지 않습니다.", "Test is excluded. These counts are shown before filtering by version. Feedback counts do not measure version accuracy or error rates.")}</p></div></section>
    </div>
    <section className="panel feedback-history-panel" aria-busy={current.loading}><header className="panel-head"><div><h2>{w("피드백 이력", "Feedback history")}</h2><p>{w("위 집계와 같은 조회 조건의 등록 이력입니다. 기존 이력은 덮어쓰지 않습니다.", "Registrations matching the summary filters. Existing history is never overwritten.")}</p></div><div className="feedback-history-actions"><span>{count(list?.total)}</span><button type="button" className="icon-button" disabled={current.loading} aria-label={w("피드백 새로고침", "Refresh feedback")} onClick={() => setReload(value => value + 1)}><Icon name="refresh" size={16} /></button></div></header>
      <DataTable label={w("분석 피드백 이력", "Analysis feedback history")} data={list?.items || []} className="feedback-history-table" onRowClick={item => update({ selectedId: item.id })} empty={current.loading ? w("피드백을 불러오는 중입니다…", "Loading feedback…") : current.errors.list ? w("피드백 이력을 불러오지 못했습니다.", "Could not load feedback history.") : issue ? w("조회 기간을 확인해 주세요.", "Check the registration date range.") : w("조회 조건에 맞는 피드백이 없습니다.", "No feedback matches these filters.")} columns={[
        { id: "created_at", header: w("등록 시각 (KST)", "Registered (KST)"), width: 164, render: item => <time dateTime={item.created_at}>{feedbackDateTime(item.created_at, locale)}</time> },
        { id: "production_configuration_id", header: w("운영 버전", "Production version"), width: 170, render: item => <span className={`feedback-version-badge${item.analysis_purpose === "production" && item.production_configuration_id ? " recorded" : ""}`}>{feedbackVersionLabel(item, locale)}</span> },
        { id: "signature", header: w("분석 대상", "Analysis target"), width: 225, render: item => <div className="feedback-table-target"><strong>{text(item.signature) || w("탐지명 미기록", "Signature not recorded")}</strong><span>{text(item.company_name) || w("회사 미기록", "Company not recorded")}</span></div> },
        { id: "feedback_categories", header: w("피드백 유형", "Categories"), width: 250, render: item => <FeedbackCategories item={item} locale={locale} /> },
        { id: "comment", header: w("의견", "Comment"), width: 265, render: item => <span className="feedback-table-comment">{text(item.comment) || <span className="muted">{w("추가 의견 없음", "No additional comment")}</span>}</span> },
        { id: "analyst_id", header: w("작성자", "Author"), width: 120, render: item => <span className="feedback-table-author">{text(item.analyst_id) || w("미기록", "Not recorded")}</span> },
        { id: "detail", header: w("상세", "Details"), width: 70, render: item => <button type="button" className="icon-button" aria-label={w("피드백 상세 보기", "View feedback details")} onClick={() => update({ selectedId: item.id })}><Icon name="arrow" size={16} /></button> },
      ]} />
      {list && <Pagination total={list.total} limit={applied.limit} offset={applied.offset} pageSizes={[20, 50, 100]} disabled={current.loading} label={w("피드백 이력 페이지", "Feedback history pages")} onOffsetChange={offset => update({ offset, selectedId: null })} onLimitChange={limit => update({ limit, offset: 0, selectedId: null })} />}
    </section>
    <p className="feedback-access-note"><Icon name="shield" size={15} />{w("관리자 전용 조회 화면이며 열람 기록이 남습니다. 피드백은 모델 입력과 학습 데이터로 사용하지 않습니다.", "Administrator access is required and reads are audited. Feedback is not used as model input or training data.")}</p>
    <FeedbackDetail id={applied.selectedId} onClose={() => update({ selectedId: null })} onOpen={onOpen} onUnauthorized={onUnauthorized} locale={locale} />
  </div>;
}
