import { useState } from "react";
import { useWords } from "./LifecycleViews.jsx";
import { caseFilterFields, emptyOperator, fieldDefinition, filterFieldName, filterOptionName, filterOperators, filterRuleCount, formatCaseFilter, newCaseFilter, newFilterRule, normalizeCaseFilter, operatorsFor, presetCaseFilter } from "./testCaseFilters.js";

export default function TestCaseFilter({ state, onChange, official = false }) {
  const w = useWords(), [error, setError] = useState("");
  const draft = state.filter_draft || state.case_filter || newCaseFilter();
  const update = next => { setError(""); onChange(current => ({ ...current, filter_draft: next })); };
  const groupChange = (index, change) => update({ ...draft, rules: draft.rules.map((group, i) => i === index ? { ...group, ...change } : group) });
  const ruleChange = (groupIndex, ruleIndex, change) => groupChange(groupIndex, { rules: draft.rules[groupIndex].rules.map((rule, i) => i === ruleIndex ? { ...rule, ...change } : rule) });
  const apply = expression => { setError(""); onChange(current => ({ ...current, case_filter: expression, filter_draft: expression, offset: 0 })); };
  const summary = formatCaseFilter(state.case_filter, w, official), count = filterRuleCount(draft);
  return <section className="test-case-filter" aria-label={w("집계 전 필터", "Filter before grouping")}>
    <div className="test-filter-head"><h3>{w("집계 전 필터", "Filter before grouping")}</h3><button type="button" className="text-button" aria-expanded={Boolean(state.filter_open)} onClick={() => onChange(current => ({ ...current, filter_open: !current.filter_open }))}>{state.filter_open ? w("편집 닫기", "Close editor") : w("조건 편집", "Edit conditions")}</button></div>
    <div className="test-filter-presets" role="group" aria-label={w("빠른 필터", "Quick filters")}>
      {[["all", "전체", "All"], ["false_positive", "과탐 방향", "False alarms"], ["false_negative", "미탐 방향", "Missed attacks"], ["different", "판정 불일치", "Verdict mismatches"]].map(([key, ko, en]) => <button type="button" className="secondary small" key={key} aria-pressed={JSON.stringify(state.case_filter || null) === JSON.stringify(presetCaseFilter(key))} onClick={() => apply(presetCaseFilter(key))}>{w(ko, en)}</button>)}
    </div>
    <p className="test-filter-summary" title={summary}>{summary || w("조건 없이 선택 범위 전체를 집계합니다.", "All cases in the selected scope are grouped.")}</p>
    {state.filter_open && <form className="test-filter-editor" onSubmit={event => { event.preventDefault(); try { apply(normalizeCaseFilter(draft)); } catch { setError(w("필드·조건·값을 확인해 주세요. 최대 5개 묶음, 총 20개 조건까지 가능합니다.", "Check fields, operators and values. Use up to 5 groups and 20 conditions.")); } }}>
      <label className="test-filter-join">{w("묶음 연결", "Combine groups")}<select value={draft.op} onChange={event => update({ ...draft, op: event.target.value })}><option value="and">{w("모두 일치 (AND)", "Match all (AND)")}</option><option value="or">{w("하나 이상 일치 (OR)", "Match any (OR)")}</option></select></label>
      {draft.rules.map((group, g) => <fieldset className="test-filter-group" key={g}><legend>{w(`조건 묶음 ${g + 1}`, `Condition group ${g + 1}`)}</legend>
        <div className="test-filter-group-head"><label>{w("묶음 안 조건", "Conditions within group")}<select value={group.op} onChange={event => groupChange(g, { op: event.target.value })}><option value="and">{w("모두 일치 (AND)", "Match all (AND)")}</option><option value="or">{w("하나 이상 일치 (OR)", "Match any (OR)")}</option></select></label><button type="button" className="text-button" disabled={draft.rules.length === 1} onClick={() => update({ ...draft, rules: draft.rules.filter((_, i) => i !== g) })}>{w("묶음 제거", "Remove group")}</button></div>
        {group.rules.map((rule, r) => {
          const field = fieldDefinition(rule.field), position = `${g + 1}.${r + 1}`;
          return <div className="test-filter-rule" key={r}>
            <label>{w("필드", "Field")}<select aria-label={w(`조건 ${position} 필드`, `Condition ${position} field`)} value={rule.field} onChange={event => ruleChange(g, r, newFilterRule(event.target.value))}>{caseFilterFields.map(field => <option key={field.key} value={field.key}>{filterFieldName(field, w, official)}</option>)}</select></label>
            <label>{w("조건", "Operator")}<select aria-label={w(`조건 ${position} 연산`, `Condition ${position} operator`)} value={rule.operator} onChange={event => ruleChange(g, r, { operator: event.target.value })}>{operatorsFor(field).map(operator => <option key={operator} value={operator}>{w(...filterOperators[operator])}</option>)}</select></label>
            {emptyOperator(rule.operator) ? <span className="ux-muted test-filter-no-value">{w("값 입력 불필요", "No value needed")}</span> : <label>{w("값", "Value")}{field.type === "enum"
              ? <select aria-label={w(`조건 ${position} 값`, `Condition ${position} value`)} value={rule.value ?? ""} onChange={event => ruleChange(g, r, { value: event.target.value })}><option value="" disabled>{w("선택", "Select")}</option>{field.options.map(option => <option key={option[0]} value={option[0]}>{filterOptionName(field, option, w, official)}</option>)}</select>
              : <input aria-label={w(`조건 ${position} 값`, `Condition ${position} value`)} type={field.type === "number" ? "number" : "text"} min={field.min} max={field.max} step={field.integer ? "1" : "any"} maxLength={4096} value={rule.value ?? ""} placeholder={w("조건 값 입력", "Enter a value")} onChange={event => ruleChange(g, r, { value: event.target.value })} />}</label>}
            <button type="button" className="text-button" aria-label={w(`조건 ${position} 제거`, `Remove condition ${position}`)} disabled={group.rules.length === 1} onClick={() => groupChange(g, { rules: group.rules.filter((_, i) => i !== r) })}>{w("제거", "Remove")}</button>
          </div>;
        })}
        <button type="button" className="secondary small" disabled={count >= 20} onClick={() => groupChange(g, { rules: [...group.rules, newFilterRule()] })}>{w("조건 추가", "Add condition")}</button>
      </fieldset>)}
      <div className="test-filter-actions"><button type="button" className="secondary" disabled={draft.rules.length >= 5 || count >= 20} onClick={() => update({ ...draft, rules: [...draft.rules, { op: "and", rules: [newFilterRule()] }] })}>{w("묶음 추가", "Add group")}</button><button type="submit" className="primary">{w("필터 적용", "Apply filter")}</button><small>{w("편집 중에는 집계가 바뀌지 않습니다.", "Editing does not change the results until applied.")}</small></div>
      {error && <p className="error" role="alert">{error}</p>}
    </form>}
    <p className="test-filter-note">{official ? w("과탐: 기대 오탐 → AI 정탐 · 미탐: 기대 정탐 → AI 오탐. 빠른 필터는 공식 답안 기준의 평가 대상 문항만 선택합니다. 보류 차이는 판정 불일치에 포함합니다.", "False alarm: expected false positive → AI true positive. Miss: expected true positive → AI false positive. Quick filters use the fixed official answers; mismatches include inconclusive differences.") : w("과탐: 참고 오탐 → AI 정탐 · 미탐: 참고 정탐 → AI 오탐. 빠른 필터는 평가 대상 문항만 선택합니다. 보류 차이는 판정 불일치에 포함합니다.", "False alarm: reference false positive → AI true positive. Miss: reference true positive → AI false positive. Quick filters use evaluable cases; mismatches also include inconclusive differences.")}</p>
  </section>;
}
