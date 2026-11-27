import { useEffect, useId, useMemo, useRef, useState } from "react";
import { useR5Words, verdictText } from "./R5Evaluation.jsx";
import { useConsolePreferences } from "./consolePreferences.jsx";
import { analysisNotices, analystFieldLabel, analystGuidance, analystItems, analystSummary, analystText, finalValue, hasTuningContent } from "./analystView.js";
import { assessmentView } from "./analystAssessment.js";
import { decisionExplanation, provisionalAnalysisNotice } from "./decisionExplanation.js";
import { isResultV3 } from "./assessmentData.js";
import { decisionConditions, detailEvidence } from "./decisionDetail.js";
import { evidenceRelationView } from "./evidenceRelations.js";
import { evidenceDisplayView, isEvidenceDisplayView } from "./evidenceDisplayPlan.js";
import { analysisEvidenceMeta, isCoreEvidence, receivedEvidence } from "./analysisEvidenceMeta.js";
import { evidenceSourceReferences, sharedSourceText } from "./evidenceSourceSharing.js";
import { disagreementNotice, evidenceSections, legacySignatureNotice, severityMeaning, signatureSummary, verifierDisagreed, wafObservation } from "./decisionSemantics.js";
import { formatDate, formatDuration } from "./analysisView.js";
import { isMockAnalysis } from "./labelEvaluation.js";
import { CompactEvaluationDetail } from "./ReferenceLabels.jsx";
import AnalysisSelectionActions from "./AnalysisSelection.jsx";
import NavigationAction from "./NavigationAction.jsx";
import { Icon } from "./Icon.jsx";
import RawEventView from "./RawEventView.jsx";
import AgentHistory from "./AgentHistory.jsx";
import InferenceSummary from "./InferenceSummary.jsx";
import DataTable from "./DataTable.jsx";
import DecodingView from "./DecodingView.jsx";
import ExactSourceText, { revealInPane } from "./ExactSourceText.jsx";
import { inputFieldRows, resolveEvidenceInput, splitHighlightedText } from "./evidenceInputSource.js";
import useWorkspaceHeight from "./useWorkspaceHeight.js";
import "./analysisDecision.css";
import "./testAnalysisWorkspace.css";

function Direction({ value, label }) {
  const w = useR5Words();
  return <span className={`decision-direction direction-${value}`}>{label ? w(...label) : ({ attack_request: w("공격 요청 근거", "Attack request evidence"), normal_request: w("정상 요청 근거", "Normal request evidence"), context: w("참고 내용", "Context"), unclassified: w("구분 미기록", "Unclassified") })[value]}</span>;
}

function evidenceSelection(item, target, onViewInput, selectOnCard) {
  if (!selectOnCard) return {};
  const selected = target && item.field === target.field && item.excerpt === target.excerpt
    && (item.evidence_id && target.evidence_id ? item.evidence_id === target.evidence_id : item.number === target.number);
  return {
    tabIndex: 0, "data-evidence-selected": selected ? "true" : undefined,
    onClick: event => {
      if (!event.target.closest("button,a,input,select,textarea,summary,details") && !window.getSelection()?.toString()) onViewInput?.(item);
    },
    onKeyDown: event => {
      if (event.target === event.currentTarget && ["Enter", " "].includes(event.key)) { event.preventDefault(); onViewInput?.(item); }
    },
  };
}

export function DecisionHero({ detail }) {
  const w = useR5Words();
  const finished = detail.status === "completed", verdict = finished ? finalValue(detail, "verdict") : detail.status;
  const severity = finished && detail.result?.threat_analysis?.severity;
  const label = finished ? verdictText(verdict, w) : ({ canceled: w("중지됨", "Stopped"), failed: w("실행 실패", "Execution failed"), pending: w("분석 대기", "Queued"), processing: w("분석 중", "Processing") })[detail.status] || w("상태 미확인", "Unknown status");
  const omitted = finalValue(detail, "input_truncated");
  return <section className={`decision-hero decision-hero-${verdict}`} aria-label={w("판정 요약", "Decision summary")}>
      <div className="decision-hero-kicker"><span className={`decision-purpose purpose-${detail.analysis_purpose || "legacy"}`}>{detail.analysis_purpose === "production" ? "Production" : detail.analysis_purpose === "test" ? "Test" : w("기존 미분류", "Legacy / unknown")}</span>{detail.company_name && <span>{detail.company_name}</span>}{isMockAnalysis(detail) && <span className="status">{w("모의 분석", "Simulated analysis")}</span>}</div>
      <div className="decision-verdict-line"><strong className={`decision-verdict verdict-${verdict}`}>{label}</strong>{severity && <span className={`severity severity-${String(severity).toLowerCase()}`} title={w(...severityMeaning)}>{severity === "NONE" ? w("해당 없음", "Not applicable") : severity === "UNKNOWN" ? w("미확정", "Undetermined") : severity}</span>}</div>
      <h1>{detail.event_name || detail.signature || w("분석 상세", "Analysis detail")}</h1>
      <p className="decision-hero-summary">{detail.status === "canceled" ? w("테스트가 중지되어 이 문항의 판정을 완료하지 않았습니다. 실패나 오답으로 집계하지 않습니다.", "This case stopped before a final decision. It is not counted as a failure or an incorrect verdict.") : analystSummary(detail)}</p>
      <dl className="decision-hero-timing">{[
        [w("접수부터 판정까지", "Admission to decision"), detail.total_elapsed_ms], [w("초기 대기", "Initial queue wait"), detail.queue_wait_ms], [w("분석 처리", "Analysis processing"), detail.processing_duration_ms],
      ].map(([key, value]) => <div key={key}><dt>{key}</dt><dd>{formatDuration(value, w("미측정", "Not measured"))}</dd></div>)}</dl>
      <details className="decision-hero-observations"><summary>{w("이벤트 정보와 시간 기준", "Event details and timing boundaries")}</summary><dl className="decision-hero-facts">{[
          [w("WAF 조치", "WAF action"), detail.waf_action === "D" ? w("차단", "Blocked") : detail.waf_action === "A" ? w("허용", "Allowed") : w("미기록", "Not recorded")],
          [w("입력 생략", "Input omitted"), omitted === true ? w("있음", "Yes") : omitted === false ? w("없음", "No") : w("미기록", "Not recorded")],
          [w("WAF 탐지", "WAF detection"), detail.signature || w("미기록", "Not recorded")],
          [w("접수 시각", "Received at"), formatDate(detail.created_at)],
          [w("출발지 IP", "Source IP"), detail.src_ip || w("미기록", "Not recorded")],
          [w("목적지 IP", "Destination IP"), detail.dest_ip || w("미기록", "Not recorded")],
        ].map(([key, value]) => <div key={key}><dt>{key}</dt><dd>{value}</dd></div>)}</dl>
      <p className="decision-timing-boundary muted">{w("접수부터 판정까지는 초기 대기를 포함합니다. 분석 처리는 초기 대기를 제외하며 내부 재시도와 복구 시간을 포함합니다.", "Admission to decision includes the initial queue wait. Analysis processing excludes that wait and includes internal retries and recovery.")}</p>
      {severity && <p className="decision-severity-note muted">{w(...severityMeaning)}</p>}</details>
      {analysisNotices(detail).map(notice => <p className="decision-hero-notice" key={notice}>{notice}</p>)}
    </section>;
}

