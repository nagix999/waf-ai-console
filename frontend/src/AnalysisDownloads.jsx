import { useEffect, useRef, useState } from "react";
import { Icon } from "./Icon.jsx";
import HelpTooltip from "./HelpTooltip.jsx";
import { fetchReportFile, reportDownloadError, saveReportFile } from "./analysisDownloads.js";
import "./analysisDownloads.css";

export default function AnalysisDownloads({ id, status, onUnauthorized }) {
  const [busy, setBusy] = useState("");
  const [message, setMessage] = useState("");
  const current = useRef(null);
  useEffect(() => {
    setBusy(""); setMessage("");
    return () => { current.current?.abort(); current.current = null; };
  }, [id]);
  async function download(format) {
    if (busy || current.current || status !== "completed") return;
    const controller = new AbortController(); current.current = controller;
    setBusy(format); setMessage("");
    try {
      const blob = await fetchReportFile(id, format, { signal: controller.signal });
      if (!controller.signal.aborted && current.current === controller) saveReportFile(blob, id, format);
    } catch (error) {
      if (!controller.signal.aborted && current.current === controller) {
        setMessage(reportDownloadError(error));
        if (error.status === 401) onUnauthorized?.();
      }
    } finally {
      if (current.current === controller) { current.current = null; setBusy(""); }
    }
  }
  return <div className="analysis-downloads">
    <div className="analysis-download-actions" role="group" aria-label="분석 보고서 다운로드">
      <button type="button" className="secondary small" disabled={!!busy || status !== "completed"} onClick={() => download("pdf")}><Icon name="file" size={15} />{busy === "pdf" ? "PDF 작성 중…" : "PDF 다운로드"}</button>
      <button type="button" className="secondary small" disabled={!!busy || status !== "completed"} onClick={() => download("xlsx")}><Icon name="file" size={15} />{busy === "xlsx" ? "Excel 작성 중…" : "Excel 다운로드"}</button>
      <span className="download-scope">근거 발췌 포함<HelpTooltip label="다운로드 범위">현재 분석 1건의 최종 판정·근거·답안 비교·소요 시간·추가 확인을 담습니다. HTTP 전체 원문·디코딩 자료·Agent 입출력과 기술 부록은 제외합니다. 근거 발췌에는 내부 정보가 포함될 수 있습니다. 다운로드 요청은 감사 이력에 남습니다.<br />출력 한도: 본문 512,000바이트·1,000항목·PDF 80페이지·파일 10 MiB. 초과하면 부분 파일을 만들지 않습니다.</HelpTooltip></span>
    </div>
    {status !== "completed" && <small className="download-status">분석이 완료되면 보고서를 내려받을 수 있습니다.</small>}
    {busy && <small className="download-status" role="status">저장된 결과로 파일을 작성하고 있습니다. 새 분석은 실행하지 않습니다.</small>}
    {message && <p className="download-error" role="alert">{message}</p>}
  </div>;
}
