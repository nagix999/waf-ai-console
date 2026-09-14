import HelpTooltip from "./HelpTooltip.jsx";

export default function SummaryPreview({ text }) {
  if (typeof text !== "string" || !text.trim()) return null;
  const chars = Array.from(text);
  return <HelpTooltip label="분석 요약" previewText={chars.length > 90 ? `${chars.slice(0, 90).join("")}...` : text}>{text}</HelpTooltip>;
}
