import { useEffect, useRef, useState } from "react";
import { api } from "./api.js";
import { formatDate, formatDuration } from "./analysisView.js";
import { MetricHelp } from "./EvaluationMetrics.jsx";
import { metricText } from "./evaluationMetrics.js";
import { evaluationOutcomes, referenceVerdicts } from "./labelEvaluation.js";
import { comparisonChanges, comparisonExclusions, comparisonWarnings, initialTestComparisonState, testComparisonChange, testComparisonQuery, tokenCountText, watchComparisonRead } from "./testComparison.js";
import "./testComparison.css";
import HelpTooltip from "./HelpTooltip.jsx";
import Dialog from "./Dialog.jsx";

const emptyRead = () => ({ key: "", data: null, loading: true, error: "" });
const countText = value => Number.isFinite(value) ? value.toLocaleString() : "미기록";
const runLabel = run => `${run?.name || "실행명 미기록"} · ${run?.prompt_version || "프롬프트 미기록"}`;

export function TestComparisonMetrics({ data }) {
  const columns = [data.baseline_evaluation, data.candidate_evaluation];
  const metrics = [["accuracy", "Accuracy"], ["precision", "Precision"], ["recall", "Recall"], ["f1", "F1 score"], ["coverage", "판정 커버리지"], ["abstention_rate", "판정 보류율"]];
  const counts = [["false_negative", "미탐 방향"], ["false_positive", "과탐 방향"], ["abstained", "이진 답안 · AI 보류"], ["expected_abstention_match", "기대 보류 일치"], ["expected_abstention_mismatch", "기대 보류 불일치"]];
  return <div className="table-wrap"><table className="test-comparison-metrics"><thead><tr><th>동일 비교 문항 기준 지표</th><th>기준 실행</th><th>후보 실행</th></tr></thead><tbody>
    <tr><th>비교 가능한 문항 수</th>{columns.map((summary, index) => <td key={index}>{countText(summary?.total)}건</td>)}</tr>
    <tr><th>이진 확정 판정 수</th>{columns.map((summary, index) => <td key={index}>{countText(summary?.binary_decided)}건</td>)}</tr>
    {metrics.map(([key, label]) => <tr key={key}><th>{label}<MetricHelp metric={key} label={label} /></th>{columns.map((summary, index) => <td key={index}>{metricText(summary?.metrics?.[key], key)}</td>)}</tr>)}
    {counts.map(([key, label]) => <tr key={key}><th>{label}</th>{columns.map((summary, index) => <td key={index}>{countText(summary?.outcomes?.[key])}건</td>)}</tr>)}
  </tbody></table></div>;
}