function EvidenceOrigin({ roles = [], field }) {
  const w = useR5Words();
  if (!roles.length && !field) return null;
  return <details className="decision-evidence-origin"><summary>{w("작성 출처", "Authorship")}</summary>{!!roles.length && <p>{roles.map(role => role === "primary" ? w("1차 분석", "Primary analysis") : w("추가 검증", "Additional verification")).join(" / ")}</p>}{field && <p><code>{field}</code></p>}</details>;
}

function DisplayStatus({ display, view, loading, stopped, error, onRetry }) {
  const w = useR5Words(), state = display?.status, metrics = view.plan.metrics;
  const failed = view.invalid || state === "fallback" || view.plan.status === "fallback";
  let label = failed ? w("정리 실패 — 원본 유지", "Organization unavailable — originals retained")
    : error ? w("정리 상태를 확인하지 못했습니다", "Could not check organization status")
    : ["pending", "running"].includes(state) ? w("근거 정리 중", "Organizing evidence")
      : metrics.merged_count ? w(`정리됨 — 근거 ${metrics.original_evidence_count}개를 ${metrics.group_count}묶음`, `Organized — ${metrics.original_evidence_count} evidence items in ${metrics.group_count} groups`)
        : state === "completed" ? w("검토 완료 — 합친 근거 없음", "Review complete — no evidence combined")
          : state === "skipped" ? w("중복 없음", "No duplicates") : state === "disabled" ? w("원본 근거", "Original evidence") : w("근거 확인", "Evidence overview");
  return <div className={`decision-display-status${failed || error ? " is-fallback" : ""}`} role="status"><span>{label}</span>
    {["pending", "running"].includes(state) && <small>{w("분석은 완료되었습니다. 정리 중에도 원문과 판정은 확인할 수 있습니다.", "Analysis is complete. Evidence organization does not change the decision.")}</small>}
    {stopped && <small>{w("자동 확인을 멈췄습니다. 상태 확인은 분석을 다시 실행하지 않습니다.", "Automatic refresh paused. Checking status does not rerun analysis.")}</small>}
    {(failed || error || stopped) && onRetry && <button type="button" className="text-button" disabled={loading} onClick={onRetry}>{w("상태 다시 확인", "Check status")}</button>}
  </div>;
}

function CoreEvidenceStatus({ detail }) {
  const w = useR5Words(), meta = analysisEvidenceMeta(detail) || analysisEvidenceMeta(detail.result);
  const count = Array.isArray(detail.result?.evidence) ? detail.result.evidence.length : 0;
  return <div className={`decision-display-status${meta?.status === "fallback" ? " is-fallback" : ""}`} role="status"><span>{meta?.status === "pending"
    ? w("핵심 근거 준비 중", "Preparing core evidence") : meta?.status === "fallback"
      ? w(`정리 미적용 — 전달된 근거 ${count}개`, `Organization unavailable — ${count} received evidence items`)
      : w(`핵심 근거 ${count}개`, `${count} core evidence items`)}</span></div>;
}

function CoreEvidenceCards({ detail, onViewInput, onExecution, selectedTarget, selectOnCard }) {
  const w = useR5Words(), items = receivedEvidence(detail.result), id = useId(), excerpts = useRef(new Map());
  function jumpToSource(index) {
    const target = excerpts.current.get(index);
    target?.scrollIntoView({ block: "nearest", behavior: "auto" });
    target?.focus({ preventScroll: true });
  }
  return <section className="panel decision-evidence-panel decision-core-evidence-panel"><div className="panel-head-inline"><h2>{w("보안 분석 근거", "Security analysis evidence")}</h2>{onViewInput && <NavigationAction onClick={() => onViewInput(null)}>{w("근거와 원문", "Evidence & input")}</NavigationAction>}</div>
    <CoreEvidenceStatus detail={detail} />
    <div className="decision-display-groups">{evidenceSourceReferences(items).map(({ item, index, sourceIndex }) => <article className={`decision-evidence-row decision-core-evidence evidence-${item.supports}`} key={`${item.evidence_id}:${index}`} {...evidenceSelection(item, selectedTarget, onViewInput, selectOnCard)}>
      <div className="decision-evidence-label"><strong>{w("근거", "Evidence")} {item.number}</strong><Direction value={item.supports} />
        <span>{analystFieldLabel(item.field)}</span><EvidenceOrigin roles={item.roles} field={item.field} />
      </div><div className="decision-evidence-content"><p className="decision-linked-interpretation">{item.interpretation_ko}</p>{sourceIndex === index
        ? <code dir="ltr" id={`${id}-source-${index}`} tabIndex={0} aria-label={w(`근거 ${item.number} 원문 발췌`, `Evidence ${item.number} source excerpt`)} ref={node => { if (node) excerpts.current.set(index, node); else excerpts.current.delete(index); }}>{item.excerpt}</code>
        : <button type="button" className="text-button decision-shared-source" aria-controls={`${id}-source-${sourceIndex}`} onClick={() => jumpToSource(sourceIndex)}>{w(`같은 원문 보기 (근거 ${items[sourceIndex].number})`, `View the source shared with evidence ${items[sourceIndex].number}`)}</button>}
        {onViewInput && <div className="decision-source-actions"><button type="button" className="text-button evidence-input-link" aria-label={w(`근거 ${item.number} 원문 보기`, `View evidence ${item.number} source`)} onClick={() => onViewInput(item)}>{w("원문에서 보기", "View source")} →</button></div>}
      </div>
    </article>)}</div>
    {!items.length && <p className="muted">{w("전달된 근거가 없습니다. 이것만으로 정상 요청이라고 볼 수는 없습니다.", "No evidence was returned. This alone does not establish that the request is benign.")}</p>}
    <p className="decision-evidence-footnote">{w("API가 전달한 근거를 같은 순서로 표시합니다. 공격 요청 근거는 공격 성공을 뜻하지 않습니다.", "Evidence is shown in the order returned by the API. Attack request evidence does not establish attack success.")}</p>
    {onExecution && <button type="button" className="text-button" onClick={onExecution}>{w("전체 원본 근거는 실행 이력에서 확인", "View all original evidence in the execution trace")} →</button>}
  </section>;
}

