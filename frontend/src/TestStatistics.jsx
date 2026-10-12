import { useEffect, useMemo, useRef, useState } from "react";
import { api } from "./api.js";
import DataTable, { changedSort, serverSorting } from "./DataTable.jsx";
import Pagination from "./Pagination.jsx";
import { useWords } from "./LifecycleViews.jsx";
import { watchTestRunRead } from "./TestRuns.jsx";
import { initialStatistics, percent, statisticFields, statisticsQuery } from "./testStatistics.js";
import "./testStatistics.css";

const verdicts = [["true_positive", "정탐", "True positive"], ["false_positive", "오탐", "False positive"], ["inconclusive", "보류", "Inconclusive"]];

export function VerdictDistribution({ row, words: w }) {
  const description = verdicts.map(([key, ko, en]) => `${w(ko, en)} ${row[key]} (${percent(row.verdict_rates[key])})`).join(" · ");
  return <div className="test-stat-distribution"><div className="test-stat-bar" role="img" aria-label={description}>
    {verdicts.map(([key]) => row.verdict_rates[key] > 0 && <span key={key} className={`test-stat-${key}`} style={{ width: `${row.verdict_rates[key] * 100}%` }} />)}
  </div><span className="test-stat-distribution-label" aria-hidden="true">{verdicts.map(([key, ko, en]) => <span key={key}><i className={`test-stat-${key}`} />{w(ko, en)} {percent(row.verdict_rates[key])}</span>)}</span></div>;
}

