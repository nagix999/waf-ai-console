import { Table } from "./DataTable.jsx";
import { useId, useMemo, useRef, useState } from "react";
import { buildAnalysisReport, decodeReportText } from "./analysisReport.js";
import { parseApiDocument } from "./apiDocument.js";
import { Icon } from "./Icon.jsx";
import { useR5Words } from "./R5Evaluation.jsx";
import "./analysisReport.css";

// Only the fixed template supplies Markdown structure. Dynamic values are
// escaped by the generator, decoded once, and rendered as React text. Never
// interpret embedded HTML, links, images, or model-generated Markdown.
function ReportBlocks({ blocks, section }) {
  const tiers = blocks.findIndex(block => block.type === "heading" && block.level === 3 && ["핵심 근거", "나머지 근거"].includes(block.text));
  if (tiers >= 0) {
    const groups = [];
    for (const block of blocks.slice(tiers)) {
      if (block.type === "heading" && block.level === 3) groups.push({ heading: block.text, blocks: [] });
      else groups.at(-1).blocks.push(block);
    }
    return <><ReportBlocks blocks={blocks.slice(0, tiers)} section={section} />{groups.map((group, index) => <div className="report-evidence-tier" key={index}><h4>{decodeReportText(group.heading)}</h4><ReportBlocks blocks={group.blocks} section={group.heading} /></div>)}</>;
  }
  // Only fixed-template level-three headings form evidence groups. Untrusted
  // model values remain escaped text, never Markdown structure or CSS names.
  const isGroup = block => block.type === "heading" && (block.level === 3 && ["공격 요청 근거", "정상 요청 근거", "정탐 근거", "오탐 근거", "반대 근거", "공격 해석", "정상 해석", "참고 내용", "참고내용"].includes(block.text)
    || block.level === 4 && /^근거 [1-9][0-9]? — (공격 요청 근거|정상 요청 근거|참고 내용|구분 미기록)$/.test(block.text));
  const firstGroup = blocks.findIndex(isGroup);
  if (firstGroup >= 0) {
    const groups = [];
    for (const block of blocks.slice(firstGroup)) {
      if (isGroup(block)) groups.push({ heading: block.text, blocks: [] });
      else groups.at(-1).blocks.push(block);
    }
    return <><ReportBlocks blocks={blocks.slice(0, firstGroup)} section={section} />{groups.map((group, index) => <section key={index} className={`report-evidence-group ${["공격 요청 근거", "정탐 근거", "공격 해석"].includes(group.heading) || group.heading.endsWith("— 공격 요청 근거") ? "attack" : ["정상 요청 근거", "오탐 근거", "정상 해석"].includes(group.heading) || group.heading.endsWith("— 정상 요청 근거") ? "normal" : "context"}`}><h4>{decodeReportText(group.heading)}</h4><ReportBlocks blocks={group.blocks} section={group.heading} /></section>)}</>;
  }
  return blocks.map((block, index) => {
    // The generator appends exactly one LF to separate the excerpt from its
    // closing fence. Remove only that separator, preserving original CR/LF.
    if (block.type === "code") return <pre key={index} className="report-excerpt" tabIndex={0} aria-label={`${section} 원문 발췌`}><code>{block.text.endsWith("\n") ? block.text.slice(0, -1) : block.text}</code></pre>;
    if (block.type === "table") return <div key={index} className="report-table"><Table><caption className="sr-only">{section}</caption><thead><tr>{block.headers.map((cell, i) => <th key={i} scope="col">{decodeReportText(cell)}</th>)}</tr></thead><tbody>{block.rows.map((row, i) => <tr key={i}>{row.map((cell, j) => <td key={j}>{decodeReportText(cell)}</td>)}</tr>)}</tbody></Table></div>;
    if (block.type === "list") {
      const Tag = block.ordered ? "ol" : "ul";
      return <Tag key={index} {...(block.ordered ? { start: block.start } : {})}>{block.items.map((item, i) => <li key={i}>{decodeReportText(item)}</li>)}</Tag>;
    }
    if (block.type === "heading") return block.level >= 4 ? <h5 key={index}>{decodeReportText(block.text)}</h5> : <h4 key={index}>{decodeReportText(block.text)}</h4>;
    return <p key={index}>{decodeReportText(block.text)}</p>;
  });
}