export function TestComparisonPerformance({ performance }) {
  const sides = [performance?.baseline, performance?.candidate];
  return <section className="test-comparison-performance"><h3>처리 시간·토큰<HelpTooltip label="처리 시간과 사용량">비교 가능한 문항의 기록된 재시도·재선점 이력을 포함합니다. 실패 등으로 비교에서 제외된 문항과 미기록 호출은 포함하지 않습니다. 미측정은 0과 구분합니다.</HelpTooltip></h3><p className="muted">두 실행 전체의 비용 또는 청구량은 아닙니다.</p>
    <div className="table-wrap"><table><thead><tr><th>항목</th><th>기준 실행</th><th>후보 실행</th></tr></thead><tbody>
      {[["mean_ms", "문항 처리 시간 · 평균"], ["p50_ms", "문항 처리 시간 · p50"], ["p95_ms", "문항 처리 시간 · p95"]].map(([key, label]) => <tr key={key}><th>{label}<HelpTooltip label={label}>{key === "p50_ms" ? "비교 가능한 문항 처리 시간의 중앙값입니다. 절반이 이 시간 이내에 처리됐습니다." : key === "p95_ms" ? "비교 가능한 문항 처리 시간의 95백분위입니다. 약 95%가 이 시간 이내에 처리됐습니다. 소수의 느린 문항을 확인할 때 유용합니다." : "비교 가능한 문항의 평균 처리 시간입니다. 낮을수록 빠르지만 모델·입력 조건이 같아야 비교하기 쉽습니다."} 낮을수록 빠른 처리이며 판정 품질을 뜻하지 않습니다.</HelpTooltip></th>{sides.map((side, index) => <td key={index}>{formatDuration(side?.processing_ms?.[key], "미측정")}</td>)}</tr>)}
      <tr><th>문항 처리 시간 측정 / 미기록</th>{sides.map((side, index) => <td key={index}>{countText(side?.processing_ms?.count)} / {countText(side?.processing_ms?.missing_count)}건</td>)}</tr>
      <tr><th>기록된 LLM 단계 시간 합계</th>{sides.map((side, index) => <td key={index}>{side?.llm_step_ms?.count > 0 ? formatDuration(side.llm_step_ms.sum_ms, "미측정") : "미측정"}<small>측정 {countText(side?.llm_step_ms?.count)} · 미기록 {countText(side?.llm_step_ms?.missing_count)}단계</small></td>)}</tr>
      {[["input_tokens", "입력 토큰"], ["output_tokens", "출력 토큰"], ["total_tokens", "전체 토큰"]].map(([key, label]) => <tr key={key}><th>{label} · 완전 측정 단계 합계</th>{sides.map((side, index) => <td key={index}>{tokenCountText(side?.tokens?.[key], side?.missing_agent_histories)}<small>완전 측정 {countText(side?.tokens?.[key]?.measured_steps)} · 미측정 {countText(side?.tokens?.[key]?.missing_steps)}단계</small></td>)}</tr>)}
      <tr><th>출력 교정이 발생한 단계</th>{sides.map((side, index) => <td key={index}>{countText(side?.output_repair_steps)}단계</td>)}</tr>
      <tr><th>실행 이력 미기록 문항</th>{sides.map((side, index) => <td key={index}>{countText(side?.missing_agent_histories)}건</td>)}</tr>
    </tbody></table></div>
  </section>;
}

export function TestComparisonItems({ items, onOpen }) {
  return <div className="table-wrap"><table className="test-comparison-items"><thead><tr><th>문항 / 참고 답안</th><th>기준 실행</th><th>후보 실행</th><th>변화 / 비교 조건</th></tr></thead><tbody>{items.map((item, index) => <tr key={`${item.event_id}-${index}`}>
    <td><strong>{item.case_name || item.event_id}</strong><small>{item.case_name ? item.event_id : ""}</small><small>{item.difficulty || "난이도 미분류"} · {item.test_category || "유형 미분류"}</small><small>공통 답안: {referenceVerdicts[item.reference_verdict] || "동일 답안 확인 불가"}</small></td>
    {[[item.baseline_analysis_id, item.baseline_verdict, item.baseline_outcome, "기준"], [item.candidate_analysis_id, item.candidate_verdict, item.candidate_outcome, "후보"]].map(([id, verdict, outcome, label]) => <td key={label}><strong>{referenceVerdicts[verdict] || "판정 없음"}</strong><small>{evaluationOutcomes[outcome] || "평가 정보 확인 필요"}</small>{id && <button type="button" className="text-button" onClick={() => onOpen(id)}>{label} 분석 상세</button>}</td>)}
    <td><span className={`comparison-change comparison-${item.change}`}>{comparisonChanges[item.change] || "변화 확인 필요"}</span>{item.comparison_status !== "comparable" && <small>{comparisonExclusions[item.comparison_status] || "비교 조건 확인 필요"}</small>}</td>
  </tr>)}</tbody></table></div>;
}

