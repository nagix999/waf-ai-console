import { useConsolePreferences } from "./consolePreferences.jsx";

export default function ParserPairEditor({ text, onChange, disabled }) {
  const { locale } = useConsolePreferences(), w = (ko, en) => locale === "en" ? en : ko;
  let rows;
  try { rows = JSON.parse(text || "[]"); if (!Array.isArray(rows)) rows = []; } catch { rows = []; }
  const update = next => onChange(JSON.stringify(next));
  return <div className="pw-pair-editor"><p className="pw-inline-note">{w("같은 이름이 여러 번 나오면 각각 한 행으로 유지하세요. 순서도 보존됩니다.", "Keep repeated names in separate rows. Order is preserved.")}</p>{rows.map((row, index) => <div className="pw-pair-row" key={index}><label>{w("이름", "Name")}<input aria-label={`${w("항목", "Item")} ${index + 1} ${w("이름", "name")}`} value={row.name ?? ""} disabled={disabled} onChange={event => update(rows.map((item, position) => position === index ? { ...item, name: event.target.value } : item))} /></label><label>{w("값", "Value")}<input aria-label={`${w("항목", "Item")} ${index + 1} ${w("값", "value")}`} value={row.value ?? ""} disabled={disabled} onChange={event => update(rows.map((item, position) => position === index ? { ...item, value: event.target.value } : item))} /></label><button type="button" className="text-button" disabled={disabled} aria-label={`${w("항목", "Item")} ${index + 1} ${w("삭제", "remove")}`} onClick={() => update(rows.filter((_, position) => position !== index))}>{w("삭제", "Remove")}</button></div>)}<button type="button" className="secondary" disabled={disabled} onClick={() => update([...rows, { name: "", value: "" }])}>{w("항목 추가", "Add item")}</button></div>;
}