export default function TestStatistics({ run, filters, state: controlled, onChange, onGroup, onUnauthorized, refresh }) {
  const w = useWords(); const [local, setLocal] = useState(initialStatistics), [reload, setReload] = useState(0);
  const state = controlled || local, setState = onChange || setLocal;
  const empty = () => ({ key: null, data: null, error: "", loading: true });
  const [read, setRead] = useState(empty);
  const unauthorized = useRef(onUnauthorized); unauthorized.current = onUnauthorized;
  const query = useMemo(() => statisticsQuery(filters, state), [filters, state]);
  const key = JSON.stringify([run.id, query, refresh, run.completed, run.failed, run.pending, run.processing, run.canceled, reload]);
  const { data, error, loading } = read.key === key ? read : empty();
  useEffect(() => {
    setRead({ ...empty(), key });
    return watchTestRunRead({ read: async options => {
      const result = await api.testStatistics(run.id, query, options);
      if (result?.test_run_id !== run.id || !Array.isArray(result.items) || !result.summary) throw Error("invalid_statistics");
      return result;
    }, onUpdate: patch => setRead(current => ({ ...current, ...patch, key })),
    isRunning: result => result.summary.pending + result.summary.processing > 0,
    errorMessage: w("통계 정보를 조회하지 못했습니다. 다시 조회해 주세요.", "Unable to load statistics. Try again."),
    onUnauthorized: () => unauthorized.current?.() });
  }, [key]);
  const change = patch => setState(current => ({ ...(current || initialStatistics()), ...patch }));
  const field = statisticFields.find(([value]) => value === state.group_by) || statisticFields[0];
  const accuracyTitle = run.evaluation_mode === "ground_truth" ? w("기대 판정 대비 전체 정확도", "Overall expected-verdict accuracy") : w("참고 판정 대비 전체 정확도", "Overall reference accuracy");
  const columns = [
    { id: "value", header: w(field[1], field[2]), width: "24%", sortable: true, render: row => <button className="text-button" onClick={() => onGroup(state.group_by, row.value)}>{row.value == null ? w("미입력", "Not provided") : state.group_by === "waf_action" ? ({ D: w("차단", "Deny"), A: w("허용", "Allow") }[row.value] || row.value) : row.value}</button> },
    { id: "cases", header: w("문항", "Cases"), width: "8%", className: "numeric", sortable: true },
    { id: "distribution", header: w("정탐·오탐·보류 비율", "Verdict distribution"), width: "30%", render: row => <VerdictDistribution row={row} words={w} /> },
    { id: "comparable", header: w("비교 가능", "Compared"), width: "10%", className: "numeric", render: row => <>{row.comparable}<small>{w("답안 있음", "Labeled")} {row.labeled}</small></> },
    { id: "accuracy", header: w("전체 정확도", "Overall accuracy"), width: "10%", className: "numeric", sortable: true, render: row => <><strong>{percent(row.accuracy)}</strong><small>{row.matches} / {row.comparable}</small></> },
    { id: "errors", header: w("미탐·과탐", "Misses / false alarms"), width: "10%", className: "numeric", render: row => <>{row.false_negatives} / {row.false_positives}</> },
    { id: "completed", header: w("완료·실패", "Done / failed"), width: "8%", className: "numeric", render: row => <>{row.completed} / {row.failed}</> },
  ];
  return <section className="panel test-statistics" aria-busy={loading}>
    <div className="panel-head"><div><h2>{w("통계 요약", "Statistics Summary")}</h2><p className="ux-muted">{w("선택한 필드별 판정 분포와 참고 판정 대비 정확도를 확인합니다.", "Compare verdict distribution and reference accuracy by field.")}</p></div><button className="secondary" disabled={loading} onClick={() => setReload(value => value + 1)}>{w("다시 조회", "Refresh")}</button></div>
    <form className="test-stat-controls" onSubmit={event => { event.preventDefault(); change({ q: state.draft.trim(), offset: 0 }); }}>
      <label>{w("집계 기준", "Group by")}<select value={state.group_by} onChange={event => change({ group_by: event.target.value, q: "", draft: "", offset: 0 })}>{statisticFields.map(([value, ko, en]) => <option key={value} value={value}>{w(ko, en)}</option>)}</select></label>
      <label>{w("항목 검색", "Search groups")}<input maxLength={500} value={state.draft} onChange={event => change({ draft: event.target.value })} placeholder={w("필드 값으로 검색", "Search field values")} /></label><button className="secondary" disabled={loading}>{w("검색", "Search")}</button>
    </form>
    {error && <p className="error" role="alert">{error}</p>}
    {!data && !error && <p role="status">{w("통계를 불러오는 중…", "Loading statistics…")}</p>}
    {data && <>
      <div className="test-stat-overview">
        <div><span>{w("분석 문항", "Accepted cases")}</span><strong>{data.summary.cases.toLocaleString()}</strong><small>{w("완료", "Completed")} {data.summary.completed} · {w("실패", "Failed")} {data.summary.failed}</small></div>
        <div><span>{w("보류 비율", "Inconclusive rate")}</span><strong>{percent(data.summary.verdict_rates.inconclusive)}</strong><small>{data.summary.inconclusive} / {data.summary.distribution_total} {w("판정 완료", "completed verdicts")}</small></div>
        <div><span>{accuracyTitle}</span><strong>{percent(data.summary.accuracy)}</strong><small>{data.summary.matches} / {data.summary.comparable} {w("비교 가능 · 보류 포함", "compared · includes inconclusive")}</small></div>
      </div>
      <p className="test-stat-definition">{w("비율은 판정 완료 문항 기준입니다. 정확도는 보류를 포함한 답안 일치 ÷ 비교 가능 문항이며, 실행 실패·모의 실행 등 평가 제외 문항은 빼고 계산합니다.", "Distribution uses completed verdicts. Accuracy includes inconclusive answers: matches / comparable cases, excluding failures, stub runs and other ineligible results.")}</p>
      <p className="test-stat-basis">{w("답안 기준", "Reference basis")}: {w({ latest: "최신 참고 판정", initial: "접수 당시 기대 판정", saved: "저장한 평가", ground_truth: "공식 버전의 기대 판정" }[data.reference_basis] || "미확인", { latest: "Latest Reference Verdict", initial: "Expected verdict at admission", saved: "Saved evaluation", ground_truth: "Published version's expected verdict" }[data.reference_basis] || "Unknown")} · {w("접수 거부", "Rejected")} {data.rejected} · {w("중복 접수", "Duplicates")} {data.duplicates}</p>
      <DataTable className="test-stat-table" label={w("필드별 통계", "Statistics by field")} data={data.items} columns={columns} getRowId={row => row.value == null ? "missing" : `value:${row.value}`} sorting={serverSorting(state.sort_by, state.sort_order)} onSortingChange={update => change(changedSort(update, serverSorting(state.sort_by, state.sort_order)))} empty={w("조건에 맞는 집계 항목이 없습니다.", "No groups match this scope.")} />
      <Pagination label={w("통계 항목 페이지", "Statistics pages")} pageSizes={[10, 25, 50, 100]} total={data.total_groups} limit={state.limit} offset={state.offset} disabled={loading} onOffsetChange={offset => change({ offset })} onLimitChange={limit => change({ limit, offset: 0 })} />
      <p className="ux-muted">{w("항목명을 누르면 해당 문항을 확인할 수 있습니다. 답안이 없거나 비교 가능한 문항이 없으면 정확도는 —로 표시합니다.", "Select a group to inspect its cases. Accuracy is — when no comparable reference answers exist.")}</p>
    </>}
  </section>;
}