export function ReportDocument({ report, reportId = "pdf-report", sections, jumpTo }) {
  const w = useR5Words();
  return <article className="panel report-paper" aria-label={w("분석 보고서", "Analysis report")}>
    <header className="report-cover"><div className="report-brand"><strong>WAF AI CONSOLE</strong><span>ANALYSIS REPORT</span></div><span className="eyebrow">{w("요청 보안 분석", "Request security analysis")}</span><h2>{decodeReportText(report.title)}</h2><ReportBlocks blocks={report.intro} section={w("보고서 안내", "Report notes")} /></header>
    {report.sections.map((section, index) => <section className="report-section" ref={sections ? node => { sections.current[index] = node; } : undefined} tabIndex={-1} key={section.id} aria-labelledby={reportId + "-section-" + index}><div className="report-section-heading"><span aria-hidden="true">{String(index + 1).padStart(2, "0")}</span><h3 id={reportId + "-section-" + index}>{decodeReportText(section.title)}</h3></div><ReportBlocks blocks={section.blocks} section={section.title} />{jumpTo && <button type="button" className="text-button report-back-top" onClick={() => jumpTo("top")}>{w("목차로", "Back to contents")}</button>}</section>)}
  </article>;
}

export default function AnalysisReport({ detail, decoding = null, mode, onModeChange, includeAppendix: controlledAppendix, onAppendixChange }) {
  const w = useR5Words();
  const [localAppendix, setLocalAppendix] = useState(false);
  const includeAppendix = controlledAppendix ?? localAppendix;
  const setIncludeAppendix = onAppendixChange ?? setLocalAppendix;
  const reportId = useId();
  const top = useRef(null);
  const sections = useRef([]);
  const markdown = useMemo(() => buildAnalysisReport(detail, { decoding, includeAppendix }), [detail, decoding, includeAppendix]);
  const report = useMemo(() => parseApiDocument(markdown), [markdown]);
  function jumpTo(index) { const target = index === "top" ? top.current : sections.current[Number(index)]; target?.scrollIntoView({ behavior: "auto", block: "start" }); target?.focus({ preventScroll: true }); }
  return <section className="analysis-report" aria-label={w("보고서 보기 설정", "Report view settings")} ref={top} tabIndex={-1}>
    <div className="panel report-toolbar">
      <div className="report-toolbar-title"><span className="report-icon"><Icon name="file" size={22} /></span><h2>{w("분석 보고서", "Analysis report")}</h2></div>
      <div className="report-mode" role="group" aria-label={w("보고서 표시 방식", "Report presentation")}>
        <button type="button" aria-pressed={mode === "preview"} aria-controls={reportId + "-content"} onClick={() => onModeChange("preview")}><Icon name="file" size={15} />{w("보고서 보기", "Report preview")}</button>
        <button type="button" aria-pressed={mode === "source"} aria-controls={reportId + "-content"} onClick={() => onModeChange("source")}><span aria-hidden="true">MD</span>{w("Markdown 원본", "Markdown source")}</button>
      </div>
      <div className="report-appendix-option"><label><input type="checkbox" checked={includeAppendix} onChange={(event) => setIncludeAppendix(event.target.checked)} />{w("평가·실행 부록 포함", "Include evaluation and execution appendix")}</label><small className="ux-muted">{w("화면·Markdown·PDF에 동일하게 적용 · Excel은 기본 항목으로 출력", "Applies to the screen, Markdown and PDF. Excel retains its standard fields.")}</small></div>
    </div>
    <div className="report-reading-tools"><span className="report-scope-note">{w("근거 발췌 포함", "Includes evidence excerpts")}</span>{mode !== "source" && <label className="report-jump">{w("목차", "Contents")}<select aria-label={w("보고서 목차", "Report contents")} value="" onChange={(event) => { if (event.target.value) jumpTo(event.target.value); }}><option value="">{w("항목으로 이동", "Jump to section")}</option>{report.sections.map((section, index) => <option key={section.id} value={String(index)}>{decodeReportText(section.title)}</option>)}</select></label>}</div>
    <div id={reportId + "-content"}>
      {mode === "source" ? <div className="panel report-source"><div className="report-source-head"><span>{w("Markdown 원본", "Markdown source")}</span><small>{w("읽기 전용 · 보고서 보기와 동일한 내용", "Read-only. The same content as the preview.")}</small></div><pre tabIndex={0} aria-label={w("보고서 Markdown 원본", "Report Markdown source")}><code>{markdown}</code></pre></div> : <ReportDocument report={report} reportId={reportId} sections={sections} jumpTo={jumpTo} />}
    </div>
  </section>;
}