export function EvidenceDisplaySummary({ detail, loading = false, stopped = false, error = false, onRetry }) {
  const w = useR5Words(), display = detail.evidence_display;
  const meta = analysisEvidenceMeta(detail) || analysisEvidenceMeta(detail.result);
  if (meta?.view === "core-v1" || meta?.view === "core-v2") return <section className="panel decision-display-execution"><h2>{w("핵심 근거 전달", "Core evidence delivery")}</h2><CoreEvidenceStatus detail={detail} /><p className="muted">{w("화면과 API는 같은 근거를 사용합니다. 아래 실행 이력에는 분석 당시의 전체 기록이 보존됩니다.", "The screen and API use the same evidence. The execution trace below preserves the complete original analysis record.")}</p></section>;
  const view = detail.status === "completed" && isEvidenceDisplayView(display)
    ? evidenceDisplayView(detail.result, display) : null;
  if (!view) return null;
  return <section className="panel decision-display-execution"><h2>{w("현재 근거 정리", "Current evidence organization")}</h2>
    <DisplayStatus {...{ display, view, loading, stopped, error, onRetry }} />
    {display.status !== "disabled" && <p className="muted">{w("근거 정리는 분석 종료 후 별도로 처리합니다. 아래 단계는 분석 당시 기록이므로 근거 정리 단계가 건너뜀으로 남아 있어도 현재 상태와 다를 수 있습니다.", "Evidence organization runs separately after analysis. The trace below records the original analysis; a skipped editor step does not describe the current organization status.")}</p>}
  </section>;
}

function CompactEvidenceCards({ detail, onViewInput, relations, relationsError, relationsLoading, displayPollingStopped, onRetryRelations, selectedTarget, selectOnCard }) {
  const w = useR5Words(), id = useId(), [additionalOpen, setAdditionalOpen] = useState(false), [sourcesOpen, setSourcesOpen] = useState(new Set());
  const display = detail.evidence_display, view = evidenceDisplayView(detail.result, display, relations ?? display?.relations ?? null);
  useEffect(() => { setAdditionalOpen(false); setSourcesOpen(new Set()); }, [detail.id, view?.plan.source_hash]);
  if (!view) return null;
  const toggleSources = key => setSourcesOpen(previous => { const next = new Set(previous); if (next.has(key)) next.delete(key); else next.add(key); return next; });
  const rows = groups => <div className="decision-display-groups">{groups.map(item => {
    const extraSources = [...item.member_sources.filter(source => source.evidence_id !== item.representative_id), ...item.related_sources];
    const opened = sourcesOpen.has(item.group_id), sourceId = `${id}-${item.group_id}-sources`;
    return <article className={`decision-evidence-row decision-display-group evidence-${item.supports}${item.relation ? " has-linked-sources" : ""}`} key={item.group_id} {...evidenceSelection(item, selectedTarget, onViewInput, selectOnCard)}>
      <div className="decision-evidence-label"><strong>{w("근거", "Evidence")} {item.number}</strong><Direction value={item.supports} />
        <EvidenceOrigin roles={item.roles} field={item.field} />
        <span>{analystFieldLabel(item.field)}</span>{item.visibility_reasons.includes("counter_evidence") && <small className="decision-protected-reason">{w("반대 근거", "Counter-evidence")}</small>}
        {item.visibility_reasons.includes("decision_issue") && <small className="decision-protected-reason">{w("확인 조건에 연결됨", "Linked to a decision condition")}</small>}
      </div>
      <div className="decision-evidence-content"><p className="decision-linked-interpretation">{analystText(item.interpretation_ko)}</p><code dir="ltr">{item.excerpt}</code>
        <div className="decision-source-actions">{onViewInput && <button type="button" className="text-button evidence-input-link" aria-label={w(`근거 ${item.number} 원문 보기`, `View evidence ${item.number} source`)} onClick={() => onViewInput(item)}>{w("원문에서 보기", "View source")} →</button>}
          {!!extraSources.length && <button type="button" className="text-button" aria-expanded={opened} aria-controls={sourceId} onClick={() => toggleSources(item.group_id)}>{opened ? w("함께 본 원문 접기", "Hide related sources") : w(`함께 본 원문 ${extraSources.length}개 보기`, `View ${extraSources.length} related sources`)}</button>}
        </div>
        {opened && <div id={sourceId} className="decision-linked-sources">{extraSources.map(source => <section className="decision-linked-source" key={source.evidence_id} aria-label={`${w("관련 근거", "Related evidence")} ${source.number}`}>
          <div className="decision-linked-source-heading"><strong>{w("근거", "Evidence")} {source.number}</strong><Direction value={source.supports} /><span>{analystFieldLabel(source.field)}</span></div><code dir="ltr">{source.excerpt}</code>
          {onViewInput && <button type="button" className="text-button evidence-input-link" onClick={() => onViewInput(source)}>{w("원문과 개별 해석 보기", "View source and interpretation")} →</button>}
        </section>)}</div>}
      </div>
    </article>;
  })}</div>;
  return <section className="panel decision-evidence-panel decision-display-panel"><div className="panel-head-inline"><h2>{w("보안 분석 근거", "Security analysis evidence")}</h2>{onViewInput && <NavigationAction onClick={() => onViewInput(null)}>{w("전체 근거·원문", "All evidence & input")}</NavigationAction>}</div>
    <DisplayStatus display={display} view={view} loading={relationsLoading} stopped={displayPollingStopped} error={relationsError} onRetry={onRetryRelations} />
    {!!view.core.length && rows(view.core)}
    {!!view.additional.length && <div className="decision-additional-evidence"><button type="button" className="secondary decision-additional-toggle" aria-expanded={additionalOpen} aria-controls={`${id}-additional`} onClick={() => setAdditionalOpen(value => !value)}>{additionalOpen ? w("나머지 근거 접기", "Hide additional evidence") : w(`나머지 근거 ${view.additional.length}묶음 보기`, `View ${view.additional.length} additional evidence groups`)}</button>{additionalOpen && <div id={`${id}-additional`}>{rows(view.additional)}</div>}</div>}
    {!view.groups.length && <p className="muted">{w("저장된 판정 근거가 없습니다. 이것만으로 공격이 없다고 볼 수는 없습니다.", "No decision evidence is recorded. This does not establish that the request is benign.")}</p>}
    {!!view.groups.length && <p className="decision-evidence-footnote">{w("공격 요청 근거는 공격 성공을 뜻하지 않습니다. 나머지 근거와 원문은 그대로 보존됩니다.", "Attack request evidence does not establish attack success. All evidence and original sources remain available.")}</p>}
  </section>;
}

