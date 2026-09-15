// Trusted PDF entry: no application router, credentials, fetch or raw-event API.
import "./styles.css";
import { createRoot } from "react-dom/client";
import { flushSync } from "react-dom";
import { ReportDocument } from "./AnalysisReport.jsx";
import { buildAnalysisReport } from "./analysisReport.js";
import { parseApiDocument } from "./apiDocument.js";
import "./reportPdf.css";

window.renderWafReport = ({ detail, decoding, includeAppendix, theme }) => {
  document.documentElement.dataset.theme = theme === "dark" ? "dark" : "light";
  const markdown = buildAnalysisReport(detail, { decoding, includeAppendix });
  if (new TextEncoder().encode(markdown).length > 512_000) throw new Error("report_too_large");
  const report = parseApiDocument(markdown);
  flushSync(() => createRoot(document.getElementById("report-root")).render(<ReportDocument report={report} />));
  if (document.querySelectorAll("p, li, tr, pre, h3, h4, h5").length > 1000) throw new Error("report_too_large");
  window.wafReportReady = true;
};
