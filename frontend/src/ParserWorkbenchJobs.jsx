import { useState } from "react";
import { useConsolePreferences } from "./consolePreferences.jsx";
import DataTable from "./DataTable.jsx";
import Dialog from "./Dialog.jsx";
import Pagination from "./Pagination.jsx";
import TextInspector from "./TextInspector.jsx";
import { parserJobDiagnostics, parserJobFailure, parserJobKindLabel, parserJobStatusLabel,
  parserJobTimeline, parserJobMatchSummary } from "./parserWorkbenchJobs.js";

const statusToken = value => ["pending", "running", "processing", "completed", "failed", "canceled"].includes(value) ? value : "unrecorded";

function MatchDiagnostics({ job, english }) {
  const summary = parserJobMatchSummary(job), w = (ko, en) => english ? en : ko;
  if (!summary) return null;
  const kinds = { starts_with: w("시작 문자열", "Starting text"), json_equals: w("JSON 값", "JSON value"),
    json_string_exists: w("JSON 문자열 존재", "JSON string present"), marker_pair_exists: w("시작과 끝 표식", "Start and end markers"),
    regex_match: w("정규식", "Regular expression") };
  const complete = summary.source === "coverage";
  return <section className="pw-match-diagnostics" aria-label={w("형식 식별 조건 검사", "Format condition checks")}>
    <h4>{w("형식 식별 조건 검사", "Format condition checks")}</h4>
    {summary.group_index && <p>{w(`${summary.group_index}번째 형식의 마지막 실패 시도입니다.`, `Latest failed attempt for format ${summary.group_index}.`)}</p>}
    <p>{complete
      ? w(`제안용 입력 ${summary.sample_count}개 중 ${summary.checked_sample_count}개를 확인했습니다.`, `Checked ${summary.checked_sample_count} of ${summary.sample_count} authoring inputs.`)
      : w("이전 기록에 남은 검사만 표시합니다. 전체 입력의 일치 비율이 아닙니다.", "Only checks retained in the earlier record are shown, not match rates for all inputs.")}</p>
    {complete && summary.checked_sample_count < summary.sample_count && <p className="pw-inline-note">{
      w(`검사를 완료하지 못한 입력: ${summary.sample_count - summary.checked_sample_count}개.`, `Inputs not fully checked: ${summary.sample_count - summary.checked_sample_count}.`)}</p>}
    <ul>{summary.conditions.map(condition => <li key={condition.field}>
      <strong>{w(`조건 ${condition.condition_index + 1}`, `Condition ${condition.condition_index + 1}`)}: {kinds[condition.kind]}</strong>
      <span>{w(`확인한 입력 ${condition.checked_count}개 중 ${condition.matched_count}개 일치`, `${condition.matched_count} of ${condition.checked_count} checked inputs match`)}</span>
      <small>{w(`불일치 ${condition.unmatched_count}개 / 검사 오류 ${condition.error_count}개`, `${condition.unmatched_count} do not match / ${condition.error_count} check errors`)}</small>
    </li>)}</ul>
    <p className="pw-inline-note">{summary.mode === "all"
      ? w("모든 식별 조건이 맞아야 형식이 선택됩니다. 조건 일치는 HTTP 추출 성공을 뜻하지 않습니다.", "All conditions must match to select this format. A condition match does not mean HTTP extraction succeeded.")
      : summary.mode === "any"
        ? w("하나 이상의 식별 조건이 맞으면 형식이 선택됩니다. 개별 조건의 결과는 HTTP 추출 성공 여부와 다릅니다.", "At least one condition must match to select this format. Individual condition results do not indicate HTTP extraction success.")
        : w("개별 조건 검사이며, 형식 선택이나 HTTP 추출의 전체 성공률이 아닙니다.", "These are individual condition checks, not overall format selection or HTTP extraction success rates.")}</p>
    <p>{summary.has_match_failure
      ? w("입력별로 달라지는 URI나 헤더 값이 식별 조건에 들어갔는지 확인하세요. 포장 구조가 다른 입력은 형식을 나누세요.", "Check whether changing URI or header values were used as format conditions. Split inputs with different wrapper structures into separate formats.")
      : w("식별 조건과 별개로 HTTP 요청 구간의 추출이나 원문 위치 확인에 실패할 수 있습니다. 추출 설정을 확인하세요.", "HTTP extraction or source mapping can fail separately from format matching. Review the extraction settings.")}</p>
  </section>;
}

