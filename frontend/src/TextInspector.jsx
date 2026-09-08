import { useEffect, useId, useMemo, useRef, useState } from "react";
import { recordText, textMatches } from "./inspection.js";
import "./inspection.css";

export default function TextInspector({ value, label = "원문", empty = "기록된 내용이 없습니다.", compact = false }) {
  const text = recordText(value);
  const simple = compact && text.length <= 256;
  const [query, setQuery] = useState("");
  const [selected, setSelected] = useState(0);
  const [wrap, setWrap] = useState(true);
  const [copied, setCopied] = useState("");
  const [copying, setCopying] = useState(false);
  const searchId = useId(); const mark = useRef(null); const mounted = useRef(false);
  const matches = useMemo(() => textMatches(text, query), [text, query]);
  const index = matches.positions.length ? selected % matches.positions.length : 0;
  const start = matches.positions[index];
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
  useEffect(() => { setSelected(0); setCopied(""); }, [text, query]);
  useEffect(() => { mark.current?.scrollIntoView({ block: "nearest", inline: "nearest" }); }, [index, query]);
  async function copy() {
    setCopying(true); setCopied("");
    try { await navigator.clipboard.writeText(text); if (mounted.current) setCopied("복사했습니다."); }
    catch { if (mounted.current) setCopied("복사할 수 없습니다. 아래 텍스트를 선택해 복사하세요."); }
    finally { if (mounted.current) setCopying(false); }
  }
  return <section className={`text-inspector${simple ? " inspector-compact" : ""}`} aria-label={`${label} 열람`}>
    <div className="inspector-tools">
      {!simple && <div className="inspector-search"><label className="sr-only" htmlFor={searchId}>{label}에서 찾기</label><input id={searchId} type="search" value={query} maxLength={160} placeholder="내용에서 찾기" onChange={event => setQuery(event.target.value)} autoComplete="off" spellCheck={false} />
        {query && <><span role="status">{matches.positions.length ? `${index + 1} / ${matches.positions.length}${matches.limited ? "+" : ""}` : "없음"}</span><button type="button" className="secondary" aria-label={`${label} 이전 일치`} disabled={!matches.positions.length} onClick={() => setSelected(index + matches.positions.length - 1)}>↑</button><button type="button" className="secondary" aria-label={`${label} 다음 일치`} disabled={!matches.positions.length} onClick={() => setSelected(index + 1)}>↓</button></>}
      </div>}
      <div className="ux-toolbar">{!simple && <button type="button" className="secondary" aria-pressed={wrap} onClick={() => setWrap(value => !value)}>자동 줄바꿈</button>}<button type="button" className="secondary" disabled={!text || copying} onClick={copy}>{copying ? "복사 중…" : simple ? "복사" : "전체 복사"}</button></div>
    </div>
    {query && <p className="ux-muted">대소문자 구분{matches.limited && " · 처음 200곳까지 표시"}</p>}
    {copied && <p className="ux-muted" role="status">{copied}</p>}
    {text ? <pre className={`inspector-content${wrap ? " inspector-wrap" : ""}`} tabIndex={0} aria-label={label}><code>{start === undefined ? text : <>{text.slice(0, start)}<mark ref={mark}>{text.slice(start, start + query.length)}</mark>{text.slice(start + query.length)}</>}</code></pre> : <p className="empty">{empty}</p>}
  </section>;
}
