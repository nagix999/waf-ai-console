import { useEffect, useId, useMemo, useRef, useState } from "react";
import { useR5Words } from "./R5Evaluation.jsx";
import { textMatches } from "./inspection.js";
import { splitHighlightedText } from "./evidenceInputSource.js";

export function revealInPane(node) {
  const pane = node?.closest(".test-analysis-pane");
  if (!pane) return;
  const box = node.getBoundingClientRect(), viewport = pane.getBoundingClientRect();
  if (box.top < viewport.top + 64 || box.bottom > viewport.bottom) pane.scrollTop += box.top - viewport.top - 82;
}

export default function ExactSourceText({ value, ranges = [], target, label }) {
  const w = useR5Words(), id = useId(), mark = useRef(null);
  const [query, setQuery] = useState(""), [selected, setSelected] = useState(0), [wrap, setWrap] = useState(true), [copied, setCopied] = useState("");
  const text = typeof value === "string" ? value : "", matches = useMemo(() => textMatches(text, query), [text, query]);
  const index = matches.positions.length ? selected % matches.positions.length : 0;
  const highlighted = query ? matches.positions.length ? [{ start: matches.positions[index], end: matches.positions[index] + query.length }] : [] : ranges;
  const parts = splitHighlightedText(text, highlighted), firstHighlight = parts.findIndex(part => part.highlighted);
  useEffect(() => { setQuery(""); setSelected(0); setCopied(""); }, [target, text]);
  useEffect(() => { revealInPane(mark.current); }, [target, index, query, ranges]);
  async function copy() {
    try { await navigator.clipboard.writeText(text); setCopied(w("복사했습니다.", "Copied.")); }
    catch { setCopied(w("복사할 수 없습니다. 원문을 선택해 복사하세요.", "Could not copy. Select and copy the source text.")); }
  }
  return <section className="test-exact-source" aria-label={label}>
    <div className="test-source-tools"><div className="inspector-search"><label className="sr-only" htmlFor={id}>{w("원문에서 찾기", "Find in source")}</label><input id={id} type="search" value={query} maxLength={160} placeholder={w("원문에서 찾기", "Find in source")} onChange={event => { setQuery(event.target.value); setSelected(0); }} autoComplete="off" spellCheck={false} />{query && <><span role="status">{matches.positions.length ? `${index + 1}/${matches.positions.length}${matches.limited ? "+" : ""}` : w("없음", "No matches")}</span><button type="button" className="secondary" disabled={!matches.positions.length} aria-label={w("이전 일치", "Previous match")} onClick={() => setSelected(index + matches.positions.length - 1)}>↑</button><button type="button" className="secondary" disabled={!matches.positions.length} aria-label={w("다음 일치", "Next match")} onClick={() => setSelected(index + 1)}>↓</button></>}</div><div className="ux-toolbar"><button type="button" className="secondary" aria-pressed={wrap} onClick={() => setWrap(value => !value)}>{w("줄바꿈", "Wrap")}</button><button type="button" className="secondary" onClick={copy} disabled={typeof value !== "string"}>{w("전체 복사", "Copy all")}</button></div></div>
    {copied && <p role="status" className="muted">{copied}</p>}
    {typeof value === "string" ? <pre className={`test-raw-payload${wrap ? " is-wrapped" : ""}`} tabIndex={0} aria-label={label}><code>{parts.map((part, index) => part.highlighted ? <mark key={index} ref={index === firstHighlight ? mark : undefined} data-source-highlight="true">{part.text}</mark> : part.text)}</code></pre> : <p className="muted">{w("원문이 기록되지 않았습니다.", "Source text was not recorded.")}</p>}
  </section>;
}
