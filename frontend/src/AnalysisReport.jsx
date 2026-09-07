import { useMemo, useState } from "react";
import { buildAnalysisReport, decodeReportText } from "./analysisReport.js";
import { parseApiDocument } from "./apiDocument.js";
import { Icon } from "./Icon.jsx";
import "./analysisReport.css";

// Only the fixed template supplies Markdown structure. Dynamic values are
// escaped by the generator, decoded once, and rendered as React text. Never
// interpret embedded HTML, links, images, or model-generated Markdown.
function ReportBlocks({ blocks, section }) {
  return blocks.map((block, index) => {
    // The generator appends exactly one LF to separate the excerpt from its
    // closing fence. Remove only that separator, preserving original CR/LF.
    if (block.type === "code") return <pre key={index} className="report-excerpt" tabIndex={0} aria-label={`${section} 원문 발췌`}><code>{block.text.endsWith("\n") ? block.text.slice(0, -1) : block.text}</code></pre>;
    if (block.type === "table") return <div key={index} className="report-table"><table><caption className="sr-only">{section}</caption><thead><tr>{block.headers.map((cell, i) => <th key={i} scope="col">{decodeReportText(cell)}</th>)}</tr></thead><tbody>{block.rows.map((row, i) => <tr key={i}>{row.map((cell, j) => <td key={j}>{decodeReportText(cell)}</td>)}</tr>)}</tbody></table></div>;
    if (block.type === "list") {
      const Tag = block.ordered ? "ol" : "ul";
      return <Tag key={index} {...(block.ordered ? { start: block.start } : {})}>{block.items.map((item, i) => <li key={i}>{decodeReportText(item)}</li>)}</Tag>;
    }
    if (block.type === "heading") return <h4 key={index}>{decodeReportText(block.text)}</h4>;
    return <p key={index}>{decodeReportText(block.text)}</p>;
  });
}

export default function AnalysisReport({ detail, decoding = null, mode, onModeChange }) {
  const [includeAppendix, setIncludeAppendix] = useState(false);
  const markdown = useMemo(() => buildAnalysisReport(detail, { decoding, includeAppendix }), [detail, decoding, includeAppendix]);
  const report = useMemo(() => parseApiDocument(markdown), [markdown]);
  return <section className="analysis-report" aria-label="보고서 보기 설정">
    <div className="panel report-toolbar">
      <div className="report-toolbar-title"><span className="report-icon"><Icon name="file" size={22} /></span><div><h2>분석 보고서</h2><p>판정 근거와 추가로 확인할 내용을 정리합니다.</p></div></div>
      <div className="report-mode" role="group" aria-label="보고서 표시 방식">
        <button type="button" aria-pressed={mode === "preview"} aria-controls="analysis-report-content" onClick={() => onModeChange("preview")}><Icon name="file" size={15} />보고서 보기</button>
        <button type="button" aria-pressed={mode === "source"} aria-controls="analysis-report-content" onClick={() => onModeChange("source")}><span aria-hidden="true">MD</span>Markdown 원본</button>
      </div>
      <label className="report-appendix-option"><input type="checkbox" checked={includeAppendix} onChange={(event) => setIncludeAppendix(event.target.checked)} />평가·실행 부록 포함<small>참고 답안 비교와 기술정보가 보고서 및 Markdown 원본에 함께 포함됩니다.</small></label>
    </div>
    <p className="report-scope"><Icon name="shield" size={15} />추가 LLM 호출이나 원문 자동 조회 없이 작성됩니다. 원문 탭에서 조회한 디코딩 후보는 원문·변환 단계와 함께 포함됩니다. HTTP 전체 원문과 Agent 단계 입출력 전체는 포함하지 않습니다.</p>
    <div id="analysis-report-content">
      {mode === "source" ? <div className="panel report-source"><div className="report-source-head"><span>MARKDOWN SOURCE</span><small>읽기 전용 · 보고서 보기와 동일한 내용</small></div><pre tabIndex={0} aria-label="보고서 Markdown 원본"><code>{markdown}</code></pre></div> : <article className="panel report-paper" aria-label="분석 보고서">
        <header className="report-cover"><span className="eyebrow">WAF AI · ANALYSIS REPORT</span><h2>{decodeReportText(report.title)}</h2><ReportBlocks blocks={report.intro} section="보고서 안내" /></header>
        {report.sections.map((section, index) => <section className="report-section" key={section.id} aria-labelledby={`report-section-${index}`}><div className="report-section-heading"><span aria-hidden="true">{String(index + 1).padStart(2, "0")}</span><h3 id={`report-section-${index}`}>{decodeReportText(section.title)}</h3></div><ReportBlocks blocks={section.blocks} section={section.title} /></section>)}
      </article>}
    </div>
  </section>;
}
