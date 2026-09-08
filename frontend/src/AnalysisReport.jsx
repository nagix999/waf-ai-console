import { useId, useMemo, useRef, useState } from "react";
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
  const reportId = useId();
  const top = useRef(null);
  const sections = useRef([]);
  const markdown = useMemo(() => buildAnalysisReport(detail, { decoding, includeAppendix }), [detail, decoding, includeAppendix]);
  const report = useMemo(() => parseApiDocument(markdown), [markdown]);
  function jumpTo(index) { const target = index === "top" ? top.current : sections.current[Number(index)]; target?.scrollIntoView({ behavior: "auto", block: "start" }); target?.focus({ preventScroll: true }); }
  return <section className="analysis-report" aria-label="보고서 보기 설정" ref={top} tabIndex={-1}>
    <div className="panel report-toolbar">
      <div className="report-toolbar-title"><span className="report-icon"><Icon name="file" size={22} /></span><h2>분석 보고서</h2></div>
      <div className="report-mode" role="group" aria-label="보고서 표시 방식">
        <button type="button" aria-pressed={mode === "preview"} aria-controls={reportId + "-content"} onClick={() => onModeChange("preview")}><Icon name="file" size={15} />보고서 보기</button>
        <button type="button" aria-pressed={mode === "source"} aria-controls={reportId + "-content"} onClick={() => onModeChange("source")}><span aria-hidden="true">MD</span>Markdown 원본</button>
      </div>
      <div className="report-appendix-option"><label><input type="checkbox" checked={includeAppendix} onChange={(event) => setIncludeAppendix(event.target.checked)} />평가·실행 부록 포함</label><small className="ux-muted">부록·조회한 디코딩은 화면과 Markdown에만 포함 · PDF·Excel 제외</small></div>
    </div>
    <div className="report-reading-tools"><span className="report-scope-note">근거 발췌 포함</span>{mode !== "source" && <label className="report-jump">목차<select aria-label="보고서 목차" value="" onChange={(event) => { if (event.target.value) jumpTo(event.target.value); }}><option value="">항목으로 이동</option>{report.sections.map((section, index) => <option key={section.id} value={String(index)}>{decodeReportText(section.title)}</option>)}</select></label>}</div>
    <div id={reportId + "-content"}>
      {mode === "source" ? <div className="panel report-source"><div className="report-source-head"><span>Markdown 원본</span><small>읽기 전용 · 보고서 보기와 동일한 내용</small></div><pre tabIndex={0} aria-label="보고서 Markdown 원본"><code>{markdown}</code></pre></div> : <article className="panel report-paper" aria-label="분석 보고서">
        <header className="report-cover"><span className="eyebrow">WAF AI · 분석 보고서</span><h2>{decodeReportText(report.title)}</h2><ReportBlocks blocks={report.intro} section="보고서 안내" /></header>
        {report.sections.map((section, index) => <section className="report-section" ref={(node) => { sections.current[index] = node; }} tabIndex={-1} key={section.id} aria-labelledby={reportId + "-section-" + index}><div className="report-section-heading"><span aria-hidden="true">{String(index + 1).padStart(2, "0")}</span><h3 id={reportId + "-section-" + index}>{decodeReportText(section.title)}</h3></div><ReportBlocks blocks={section.blocks} section={section.title} /><button type="button" className="text-button report-back-top" onClick={() => jumpTo("top")}>목차로</button></section>)}
      </article>}
    </div>
  </section>;
}
