import { useEffect, useMemo, useRef, useState } from "react";
import { api } from "./api.js";
import DataTable, { changedSort, serverSorting } from "./DataTable.jsx";
import Pagination from "./Pagination.jsx";
import { useWords } from "./LifecycleViews.jsx";
import { watchTestRunRead } from "./TestRuns.jsx";
import { addGrouping, changeGrouping, initialStatistics, percent, statisticFields, statisticsFields, statisticsQuery } from "./testStatistics.js";
import "./testStatistics.css";
import TestCaseFilter from "./TestCaseFilter.jsx";

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
  const fields = statisticsFields(state);
  const displayValue = (field, value) => value == null ? w("미입력", "Not provided") : field === "waf_action"
    ? ({ D: w("차단", "Deny"), A: w("허용", "Allow") }[value] || value) : value;
  const rowValues = row => row.values || { [fields[0]]: row.value };
  const openGroup = row => onGroup(fields.length === 1 ? fields[0] : fields, fields.length === 1 ? row.value : rowValues(row));
  const groupLabel = row => fields.map(field => displayValue(field, rowValues(row)[field])).join(" · ");
  const widths = fields.length === 3 ? { dimension: 12, cases: 8, distribution: 23, comparable: 9, errors: 7, completed: 7 }
    : fields.length === 2 ? { dimension: 14, cases: 7, distribution: 28, comparable: 10, errors: 9, completed: 8 }
    : { dimension: 24, cases: 8, distribution: 30, comparable: 10, errors: 10, completed: 8 };
  const accuracyTitle = state.case_filter ? w("선택 문항 정확도", "Selected-case accuracy") : run.evaluation_mode === "ground_truth" ? w("기대 판정 대비 전체 정확도", "Overall expected-verdict accuracy") : w("참고 판정 대비 전체 정확도", "Overall reference accuracy");
  const columns = [
    ...fields.map((field, index) => ({ id: index === 0 ? "value" : field, header: w(...statisticFields.find(([key]) => key === field).slice(1)),
      width: `${widths.dimension}%`, sortable: index === 0, className: "test-stat-dimension",
      render: row => <button type="button" className="text-button" aria-label={fields.length > 1 ? `${groupLabel(row)} ${w("문항 보기", "cases")}` : undefined}
        onClick={() => openGroup(row)}>{displayValue(field, rowValues(row)[field])}</button> })),
    { id: "cases", header: w("문항", "Cases"), width: `${widths.cases}%`, className: "numeric", sortable: true },
    { id: "distribution", header: w("정탐·오탐·보류 비율", "Verdict distribution"), width: `${widths.distribution}%`, render: row => <VerdictDistribution row={row} words={w} /> },
    { id: "comparable", header: w("비교 가능", "Compared"), width: `${widths.comparable}%`, className: "numeric", render: row => <>{row.comparable}<small>{w("답안 있음", "Labeled")} {row.labeled}</small></> },
    { id: "accuracy", header: w("전체 정확도", "Overall accuracy"), width: "10%", className: "numeric", sortable: true, render: row => <><strong>{percent(row.accuracy)}</strong><small>{row.matches} / {row.comparable}</small></> },
    { id: "errors", header: w("미탐·과탐", "Misses / false alarms"), width: `${widths.errors}%`, className: "numeric", render: row => <>{row.false_negatives} / {row.false_positives}</> },
    { id: "completed", header: w("완료·실패", "Done / failed"), width: `${widths.completed}%`, className: "numeric", render: row => <>{row.completed} / {row.failed}</> },
  ];
  return <section className="panel test-statistics" aria-busy={loading}>
    <div className="panel-head"><div><h2>{w("통계 요약", "Statistics Summary")}</h2><p className="ux-muted">{w("최대 3개 필드를 조합해 판정 비율과 참고 판정 대비 정확도를 확인합니다.", "Combine up to 3 fields to compare verdict distribution and reference accuracy.")}</p></div><button className="secondary" disabled={loading} onClick={() => setReload(value => value + 1)}>{w("다시 조회", "Refresh")}</button></div>
    <TestCaseFilter state={state} onChange={setState} official={run.evaluation_mode === "ground_truth"} />
    <form className="test-stat-controls" onSubmit={event => { event.preventDefault(); change({ q: state.draft.trim(), offset: 0 }); }}>
      <div className="test-stat-group-controls" role="group" aria-label={w("집계 기준 선택", "Grouping fields")}>
        {fields.map((field, index) => <div className="test-stat-group-select" key={index}>
          <label>{w("집계 기준", "Group by")}{index > 0 ? ` ${index + 1}` : ""}<select value={field} onChange={event => setState(current => changeGrouping(current, fields.map((value, i) => i === index ? event.target.value : value)))}>
            {statisticFields.map(([value, ko, en]) => <option key={value} value={value} disabled={value !== field && fields.includes(value)}>{w(ko, en)}</option>)}
          </select></label>
          {index > 0 && <button type="button" className="text-button test-stat-remove" aria-label={w(`집계 기준 ${index + 1} 제거`, `Remove grouping field ${index + 1}`)} onClick={() => setState(current => changeGrouping(current, fields.filter((_, i) => i !== index)))}>{w("제거", "Remove")}</button>}
        </div>)}
        <button type="button" className="secondary" disabled={fields.length === 3} onClick={() => setState(current => addGrouping(current))}>{w("기준 추가", "Add field")}</button>
      </div>
      <label className="test-stat-search">{w("항목 검색", "Search groups")}<input maxLength={500} value={state.draft} onChange={event => change({ draft: event.target.value })} placeholder={w("선택한 필드 값으로 검색", "Search selected field values")} /></label><button className="secondary" disabled={loading}>{w("검색", "Search")}</button>
    </form>
    {error && <p className="error" role="alert">{error}</p>}
    {!data && !error && <p role="status">{w("통계를 불러오는 중…", "Loading statistics…")}</p>}
    {data && <>
      <div className="test-stat-overview">
        <div><span>{data.case_filter_applied ? w("필터에 맞는 문항", "Matching cases") : w("분석 문항", "Accepted cases")}</span><strong>{data.summary.cases.toLocaleString()}</strong><small>{data.case_filter_applied ? `${w("필터 전", "Before filter")} ${data.scope_cases} · ` : ""}{w("완료", "Completed")} {data.summary.completed} · {w("실패", "Failed")} {data.summary.failed}</small></div>
        <div><span>{w("보류 비율", "Inconclusive rate")}</span><strong>{percent(data.summary.verdict_rates.inconclusive)}</strong><small>{data.summary.inconclusive} / {data.summary.distribution_total} {w("판정 완료", "completed verdicts")}</small></div>
        <div><span>{accuracyTitle}</span><strong>{percent(data.summary.accuracy)}</strong><small>{data.summary.matches} / {data.summary.comparable} {w("비교 가능 · 보류 포함", "compared · includes inconclusive")}</small></div>
      </div>
      <p className="test-stat-definition">{w("비율은 판정 완료 문항 기준입니다. 정확도는 보류를 포함한 답안 일치 ÷ 비교 가능 문항이며, 실행 실패·모의 실행 등 평가 제외 문항은 빼고 계산합니다.", "Distribution uses completed verdicts. Accuracy includes inconclusive answers: matches / comparable cases, excluding failures, stub runs and other ineligible results.")}</p>
      {data.case_filter_applied && <p className="test-stat-definition">{w("조건에 맞는 문항만 집계합니다. 오판만 선택하면 정확도는 0%이며, 전체 모델의 정확도를 뜻하지 않습니다. 접수 거부·중복 건수는 필터 전 범위입니다.", "Only matching cases are grouped. Selecting errors alone gives 0% accuracy, not the model's overall accuracy. Rejected and duplicate counts use the pre-filter scope.")}</p>}
      <p className="test-stat-basis">{w("답안 기준", "Reference basis")}: {w({ latest: "최신 참고 판정", initial: "접수 당시 기대 판정", saved: "저장한 평가", ground_truth: "공식 버전의 기대 판정" }[data.reference_basis] || "미확인", { latest: "Latest Reference Verdict", initial: "Expected verdict at admission", saved: "Saved evaluation", ground_truth: "Published version's expected verdict" }[data.reference_basis] || "Unknown")} · {w("접수 거부", "Rejected")} {data.rejected} · {w("중복 접수", "Duplicates")} {data.duplicates}</p>
      <DataTable className={`test-stat-table test-stat-fields-${fields.length}`} label={w("필드별 통계", "Statistics by field")} data={data.items} columns={columns} getRowId={row => JSON.stringify(fields.map(field => rowValues(row)[field]))} sorting={serverSorting(state.sort_by, state.sort_order)} onSortingChange={update => change(changedSort(update, serverSorting(state.sort_by, state.sort_order)))} empty={w("조건에 맞는 집계 항목이 없습니다.", "No groups match this scope.")} />
      <Pagination label={w("통계 항목 페이지", "Statistics pages")} pageSizes={[10, 25, 50, 100]} total={data.total_groups} limit={state.limit} offset={state.offset} disabled={loading} onOffsetChange={offset => change({ offset })} onLimitChange={limit => change({ limit, offset: 0 })} />
      <p className="ux-muted">{w("항목명을 누르면 해당 문항을 확인할 수 있습니다. 답안이 없거나 비교 가능한 문항이 없으면 정확도는 —로 표시합니다.", "Select a group to inspect its cases. Accuracy is — when no comparable reference answers exist.")}</p>
    </>}
  </section>;
}