export function EvidenceCards({ detail, onViewInput, onExecution, relations, relationsError = false, relationsLoading = false, displayPollingStopped = false, onRetryRelations, selectedTarget, selectOnCard = false }) {
  const w = useR5Words();
  if (isCoreEvidence(detail.result)) return <CoreEvidenceCards {...{ detail, onViewInput, onExecution, selectedTarget, selectOnCard }} />;
  if (isResultV3(detail.result)) return <CompactEvidenceCards {...{ detail, onViewInput, relations, relationsError, relationsLoading, displayPollingStopped, onRetryRelations, selectedTarget, selectOnCard }} />;
  const relationView = evidenceRelationView(detail.result, relations), items = relationView.rows || relationView.items;
  const classified = Boolean(assessmentView(detail.result));
  const groups = evidenceSections(finalValue(detail, "verdict")).map(group => ({ ...group, items: items.filter(item => item.supports === group.side) })).filter(group => group.items.length);
  const rows = group => <div className="decision-evidence-rows">{group.items.map(item => <article className={`decision-evidence-row${item.relation ? " has-linked-sources" : ""}`} key={`${item.number}:${item.roles?.join(",") || "all"}`} {...evidenceSelection(item, selectedTarget, onViewInput, selectOnCard)}>
    <div className="decision-evidence-label"><strong>{w("근거", "Evidence")} {item.number}</strong><span>{analystFieldLabel(item.field)}</span><EvidenceOrigin roles={item.roles} field={item.field} /></div>
    <div className="decision-evidence-content"><p className="decision-linked-interpretation">{analystText(item.interpretation_ko)}</p><span className="sr-only">{w("원문 발췌", "Source excerpt")}</span><code dir="ltr">{item.excerpt}</code>{item.relation && onViewInput && <button type="button" className="text-button decision-representative-source" onClick={() => onViewInput(item)}>{w(`근거 ${item.number} 원문 보기`, `View evidence ${item.number} source`)} →</button>}{!!item.related_numbers.length && <small className="muted">{w("같은 로그 발췌", "Same excerpt")}: {item.related_numbers.join(", ")}</small>}
      {!!item.related_sources?.length && <div className="decision-linked-sources"><h4>{w("함께 본 원문", "Related sources")}</h4>{item.related_sources.map(source => <section className="decision-linked-source" key={source.evidence_id} aria-label={`${w("관련 근거", "Related evidence")} ${source.number}`}><div className="decision-linked-source-heading"><strong>{w("근거", "Evidence")} {source.number}</strong><Direction value={source.supports} /><span>{analystFieldLabel(source.field)}</span></div><code dir="ltr">{source.excerpt}</code>{onViewInput && <button type="button" className="text-button evidence-input-link" onClick={() => onViewInput(source)}>{w("원문과 개별 해석 보기", "View source and interpretation")} →</button>}</section>)}</div>}
    </div>
    {!item.relation && onViewInput && item.field && item.excerpt && <button type="button" className="text-button evidence-input-link" onClick={() => onViewInput(item)}>{w("원문에서 보기", "View source")} →</button>}
  </article>)}</div>;
  return <section className="panel decision-evidence-panel"><div className="panel-head-inline"><h2>{isResultV3(detail.result) ? w("보안 분석 근거", "Security analysis evidence") : w("판정 근거", "Decision evidence")}</h2>{onViewInput && <NavigationAction onClick={() => onViewInput(null)}>{w("전체 근거·원문", "All evidence & input")}</NavigationAction>}</div>
    {!classified && !!items.length && <p className="muted">{w("기존 분석에는 공격·정상 요청 근거 구분이 기록되지 않았습니다.", "This older analysis has no recorded evidence directions.")}</p>}
    {classified && <p className="muted">{w("공격 요청 근거는 공격 성공을 뜻하지 않으며, 근거 개수로 최종 판정을 결정하지 않습니다.", "Attack request evidence does not establish attack success. Evidence counts do not determine the final decision.")}</p>}
    {relationsLoading && <p className="muted" role="status">{w("관련 원문을 확인하는 중…", "Loading source links…")}</p>}
    {(relationsError || relationView.invalid) && <div className="decision-relations-fallback" role="status"><p>{w("근거 연결 정보를 불러오지 못해 개별 근거를 표시합니다.", "Source links are unavailable. Individual evidence is shown.")}</p>{onRetryRelations && <button type="button" className="secondary" disabled={relationsLoading} onClick={onRetryRelations}>{w("다시 시도", "Retry")}</button>}</div>}
    {groups.map(group => group.side === "context" ? <details className="decision-context-evidence" key={group.side}><summary><Direction value={group.side} label={group.label} /> <span>{group.items.length}</span></summary>{rows(group)}</details> : <section className={`decision-evidence-group evidence-${group.side}`} key={group.side}><h3><Direction value={group.side} label={group.label} /></h3>{rows(group)}</section>)}
    {!items.length && <p className="muted">{w("저장된 판정 근거가 없습니다. 이것만으로 공격이 없다고 볼 수는 없습니다.", "No decision evidence is recorded. This does not establish that the request is benign.")}</p>}
  </section>;
}