export function TestComparisonResult({ data, state, onChange, onOpen, onRefresh, loading }) {
  return <div className="test-comparison-result">
    <div className="test-comparison-runs"><div><span>기준</span><strong>{runLabel(data.baseline)}</strong><small>{data.baseline?.profile_metadata?.model_name || "모델 미기록"}</small></div><div><span>후보 · 현재 실행</span><strong>{runLabel(data.candidate)}</strong><small>{data.candidate?.profile_metadata?.model_name || "모델 미기록"}</small></div></div>
    <p className="notice">공통 접수 {countText(data.counts?.accepted_pairs)}쌍 중 같은 이벤트 내용·같은 접수 당시 답안으로 평가 가능한 {countText(data.counts?.comparable_pairs)}쌍을 비교합니다. 위 실행 상세의 난이도·유형 필터는 이 비교에 적용되지 않습니다.</p>
    <p>판정 변경 {countText(data.counts?.changed)}건 · 답안 일치로 변경 {countText(data.counts?.improved)}건 · 답안 불일치로 변경 {countText(data.counts?.regressed)}건</p>
    <p className="evaluation-footnote">답안과 일치하도록 바뀐 문항과 불일치하도록 바뀐 문항을 구분합니다. 보류 전환을 곧바로 미탐·과탐으로 취급하지 않습니다. 기대 답안과의 일치는 독립적인 운영 정확도가 아닙니다.</p>
    {!!Object.keys(data.counts?.exclusions || {}).length && <div className="test-comparison-exclusions"><h3>비교에서 제외된 문항</h3><ul>{Object.entries(data.counts.exclusions).map(([key, value]) => <li key={key}>{comparisonExclusions[key] || "비교 조건 확인 필요"}: {countText(value)}건</li>)}</ul></div>}
    <TestComparisonMetrics data={data} />
    <TestComparisonPerformance performance={data.performance} />
    {!!data.warnings?.length && <span className="ux-muted">비교 조건 {data.warnings.length}개<HelpTooltip label="비교 조건">{data.warnings.map(code => comparisonWarnings[code] || "추가 비교 조건이 있습니다. 관리자에게 실행 기록을 확인하세요.").join("\n\n")}</HelpTooltip></span>}
    <section><div className="panel-head"><h3>문항별 판정 변화</h3><button type="button" className="secondary" disabled={loading} onClick={onRefresh}>비교 결과 새로고침</button></div>
      <label className="comparison-checkbox"><input type="checkbox" checked={state.changes_only} disabled={loading} onChange={event => onChange({ type: "changes", value: event.target.checked })} />판정이 변경된 비교 가능 문항만 보기</label><p className="muted">이 필터와 페이지 변경은 위 지표·소요 시간의 전체 비교 분모를 바꾸지 않습니다.</p>
      {data.items.length ? <TestComparisonItems items={data.items} onOpen={onOpen} /> : <p className="muted">이 조건에 해당하는 비교 문항이 없습니다.</p>}
      <div className="pagination"><span>{countText(data.total_items)}건 · {Math.floor(state.offset / state.limit) + 1}페이지</span><label>페이지당<select value={state.limit} disabled={loading} onChange={event => onChange({ type: "limit", value: Number(event.target.value) })}>{[25, 50, 100].map(value => <option key={value} value={value}>{value}건</option>)}</select></label><button type="button" className="secondary" disabled={loading || !state.offset} onClick={() => onChange({ type: "page", direction: -1 })}>이전</button><button type="button" className="secondary" disabled={loading || state.offset + state.limit >= data.total_items} onClick={() => onChange({ type: "page", direction: 1 })}>다음</button></div>
    </section>
  </div>;
}

