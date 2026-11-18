import { useState } from "react";
import { useConsolePreferences } from "./consolePreferences.jsx";
import DataTable from "./DataTable.jsx";
import Dialog from "./Dialog.jsx";
import Pagination from "./Pagination.jsx";
import TextInspector from "./TextInspector.jsx";
import { parserJobDiagnostics, parserJobFailure, parserJobKindLabel, parserJobStatusLabel,
  parserJobTimeline } from "./parserWorkbenchJobs.js";

const statusToken = value => ["pending", "running", "processing", "completed", "failed", "canceled"].includes(value) ? value : "unrecorded";

export function ParserWorkbenchJobDetails({ job, english = false }) {
  const w = (ko, en) => english ? en : ko;
  const failure = job?.status === "failed" ? parserJobFailure(job, english) : null;
  return <div className="pw-job-details">
    <div className="pw-job-heading"><strong>{parserJobKindLabel(job?.kind, english)}</strong>
      <span className="parser-job-state" data-status={statusToken(job?.status)}>{parserJobStatusLabel(job?.status, english)}</span></div>
    {failure && <><p>{failure.message}</p><p>{failure.action}</p></>}
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