export function DecisionConditions({ detail, onViewInput, preserveEvidence = false }) {
  const w = useR5Words(), conditions = decisionConditions(detail, { preserveEvidence, display: detail.evidence_display }), evidence = detailEvidence(detail.result, { preserveEvidence, display: detail.evidence_display });
  if (!conditions.visible) return null;
  return <section className="panel decision-conditions">
    {conditions.sections.map((section, index) => <div key={section.key} className="decision-check-group" data-purpose={section.key}><h2>{w(...section.title)}</h2>
      {section.introduction && <p className="muted">{w(section.introduction, section.introductionEn || section.introduction)}</p>}
      {!isResultV3(detail.result) && index === 0 && wafObservation(detail.waf_action) && <p className="muted">{w(...wafObservation(detail.waf_action))}</p>}
      {section.items.length ? <ol>{section.items.map((item, index) => <li key={index}><h3>{item.title}</h3>{item.notes.map(note => <p key={note}>{note}</p>)}{item.checks.map((check, i) => <div className="decision-check-source" key={i}><strong>{check.source_ko}</strong>{check.why_ko !== item.title && !item.notes.includes(check.why_ko) && <p>{check.why_ko}</p>}</div>)}{!!item.evidence_numbers.length && <div className="decision-related">{w("관련 근거", "Related evidence")} {item.evidence_numbers.map(number => { const source = evidence.find(row => row.number === number); return source && onViewInput ? <button type="button" className="text-button" key={number} onClick={() => onViewInput(source)}>{number}</button> : <span key={number}>{number}</span>; })}</div>}</li>)}</ol> : section.empty && <p className="muted">{section.empty}</p>}
    </div>)}
  </section>;
}

export function FinalDecisionReason({ detail, preserveEvidence = false }) {
  const w = useR5Words();
  if (!isResultV3(detail.result) || detail.status !== "completed" || isMockAnalysis(detail)) return null;
  const held = finalValue(detail, "verdict") === "inconclusive", explanation = held ? decisionExplanation(detail) : null;
  // Decisive results are explained by their linked evidence. Do not manufacture
  // a second generic rationale or generate another summary on the client.
  if (!explanation) return null;
  const issues = held && !isCoreEvidence(detail.result) ? (assessmentView(detail.result, { preserveEvidence, display: detail.evidence_display })?.issues || []).filter(issue => !issue.missing_condition_ko) : [];
  return <section className="panel decision-final-reason"><h2>{w("최종 판정 이유", "Final decision reasons")}</h2>
    <p>{explanation.reason_ko}</p>
    {!!issues.length && <ul>{issues.map((issue, index) => <li key={index}>{issue.point_ko}<small className="muted"> · {w("관련 근거", "Related evidence")} {issue.evidence_numbers.join(" · ")}</small></li>)}</ul>}
  </section>;
}

function ThreatFields({ threat, verdict }) {
  const w = useR5Words(), obfuscations = analystItems(threat?.obfuscations);
  if (!threat) return <p>{w("기술 해석이 기록되지 않았습니다.", "No technical interpretation recorded.")}</p>;
  return <dl className="decision-definitions">{[
    [verdict === "true_positive" ? w("공격 유형", "Attack type") : verdict === "false_positive" ? w("탐지 유형", "Detection type") : w("검토한 유형", "Type considered"), analystText(threat.category)],
    [w("분석 위치", "Location"), analystFieldLabel(analystText(threat.target))],
    [w("분석 내용", "Interpretation"), analystText(threat.technique_ko)],
    [w("예상 영향", "Potential impact"), analystText(threat.potential_impact_ko)],
    ...(obfuscations.length ? [[w("인코딩·난독화", "Encoding & obfuscation"), obfuscations.join(", ")]] : []),
  ].map(([key, value]) => <div key={key}><dt>{key}</dt><dd>{value}</dd></div>)}</dl>;
}

export function TechnicalInterpretation({ detail, onExecution, preserveEvidence = false }) {
  const w = useR5Words(), result = detail.result, threat = result?.threat_analysis, signature = result?.signature_assessment;
  const guidance = analystGuidance(detail), circumstances = isResultV3(result) ? [] : analystItems(result?.conflicting_evidence);
  const disagreed = verifierDisagreed(detail), verdict = finalValue(detail, "verdict");
  const issues = !isCoreEvidence(result) && ["true_positive", "false_positive"].includes(verdict) ? assessmentView(result, { preserveEvidence, display: detail.evidence_display })?.issues || [] : [];
  return <section className="panel decision-technical"><details><summary>{w("기술 해석 펼치기", "Technical interpretation")}</summary>
    {!disagreed && finalValue(detail, "verdict") === "inconclusive" && <p className="analyst-notice">{provisionalAnalysisNotice}</p>}
    {disagreed ? <div className="decision-technical-note"><p className="analyst-notice">{w(...disagreementNotice)}</p><p className="muted">{w("아래 역할별 기록은 최종 해석이 아닙니다. 양쪽 근거와 실행 상세를 함께 확인하세요.", "Role records below are not a final interpretation. Review both evidence sets and execution details.")}</p>
      {[["Primary", result?.primary], ["Verifier", result?.verifier?.output]].map(([role, output]) => <div key={role}><p><strong>{role}</strong> · {verdictText(output?.verdict, w)}</p>{output?.threat_analysis && <details><summary>{role} {w("해석 기록", "interpretation record")}</summary><ThreatFields threat={output.threat_analysis} verdict={output.verdict} /></details>}</div>)}
      {onExecution && <NavigationAction onClick={onExecution}>{w("실행 상세", "Execution details")}</NavigationAction>}
    </div> : <ThreatFields threat={threat} verdict={verdict} />}
    {!!issues.length && <div className="decision-technical-note"><h3>{w("판정 시 고려한 쟁점", "Issues considered in the decision")}</h3>{issues.map((issue, index) => <div key={index}><p>{issue.point_ko}</p>{issue.missing_condition_ko && <p>{issue.missing_condition_ko}</p>}<small>{w("관련 근거", "Related evidence")}: {issue.evidence_numbers.join(" · ")}</small></div>)}</div>}
    {signature && <div className="decision-technical-note"><h3>{w("탐지 내용과 요청의 연관성", "Signature and request")}{disagreed && ` · ${w("Primary 기록", "Primary record")}`}</h3><p>{({ exact: w("일치", "Exact"), partial: w("부분 일치", "Partial"), mismatch: w("불일치", "Mismatch"), unknown: w("확인 불가", "Unknown") })[signature.relation] || w("미기록", "Not recorded")}</p><p>{w(...signatureSummary(signature.relation))}</p>
      {signature.version === "signature-assessment-v2" ? <dl className="decision-definitions">{[
        [w("일치하는 부분", "Matching aspects"), analystItems(signature.matched_points)],
        [w("다른 부분", "Differences"), analystItems(signature.mismatched_points)],
        [w("비교의 제한", "Comparison limits"), analystItems([signature.uncertainty_ko])],
      ].filter(([, values]) => values.length).map(([label, values]) => <div key={label}><dt>{label}</dt><dd><ul>{values.map((text, index) => <li key={index}>{text}</li>)}</ul></dd></div>)}</dl>
      : analystText(signature.explanation_ko, "") && <details><summary>{w("기존 분석 설명", "Original analysis explanation")}</summary><p className="muted">{w(...legacySignatureNotice)}</p><p>{analystText(signature.explanation_ko)}</p></details>}</div>}
    {[ [w("함께 고려할 정황", "Other circumstances"), circumstances], [w("해석 시 주의할 점", "Interpretation limits"), guidance.limitations] ].filter(([, values]) => values.length).map(([title, values]) => <div className="decision-technical-note" key={title}><h3>{title}</h3><ul>{values.map((value, i) => <li key={i}>{value}</li>)}</ul></div>)}
  </details></section>;
}