export default function TestRunComparison({ candidateId, state: controlledState, onStateChange, onOpen, onUnauthorized, embedded = false }) {
  const [localState, setLocalState] = useState(initialTestComparisonState);
  const state = controlledState ?? localState, setState = controlledState == null ? setLocalState : onStateChange;
  const change = action => setState?.(current => testComparisonChange(current, action));
  const [catalogRead, setCatalogRead] = useState(emptyRead), [comparisonRead, setComparisonRead] = useState(emptyRead), [reload, setReload] = useState(0);
  const unauthorized = useRef(onUnauthorized); unauthorized.current = onUnauthorized;
  const catalogKey = JSON.stringify([candidateId, state.open, state.query, reload]);
  const comparisonKey = JSON.stringify([candidateId, state.open, testComparisonQuery(state), reload]);
  const catalog = catalogRead.key === catalogKey ? catalogRead : emptyRead();
  const result = comparisonRead.key === comparisonKey ? comparisonRead : emptyRead();
  useEffect(() => {
    if (!state.open) return undefined;
    setCatalogRead({ ...emptyRead(), key: catalogKey });
    return watchComparisonRead({ read: async options => { const data = await api.testRuns(state.query, options); if (!Array.isArray(data?.items)) throw new Error("invalid_catalog"); return data; },
      onUpdate: patch => setCatalogRead({ ...patch, key: catalogKey }), onUnauthorized: () => unauthorized.current?.() });
  }, [catalogKey]);
  useEffect(() => {
    if (!state.open || !state.baselineId || state.baselineId === candidateId) return undefined;
    setComparisonRead({ ...emptyRead(), key: comparisonKey });
    return watchComparisonRead({ read: async options => { const data = await api.compareTestRuns(candidateId, testComparisonQuery(state), options); if (data?.baseline?.id !== state.baselineId || data?.candidate?.id !== candidateId || !Array.isArray(data?.items)) throw new Error("invalid_comparison"); return data; },
      onUpdate: patch => setComparisonRead({ ...patch, key: comparisonKey }), onUnauthorized: () => unauthorized.current?.() });
  }, [comparisonKey]);
  const content = <section className="panel test-run-comparison">
    {state.open && <><p>비교 기준이 될 테스트를 선택하세요.</p><p className="ux-muted">같은 입력·답안의 저장된 결과만 비교하며 모델을 호출하지 않습니다. 모델·지침 변경만의 효과를 입증하는 실험은 아닙니다.</p>
      <form className="test-run-search" onSubmit={event => { event.preventDefault(); change({ type: "search" }); }}><label>비교 기준 테스트명 검색<input value={state.queryText} placeholder="테스트명으로 찾기" maxLength={120} onChange={event => change({ type: "draft", value: event.target.value })} /></label><button type="submit" className="secondary" disabled={catalog.loading}>검색</button><button type="button" className="secondary" disabled={catalog.loading || (state.baselineId && result.loading)} onClick={() => setReload(value => value + 1)}>새로고침</button></form>
      {catalog.loading && <p role="status">비교 기준 목록을 조회하는 중…</p>}{catalog.error && <p className="error" role="alert">{catalog.error}</p>}
      {catalog.data && <><div className="comparison-baselines" aria-label="비교 기준 선택">{catalog.data.items.filter(run => run.id !== candidateId).map(run => <button type="button" className="secondary" key={run.id} aria-pressed={state.baselineId === run.id} onClick={() => change({ type: "baseline", id: run.id })}><strong>{run.name}</strong><small>{run.prompt_version || "프롬프트 미기록"} · {formatDate(run.created_at)}</small><span>{state.baselineId === run.id ? "선택된 비교 기준" : "이 실행을 기준으로 비교"}</span></button>)}</div>{!catalog.data.items.some(run => run.id !== candidateId) && <p className="muted">이 페이지에 비교할 다른 테스트가 없습니다. 다른 이름으로 검색하거나 페이지를 이동하세요.</p>}
        <div className="pagination"><span>목록 {countText(catalog.data.total)}개 · 현재 실행은 선택 제외</span><button type="button" className="secondary" disabled={catalog.loading || !state.query.offset} onClick={() => change({ type: "search_page", direction: -1 })}>기준 목록 이전</button><button type="button" className="secondary" disabled={catalog.loading || state.query.offset + state.query.limit >= catalog.data.total} onClick={() => change({ type: "search_page", direction: 1 })}>기준 목록 다음</button></div></>}
      {!state.baselineId ? <p className="muted">비교 기준을 선택하면 동일 문항의 지표와 판정 변화를 조회합니다.</p> : state.baselineId === candidateId ? <p className="error">다른 실행을 비교 기준으로 선택하세요.</p> : <>{result.loading && <p role="status">두 실행의 동일 문항을 비교하는 중…</p>}{result.error && <p className="error" role="alert">{result.error}</p>}{result.data && <TestComparisonResult data={result.data} state={state} onChange={change} onOpen={onOpen} onRefresh={() => setReload(value => value + 1)} loading={result.loading} />}</>}
    </>}
  </section>;
  return embedded ? content : <><button type="button" className="secondary" onClick={() => change({ type: "open", value: true })}>다른 테스트와 비교</button><Dialog open={state.open} title="테스트 비교" onClose={() => change({ type: "open", value: false })}>{content}</Dialog></>;
}
