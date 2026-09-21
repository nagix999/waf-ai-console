import { useEffect, useRef, useState } from "react";
import { Icon } from "./Icon.jsx";
import HelpTooltip from "./HelpTooltip.jsx";
import { fetchReportFile, reportDownloadError, saveReportFile } from "./analysisDownloads.js";
import "./analysisDownloads.css";
import { ConsolePopover } from "./ConsoleShell.jsx";

export function DownloadScope() {
  return <span className="download-scope">근거 발췌 포함<HelpTooltip label="다운로드 범위">현재 분석 1건을 출력합니다. PDF는 웹 보고서의 서식·선택한 부록·조회한 디코딩을 반영합니다. PDF는 다운로드 시점의 저장 결과와 최신 답안을 사용하므로 다른 창에서 답안을 수정했다면 화면을 새로고침하세요. Excel은 기본 항목으로 출력합니다. HTTP 전체 원문과 Agent 입출력은 제외합니다. 근거 발췌에는 내부 정보가 포함될 수 있습니다. 다운로드 요청은 감사 이력에 남습니다.<br />출력 한도: 본문 512,000바이트·1,000항목·PDF 80페이지·파일 10 MiB. 초과하면 부분 파일을 만들지 않습니다.</HelpTooltip></span>;
}

export default function AnalysisDownloads({ id, status, onUnauthorized, includeAppendix = false, includeDecoding = false }) {
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
      const blob = await fetchReportFile(id, format, { signal: controller.signal,
        pdfOptions: { includeAppendix, includeDecoding, theme: document.documentElement.dataset.theme } });
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
      <ConsolePopover label="보고서 다운로드" disabled={!!busy || status !== "completed"} trigger={<><Icon name="file" size={15} />{busy ? "작성 중…" : "다운로드"}<Icon name="chevronDown" size={14} /></>}>{close => <>
        <button type="button" onClick={() => { close(); download("pdf"); }}>PDF 다운로드</button>
        <button type="button" onClick={() => { close(); download("xlsx"); }}>Excel 다운로드</button>
        <DownloadScope />
      </>}</ConsolePopover>
    </div>
    {status !== "completed" && <small className="download-status">분석이 완료되면 보고서를 내려받을 수 있습니다.</small>}
    {busy && <small className="download-status" role="status">저장된 결과로 파일을 작성하고 있습니다. 새 분석은 실행하지 않습니다.</small>}
    {message && <p className="download-error" role="alert">{message}</p>}
  </div>;
}