export function ParserWorkbenchJobDetails({ job, english = false }) {
  const w = (ko, en) => english ? en : ko;
  const failure = job?.status === "failed" ? parserJobFailure(job, english) : null;
  return <div className="pw-job-details">
    <div className="pw-job-heading"><strong>{parserJobKindLabel(job?.kind, english)}</strong>
      <span className="parser-job-state" data-status={statusToken(job?.status)}>{parserJobStatusLabel(job?.status, english)}</span></div>
    {failure && <><p>{failure.message}</p><p>{failure.action}</p></>}
    <MatchDiagnostics job={job} english={english} />
    <p className="pw-inline-note">{w("오류 코드와 검사 기록만 표시합니다. 원문과 모델 응답 본문은 포함하지 않습니다. 호출 예약 수는 실제 전송 횟수와 다를 수 있습니다.", "Only error codes and validation records are shown, not raw inputs or model response text. Call reservations may differ from actual transmissions.")}</p>
    <TextInspector value={parserJobDiagnostics(job)} label={w("기술 기록", "Technical record")} />
  </div>;
}

export default function ParserWorkbenchJobs({ jobs = [] }) {
  const { locale } = useConsolePreferences(), english = locale === "en";
  const w = (ko, en) => english ? en : ko;
  const [selected, setSelected] = useState(null), [offset, setOffset] = useState(0);
  const { latest, history } = parserJobTimeline(jobs), limit = 10;
  const selectedJob = selected?.id ? jobs.find(job => job.id === selected.id) || selected : selected;
  const currentOffset = Math.min(offset, Math.max(0, Math.ceil(history.length / limit) - 1) * limit);
  const time = job => {
    const parsed = Date.parse(job?.created_at);
    return Number.isFinite(parsed) ? new Date(parsed).toLocaleString(english ? "en" : "ko") : "—";
  };
  if (!latest.length && !history.length) return null;
  return <section className="pw-job-status" aria-label={w("모델 작업 상태", "Model job status")}>
    <h3>{w("모델 작업", "Model jobs")}</h3>
    <div className="pw-current-jobs">{latest.map((job, index) => {
      const failure = job.status === "failed" ? parserJobFailure(job, english) : null;
      return <article className={failure ? "pw-warning pw-current-job" : "pw-current-job"}
        key={job.id || `${job.kind}:${index}`} role={failure ? "alert" : undefined}>
        <div className="pw-job-heading"><div><strong>{parserJobKindLabel(job.kind, english)}</strong>
          <span className="parser-job-state" data-status={statusToken(job.status)}>{parserJobStatusLabel(job.status, english)}</span>
        </div><button type="button" className="text-button" onClick={() => setSelected(job)}
          aria-label={`${parserJobKindLabel(job.kind, english)} ${w("작업 상세", "job details")}`}>
          {w("작업 상세", "Job details")}</button></div>
        {failure && <><p>{failure.message}</p><p className="pw-job-next">{failure.action}</p>
          {failure.correction_count !== null && <small>{w("교정 시도", "Correction attempts")} {failure.correction_count}{w("회", "")}</small>}
        </>}
      </article>;
    })}</div>
    {history.length > 0 && <details className="pw-job-history"><summary>{w("이전 작업 이력", "Previous jobs")} ({history.length})</summary>
      <p className="pw-inline-note">{w("이전 실패는 이력에 보관되며 현재 작업의 실패로 표시하지 않습니다.", "Earlier failures remain in history and are not shown as failures of the current job.")}</p>
      <DataTable label={w("이전 모델 작업", "Previous model jobs")} data={history.slice(currentOffset, currentOffset + limit)}
        getRowId={(row, index) => row.id || `${row.kind}:${row.created_at}:${index}`} columns={[
          { id: "kind", header: w("작업", "Job"), render: row => <button type="button" className="text-button"
            onClick={() => setSelected(row)}>{parserJobKindLabel(row.kind, english)}</button> },
          { id: "status", header: w("상태", "Status"), render: row => <span className="parser-job-state" data-status={statusToken(row.status)}>{parserJobStatusLabel(row.status, english)}</span> },
          { id: "cause", header: w("기록", "Record"), render: row => row.status === "failed" ? parserJobFailure(row, english).message : "—" },
          { id: "created", header: w("요청 시각", "Requested"), render: time },
        ]} />
      <Pagination total={history.length} limit={limit} offset={currentOffset} onOffsetChange={setOffset}
        label={w("이전 모델 작업 페이지", "Previous model job pages")} />
    </details>}
    <Dialog open={Boolean(selected)} title={w("모델 작업 상세", "Model job details")} onClose={() => setSelected(null)}
      className="parser-dialog">{selectedJob && <ParserWorkbenchJobDetails job={selectedJob} english={english} />}</Dialog>
  </section>;
}
