// Pure text document shared with the browser's report generator and parser.
// No DOM, credentials, URLs or model calls are involved.
import { buildAnalysisReport, decodeReportText } from "./analysisReport.js";
import { parseApiDocument } from "./apiDocument.js";

export function normalizeReportBlock(block) {
  if (block.type === "code") return { ...block, text: block.text.endsWith("\n") ? block.text.slice(0, -1) : block.text };
  const output = { ...block };
  if (typeof block.text === "string") output.text = decodeReportText(block.text);
  if (block.items) output.items = block.items.map(decodeReportText);
  if (block.headers) output.headers = block.headers.map(decodeReportText);
  if (block.rows) output.rows = block.rows.map(row => row.map(decodeReportText));
  return output;
}

export function buildReportDocument({ detail, decoding = null, includeAppendix = false }) {
  const markdown = buildAnalysisReport(detail, { decoding, includeAppendix });
  if (new TextEncoder().encode(markdown).length > 512_000) throw new Error("report_too_large");
  const report = parseApiDocument(markdown);
  return {
    title: decodeReportText(report.title), intro: report.intro.map(normalizeReportBlock),
    sections: report.sections.map(section => ({ ...section, title: decodeReportText(section.title), blocks: section.blocks.map(normalizeReportBlock) })),
  };
}
