import { useId, useState } from "react";
import TextInspector from "./TextInspector.jsx";
import { completionReason, diagnosticCount, modelCheckNames, modelValidationError } from "./modelCheckDiagnostics.js";
import "./modelCheckDetail.css";

export default function ModelCheckDetail({ check }) {
  const [technical, setTechnical] = useState(false);
  const technicalId = useId();
  if (!check) return <p className="empty">기록된 검증 항목이 없습니다.</p>;
  const records = Array.isArray(check.response_diagnostics) ? check.response_diagnostics.filter(item => item && typeof item === "object").slice(0, 10) : null;
  return <section className="model-check-detail" aria-label="검증 항목 내용">
    <h3>{modelCheckNames[check.name] || "기타 확인"} · {check.status === "passed" ? "통과" : check.status === "failed" ? "실패" : "상태 미확인"}</h3>
    {check.status === "failed" && <p className="error">{modelValidationError(check.error_code)}</p>}
    {!!records?.length && <>
      <p className="ux-muted">이 검증에서 요청한 한도와 서버가 반환한 사용량입니다. 단위는 토큰이며, 반환되지 않은 값은 ‘미기록’으로 표시합니다.</p>
      <div className="model-check-table" role="region" aria-label="요청별 토큰과 종료 사유" tabIndex={0}>
        <table><thead><tr><th scope="col">요청</th><th scope="col">출력 한도</th><th scope="col">입력 사용량</th><th scope="col">출력 사용량</th><th scope="col">종료 사유</th></tr></thead>
          <tbody>{records.map((record, index) => <tr key={index}><th scope="row">{diagnosticCount(record.request_index)}</th><td>{diagnosticCount(record.requested_max_output_tokens)}</td><td>{diagnosticCount(record.prompt_tokens)}</td><td>{diagnosticCount(record.completion_tokens)}</td><td>{completionReason(record)}</td></tr>)}</tbody>
        </table>
      </div>
    </>}
    {check.name !== "models" && !records?.length && <p className="ux-muted">{records ? "이 항목에서 기록된 LLM 요청이 없습니다." : "기존 기록에는 요청별 토큰·종료 정보가 없습니다. 새로 검증하면 기록됩니다."}</p>}
    <button type="button" className="secondary" aria-expanded={technical} aria-controls={technicalId} onClick={() => setTechnical(value => !value)}>{technical ? "기술 기록 닫기" : "기술 기록"}</button>
    <div id={technicalId} hidden={!technical}>{technical && <TextInspector label="검증 항목 기록" value={check} />}</div>
  </section>;
}