export function PolicySuggestion({ detail }) {
  const w = useR5Words();
  if (isResultV3(detail.result)) return null;
  const tuning = detail.result?.tuning_recommendation;
  if (!hasTuningContent(tuning)) return null;
  return <section className="panel decision-policy"><details><summary>{w("정책 검토 제안", "Policy review suggestion")}{!tuning.recommended && <small>{w("주의사항", "Cautions")}</small>}</summary><dl className="decision-definitions">{[[w("범위", "Scope"), tuning.scope], [w("제안", "Suggestion"), tuning.proposal_ko], [w("변경 시 주의사항", "Risks"), tuning.risk_ko], [w("적용 전 확인", "Before applying"), tuning.validation_ko]].filter(([, value]) => analystText(value, "")).map(([key, value]) => <div key={key}><dt>{key}</dt><dd>{analystText(value)}</dd></div>)}</dl><p className="muted">{w("WAF 설정은 자동 변경하지 않습니다.", "WAF settings are never changed automatically.")}</p></details></section>;
}

export function DecisionContext({ detail, history, historyError, onSaved, onExecution, onFeedback }) {
  const w = useR5Words(), initial = detail.initial_assessment, result = detail.result;
  return <aside className="decision-sidebar">
    <section className="panel decision-context"><h2>{w("판정 맥락", "Decision context")}</h2>
      {initial && initial.comparison !== "unavailable" && <><dl className="decision-context-rows"><div><dt>{w("1차 판정", "Initial Assessment")}</dt><dd>{verdictText(initial.verdict, w)}{Number.isFinite(initial.probability) && <small>{w("수집기 점수", "Collector score")} {(initial.probability * 100).toFixed(1)}%</small>}</dd></div><div><dt>{w("심층 판정", "Deep Assessment")}</dt><dd>{detail.status === "completed" ? verdictText(finalValue(detail, "verdict"), w) : w("미완료", "Not completed")}</dd></div></dl><p className="decision-context-note">{w("1차 판정은 모델 입력이나 정답이 아닙니다. 두 판정의 점수는 같은 확률 척도가 아닙니다.", "The Initial Assessment is not a model input or Evaluation Dataset. Scores are not on a shared probability scale.")}</p></>}
      <div className="decision-reference"><h3>{w("참고 판정", "Reference Verdict")}</h3><CompactEvaluationDetail detail={detail} history={history} historyError={historyError} />{(!detail.evaluation || detail.evaluation.outcome === "unlabeled") && !historyError && <p className="muted">{w("등록된 참고 판정이 없습니다.", "No Reference Verdict is attached.")}</p>}<AnalysisSelectionActions key={detail.id} allowDataset={detail.analysis_purpose !== "test"} ids={[detail.id]} single compact onSaved={onSaved} /></div>
    </section>
    <section className="panel decision-process"><h2>{w("판정 과정 요약", "Decision process")}</h2><dl className="decision-context-rows"><div><dt>Primary</dt><dd>{verdictText(result?.primary?.verdict, w)}</dd></div><div><dt>Verifier</dt><dd>{result?.verifier?.executed === false ? w("실행하지 않음", "Not run") : result?.verifier?.error || result?.verifier?.failure_id ? w("실행 미완료", "Incomplete") : verdictText(result?.verifier?.output?.verdict, w)}</dd></div><div><dt>{w("최종 판정", "Final decision")}</dt><dd>{detail.status === "completed" ? verdictText(finalValue(detail, "verdict"), w) : w("미완료", "Not completed")}</dd></div></dl><NavigationAction onClick={onExecution}>{w("전체 실행 상세", "Execution details")}</NavigationAction></section>
    {onFeedback && <section className="panel decision-feedback"><h2>{w("분석 피드백", "Analysis feedback")}</h2><p className="muted">{w("이 분석에 API로 등록된 의견을 확인합니다.", "View feedback registered through the API for this analysis.")}</p><NavigationAction onClick={onFeedback}>{w("이 분석의 피드백", "Feedback for this analysis")}</NavigationAction></section>}
    {detail.status === "completed" && <PolicySuggestion detail={detail} />}
  </aside>;
}

