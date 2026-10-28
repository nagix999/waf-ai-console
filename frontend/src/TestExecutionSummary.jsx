import { useId } from "react";
import DataTable from "./DataTable.jsx";
import { useWords } from "./LifecycleViews.jsx";
import { executionSummary, parserWarningLabel, verifierReasonLabel } from "./testExecutionSummary.js";

const count = (value, words) => value === null ? words("미기록", "Not recorded") : value.toLocaleString();

export default function TestExecutionSummary({ summary, words: suppliedWords }) {
  const contextWords = useWords(), w = suppliedWords || contextWords, headingId = useId();
  const data = executionSummary(summary);
  if (!data) return <section className="test-execution-summary" aria-labelledby={headingId}>
    <h3 id={headingId}>{w("추가 검증 현황", "Additional Verification")}</h3>
    <p className="test-execution-note">{w("이 실행에는 추가 검증 통계가 제공되지 않습니다.", "Additional verification statistics are not available for this run.")}</p>
  </section>;
  const { parser, verifier } = data;
  const metrics = [
    [w("추가 검증 단계", "Additional verification stage"), verifier.executed, w("진입한 문항 수", "Cases entering this stage")],
    [w("파싱 사유 포함", "Includes a parsing reason"), verifier.parser_triggered, `${w("다른 사유와 함께", "With other reasons")} ${count(verifier.parser_with_other, w)}`],
    [w("파싱 사유만", "Parsing reason only"), verifier.parser_only, w("추가 검증 단계에 진입한 문항 기준", "Among cases entering verification")],
  ];
  const boundaries = [
    [w("전체 문항", "All cases"), data.cases],
    [w("검증 안 함", "Verification not required"), verifier.not_executed],
    [w("검증 결정 후 미진입", "Required, stage not entered"), verifier.awaiting_execution],
    [w("검증 기록 없음", "No verification record"), verifier.unrecorded],
    [w("모의 실행 등 제외", "Stub runs, etc. excluded"), verifier.not_applicable],
  ];
  const states = [
    ["success", w("성공", "Success")], ["partial", w("일부 미인식", "Partially recognized")],
    ["failed", w("인식 실패", "Not recognized")], ["unrecorded", w("기록 없음", "Not recorded")],
  ];
  const reasonColumns = [
    { id: "reason", header: w("검증 사유", "Verification reason"), width: "49%", render: row => verifierReasonLabel(row.code, w) },
    { id: "cases", header: w("포함 건수", "Cases"), width: "15%", className: "numeric", render: row => count(row.cases, w) },
    { id: "only_cases", header: w("단독", "Only reason"), width: "15%", className: "numeric", render: row => count(row.only_cases, w) },
    { id: "with_other_cases", header: w("다른 사유와 함께", "With other reasons"), width: "21%", className: "numeric", render: row => count(row.with_other_cases, w) },
  ];
  const warningColumns = [
    { id: "warning", header: w("파싱 경고", "Parsing warning"), width: "75%", render: row => parserWarningLabel(row.code, w) },
    { id: "cases", header: w("문항", "Cases"), width: "25%", className: "numeric", render: row => count(row.cases, w) },
  ];
  const parserRecorded = [parser.success, parser.partial, parser.failed].every(value => value !== null)
    ? parser.success + parser.partial + parser.failed : null;
  const noWarningMessage = parser.warnings_recorded === 0 && parserRecorded > 0
    ? w("이전 실행에는 경고별 기록이 없습니다.", "Older executions do not have per-warning records.")
    : parser.warnings_recorded > 0 ? w("기록된 문항에서는 파싱 경고가 없습니다.", "No parsing warnings in the recorded cases.")
      : w("파싱 경고 기록이 없습니다.", "No parsing warning records.");
  return <section className="test-execution-summary" aria-labelledby={headingId}>
    <header className="test-execution-head"><h3 id={headingId}>{w("추가 검증 현황", "Additional Verification")}</h3>
      <p className="test-execution-note">{w("선택한 문항 조건에 맞는 전체 범위입니다. 집계 기준·항목 검색·페이지 변경에는 영향을 받지 않습니다.", "Covers all cases matching the selected case filters. Grouping, group search and pagination do not change this scope.")}</p>
    </header>
    <dl className="test-execution-metrics">{metrics.map(([label, value, note]) => <div key={label}><dt>{label}</dt><dd className={value === null ? "is-unrecorded" : undefined}>{count(value, w)}</dd><small>{note}</small></div>)}</dl>
    <dl className="test-execution-boundaries">{boundaries.map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{count(value, w)}</dd></div>)}</dl>
    <p className="test-execution-note">{w("단계 진입 문항 수이며 HTTP 전송·재시도 횟수는 아닙니다. 파싱 사유만 있다고 안전하게 검증을 생략할 수 있는 것은 아닙니다.", "These are cases entering a stage, not HTTP requests or retries. A parsing-only reason does not mean verification can safely be skipped.")}</p>
    <div className="test-execution-details">
      <div className="test-execution-reasons">
        <h4>{w("이유별 추가 검증", "Verification by Reason")}</h4>
        <p className="test-execution-note">{w("한 문항에 사유가 여러 개면 각각 집계하므로 합계가 검증 문항 수를 넘을 수 있습니다.", "A case can have multiple reasons, so reason totals can exceed the number of verified cases.")}</p>
        {verifier.reasons === null ? <p className="test-execution-empty">{w("검증 사유별 기록이 제공되지 않습니다.", "Per-reason records are not available.")}</p> : <DataTable
          className="test-execution-reason-table" label={w("이유별 추가 검증 문항 수", "Verification case counts by reason")}
          data={verifier.reasons} columns={reasonColumns} getRowId={row => row.rowKey}
          empty={verifier.executed > 0 ? w("추가 검증 단계 진입은 확인되지만 사유별 기록이 없습니다.", "Verification stage entry is recorded, but its reasons are not.")
            : w("기록된 추가 검증 사유가 없습니다.", "No recorded verification reasons.")} />}
        {verifier.reasons_unrecorded > 0 && <p className="test-execution-note">{w("검증 사유 미기록", "Verification reasons not recorded")} {count(verifier.reasons_unrecorded, w)}</p>}
      </div>
      <div className="test-execution-parser">
        <h4>{w("HTTP 구조 인식", "HTTP Structure Recognition")}</h4>
        <dl className="test-execution-parser-states">{states.map(([key, label]) => <div key={key} className={`test-execution-parser-${key}`}><dt>{label}</dt><dd>{count(parser[key], w)}</dd></div>)}</dl>
        <p className="test-execution-note">{w("구조 인식 수준이며 공격 여부나 로그 완전성의 점수가 아닙니다. 모의 실행도 실제 수행한 파싱 상태는 포함합니다.", "This measures structure recognition, not attack detection or capture completeness. Actual parsing in stub runs is included.")}</p>
        <div className="test-execution-warning-head"><h4>{w("파싱 경고", "Parsing Warnings")}</h4>
          <span>{w("기록", "Recorded")} {count(parser.warnings_recorded, w)} · {w("미기록", "Not recorded")} {count(parser.warnings_unrecorded, w)}</span>
        </div>
        {parser.warnings === null ? <p className="test-execution-empty">{w("경고별 통계가 제공되지 않습니다.", "Per-warning statistics are not available.")}</p> : <DataTable
          className="test-execution-warning-table" label={w("파싱 경고별 문항 수", "Case counts by parsing warning")}
          data={parser.warnings} columns={warningColumns} getRowId={row => row.rowKey} empty={noWarningMessage} />}
        {parser.warnings_unrecorded !== 0 && <p className="test-execution-note">{w("미기록 문항은 경고 없음으로 계산하지 않습니다.", "Missing records are not counted as warning-free.")}</p>}
      </div>
    </div>
  </section>;
}