export function EvidenceWorkspace({ detail, event, target, onSelect, error, onRetry, active, relations }) {
  const w = useR5Words(), sections = evidenceSections(finalValue(detail, "verdict")), core = isCoreEvidence(detail.result);
  const display = core ? null : evidenceDisplayView(detail.result, detail.evidence_display, relations ?? detail.evidence_display?.relations ?? null);
  const sourceItems = core ? receivedEvidence(detail.result) : display?.items || evidenceRelationView(detail.result, relations).items, pane = useRef(null);
  const items = core ? sourceItems : sections.flatMap(group => sourceItems.filter(item => item.supports === group.side));
  const selected = items.find(item => item.field === target?.field && item.excerpt === target?.excerpt
    && (target?.evidence_id ? item.evidence_id === target.evidence_id : target?.interpretation_ko ? item.interpretation_ko === target.interpretation_ko : target?.number == null || item.number === target.number));
  function select(item) { onSelect(item); if (window.matchMedia("(max-width: 800px)").matches) pane.current?.scrollIntoView({ block: "start", behavior: "auto" }); }
  return <section className="decision-evidence-workspace"><div className="panel decision-evidence-nav">
    <h2>{w("근거 선택", "Choose evidence")}</h2><p className="muted">{w("근거를 선택하면 접수 원문에서 같은 문구를 찾습니다.", "Select evidence to find its exact text in the received input.")}</p>
    <div className="decision-source-list" aria-label={w("판정 근거 목록", "Decision evidence list")}>{(core ? evidenceSourceReferences(items) : items.map((item, index) => ({ item, index, sourceIndex: index }))).map(({ item, index, sourceIndex }) => <button type="button" key={item.number} aria-pressed={selected?.number === item.number} onClick={() => select(item)}><span><strong>{w("근거", "Evidence")} {item.number}</strong><Direction value={item.supports} label={sections.find(group => group.side === item.supports)?.label} /></span><small>{analystFieldLabel(item.field)}</small>{sourceIndex === index ? <code>{item.excerpt}</code> : <small className="decision-shared-source-note">{sharedSourceText(items[sourceIndex].number, w)}</small>}</button>)}</div>
    {!items.length && <p className="muted">{w("저장된 근거가 없습니다. 입력은 직접 확인할 수 있습니다.", "No evidence recorded. You can still inspect the input.")}</p>}
    {selected && <div className="decision-selected-explanation"><h3>{w("판단 이유", "Interpretation")}</h3><p>{selected.interpretation_ko}</p></div>}
  </div>
    <div ref={pane} className="decision-source-pane">{error ? <div className="panel error" role="alert"><p>{w("입력을 조회하지 못했습니다.", "Could not load the input.")}</p><button type="button" className="secondary" onClick={onRetry}>{w("다시 시도", "Retry")}</button></div> : event ? <RawEventView event={event} detail={detail} target={target} active={active} /> : <p className="panel loading" role="status">{w("입력을 불러오는 중…", "Loading input…")}</p>}</div>
  </section>;
}

export function AnalysisWorkspace({ detail, event, target, onSelect, error, onRetry, active = true, relations,
  relationsError, relationsLoading, displayPollingStopped, onRetryRelations, history, historyError, onSaved, onExecution, onFeedback, children }) {
  const w = useR5Words(), workspace = useRef(null), selectedField = useRef(null);
  const [allFields, setAllFields] = useState(false);
  const compactFields = ["company_name", "src_ip", "dest_ip", "waf_vendor", "waf_action", "signature"];
  const location = useMemo(() => resolveEvidenceInput(event, detail, target), [event, detail, target]);
  const fields = useMemo(() => inputFieldRows(detail, event), [detail, event]);
  const visibleFields = allFields ? fields : fields.filter(row => compactFields.includes(row.field));
  useWorkspaceHeight(workspace, active);
  useEffect(() => { setAllFields(false); }, [detail.id]);
  useEffect(() => { if (location?.found && location.section === "fields" && !compactFields.includes(location.rowField)) setAllFields(true); }, [target, location]);
  useEffect(() => { if (location?.found && location.section === "fields") revealInPane(selectedField.current); }, [target, location, allFields]);
  const select = item => onSelect?.(item ? { ...item } : null);
  const completed = detail.status === "completed" && detail.result;
  return <div ref={workspace} className={`analysis-workspace test-analysis-workspace ${detail.analysis_purpose === "production" ? "production-analysis-workspace" : "test-case-analysis-workspace"}`}>
    <section className="test-analysis-pane test-results-pane" aria-label={w("판정과 근거", "Decision and evidence")} tabIndex={0}>
      <header className="test-pane-heading"><h2><Icon name="shield" size={16} />{w("판정과 근거", "Decision and evidence")}</h2><span>{w("근거를 눌러 원문 확인", "Select evidence to view its source")}</span></header>
      <div className="test-pane-content"><DecisionHero detail={detail} />
        {completed ? <><EvidenceCards {...{ detail, relations, relationsError, relationsLoading, displayPollingStopped, onRetryRelations }} selectedTarget={target} selectOnCard onViewInput={select} onExecution={onExecution} /><FinalDecisionReason detail={detail} /><TechnicalInterpretation detail={detail} onExecution={onExecution} /><DecisionConditions detail={detail} onViewInput={select} /></>
          : <section className="panel decision-awaiting"><h2>{detail.status === "failed" ? w("실행 확인", "Check execution") : detail.status === "canceled" ? w("테스트 중지", "Test stopped") : detail.status === "completed" ? w("결과 본문 미기록", "Result body not recorded") : w("분석 진행", "Analysis progress")}</h2><p>{detail.status === "failed" ? w("실패한 단계와 기존 기록은 실행 탭에서 확인할 수 있습니다.", "Check the Execution tab for the failed step and saved records.") : detail.status === "canceled" ? w("이 문항은 판정 전에 중지되었습니다. 입력과 기존 실행 기록은 확인할 수 있습니다.", "This case stopped before a final decision. Input and saved execution records remain available.") : detail.status === "completed" ? w("처리는 완료되었지만 저장된 결과 본문이 없습니다. 실행 기록을 확인하세요.", "Processing completed, but the result body is unavailable. Check the execution record.") : w("완료된 판정 근거를 기다리고 있습니다.", "Waiting for completed decision evidence.")}</p><NavigationAction onClick={onExecution}>{w("실행 상세", "Execution details")}</NavigationAction></section>}
        <DecisionContext {...{ detail, history, historyError, onSaved, onExecution, onFeedback }} />{children}
      </div>
    </section>
    <section className="test-analysis-pane test-input-pane" aria-label={w("접수된 입력", "Received input")} tabIndex={0}>
      <header className="test-pane-heading"><h2><Icon name="code" size={16} />{w("입력과 원문", "Input and source")}</h2><span>{target ? w(`근거 ${target.number ?? ""} 선택`, `Evidence ${target.number ?? ""} selected`) : w("원문 보존", "Original preserved")}</span></header>
      {error ? <div className="panel error" role="alert"><p>{w("입력을 조회하지 못했습니다.", "Could not load the input.")}</p><button type="button" className="secondary" onClick={onRetry}>{w("다시 시도", "Retry")}</button></div> : !event ? <p className="panel loading" role="status">{w("입력을 불러오는 중…", "Loading input…")}</p> : <>
        {location && <p className="test-source-status" role="status"><strong>{w("선택한 근거", "Selected evidence")}: {target.number ?? target.evidence_id ?? "—"} / {target.field}</strong><span>{location.found ? location.reason === "recorded_positions" ? w("기록된 원문 위치와 발췌문이 일치합니다.", "Recorded source positions exactly match the excerpt.") : w("지정 필드에서 발췌문과 정확히 일치하는 한 곳을 확인했습니다.", "One exact excerpt match was found in the specified field.") : w("정확한 위치를 확인하지 못해 강조하지 않습니다. 반복 문구, 변환된 표현이나 원문 위치 기록을 확인하세요.", "The exact location could not be verified. Repeated text, transformed content, or missing position records may need review.")}</span></p>}
        <div className="test-pane-content"><section className="panel test-input-fields"><div className="test-input-fields-heading"><h3>{w("입력 필드", "Input fields")}</h3><button type="button" className="text-button" aria-expanded={allFields} onClick={() => setAllFields(value => !value)}>{allFields ? w("전체 입력 필드 접기", "Collapse input fields") : w(`전체 입력 필드 보기 (${fields.length})`, `View all input fields (${fields.length})`)}</button></div><DataTable label={w("접수 입력 필드", "Received input fields")} data={visibleFields} rowClassName={row => location?.found && location.section === "fields" && row.field === location.rowField ? "test-input-field-selected" : undefined} columns={[
          { id: "field", width: "42%", header: w("필드", "Field"), render: row => <><span className="test-field-label">{w(analystFieldLabel(row.field), ({ company_name: "Company", src_ip: "Source IP", dest_ip: "Destination IP", waf_vendor: "WAF vendor", waf_action: "WAF action", signature: "Signature" })[row.field] || row.field)}</span>{analystFieldLabel(row.field) !== row.field && <code className="test-field-key">{row.field}</code>}</> },
          { id: "value", header: w("수신 값", "Received value"), render: row => <span className="test-field-value" ref={node => { if (row.field === location?.rowField) selectedField.current = node; }}>{row.text === null ? row.value === null ? "null" : JSON.stringify(row.value) : splitHighlightedText(row.text, location?.found && location.section === "fields" && row.field === location.rowField ? location.ranges : []).map((part, index) => part.highlighted ? <mark key={index}>{part.text}</mark> : part.text)}</span> },
        ]} /></section>
          <section className="panel test-payload-panel"><h3>{w("HTTP 원문", "Raw HTTP payload")}</h3><ExactSourceText value={event.payload} ranges={location?.found && location.section === "payload" ? location.ranges : []} target={target} label={w("HTTP 원문", "Raw HTTP payload")} /></section>
          <p className="test-input-notice muted"><Icon name="shield" size={13} />{w("관리자 열람 기록이 남습니다. 접수 원문과 Cookie를 마스킹하지 않으며, 분석 당시 모델 입력은 생략 범위에 따라 다를 수 있습니다.", "Administrator access is audited. Received source and cookies are unmasked; the original model input may differ where content was omitted.")}</p>
          <section className="panel"><details><summary>{w("인코딩과 난독화", "Encoding and obfuscation")}</summary>{active && <DecodingView decoding={event.decoding} />}</details></section>
        </div>
      </>}
    </section>
  </div>;
}

// Keep the former internal export for callers while both purposes share one view.
export const TestAnalysisWorkspace = AnalysisWorkspace;

export function ExecutionView({ detail, runs, runsError, runsLoading, onRetry, onMetadata, initialJson = false, active, displayLoading, displayError, displayPollingStopped, onRetryDisplay, executionSelection, onExecutionSelectionChange }) {
  const { t } = useConsolePreferences(), w = useR5Words();
  const configuration = <><InferenceSummary detail={detail} runs={runs} onMetadata={onMetadata} compact title={w("사용한 설정", "Recorded settings")} /><EvidenceDisplaySummary detail={detail} loading={displayLoading} error={displayError} stopped={displayPollingStopped} onRetry={onRetryDisplay} /></>;
  return <div className="decision-execution test-execution">
    <section className="test-execution-overview" aria-label={w("실행 시간", "Execution timing")}><dl>{[
      [w("접수부터 판정까지", "Admission to decision"), detail.total_elapsed_ms, w("초기 대기 포함", "Includes initial queue wait"), "clock"], [w("초기 대기", "Initial queue wait"), detail.queue_wait_ms, w("접수부터 첫 분석 시작", "Admission to first start"), "clock"], [w("분석 처리", "Analysis processing"), detail.processing_duration_ms, w("첫 시작부터 종료", "First start to finish"), "analyses"],
    ].map(([label, value, note, icon], index) => <div key={label} className={`execution-timing-${index}`}><dt>{label}<Icon name={icon} size={16} /></dt><dd>{formatDuration(value, w("미측정", "Not measured"))}</dd><small>{note}</small></div>)}</dl><p className="muted">{w("분석 처리는 초기 대기를 제외하며 내부 대기, 재시도와 복구를 포함합니다. 병렬 단계의 합이나 HTTP 요청부터 응답까지의 시간은 아닙니다.", "Processing excludes the initial queue wait and includes internal waits, retries and recovery. It is neither a sum of parallel stages nor HTTP request-to-response time.")}</p></section>
    <p className="muted decision-audit-note"><Icon name="shield" size={13} />{t("detail.audit")}</p>
    {runsError && <div className="panel error" role="alert"><p>{t("detail.agentError")}</p><button type="button" className="secondary" disabled={runsLoading} onClick={onRetry}>{t("retry")}</button></div>}
    {runs === null ? !runsError && <p role="status">{t("loading")}</p> : <AgentHistory runs={runs || []} active={active} selection={executionSelection} onSelectionChange={onExecutionSelectionChange} compact finalResult={detail.result ?? null} configuration={configuration} initialJson={initialJson} />}
  </div>;
}
