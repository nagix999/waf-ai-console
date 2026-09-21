import { useEffect, useId, useMemo, useRef, useState } from "react";
import { recordText, textMatches } from "./inspection.js";
import JsonTree from "./JsonTree.jsx";
import { jsonTokens, looksLikeJsonRecord, parseJsonRecord } from "./jsonInspection.js";
import "./inspection.css";

function JsonText({ tokens, start, query, mark }) {
  return tokens.map((token, index) => {
    const from = Math.max(start, token.start);
    const to = Math.min(start + query.length, token.start + token.text.length);
    return <span key={index} className={`json-${token.type}`}>{start === undefined || from >= to ? token.text : <>{token.text.slice(0, from - token.start)}<mark ref={from === start ? mark : undefined}>{token.text.slice(from - token.start, to - token.start)}</mark>{token.text.slice(to - token.start)}</>}</span>;
  });
}

export default function TextInspector({ value, label = "원문", empty = "기록된 내용이 없습니다.", compact = false, jsonText = false, searchRequest }) {
  const originalText = useMemo(() => recordText(value), [value]);
  const parsed = useMemo(() => jsonText && typeof value === "string" ? parseJsonRecord(value) : null, [value, jsonText]);
  const structured = (value !== null && typeof value === "object") || !!parsed;
  // UI-created objects follow JSON serialization. Only the explicit Agent
  // option accepts a serialized record; nested HTTP/string fields stay text.
  const jsonValue = useMemo(() => parsed?.value ?? (structured ? JSON.parse(originalText) : null), [parsed, structured, originalText]);
  const [mode, setMode] = useState("tree");
  const rawMode = !!parsed && mode === "raw";
  const text = rawMode ? originalText : parsed?.text ?? originalText;
  const simple = !structured && compact && text.length <= 256;
  const [query, setQuery] = useState("");
  const [selected, setSelected] = useState(0);
  const [wrap, setWrap] = useState(true);
  const [copied, setCopied] = useState("");
  const [copying, setCopying] = useState(false);
  const searchId = useId(); const mark = useRef(null); const mounted = useRef(false);
  const matches = useMemo(() => textMatches(text, query), [text, query]);
  const tokens = useMemo(() => structured && !rawMode ? jsonTokens(text) : [], [structured, rawMode, text]);
  // Search the full pretty JSON, including collapsed nodes and long values.
  // Clearing the search restores the chosen view and its expansion state.
  const treeVisible = structured && mode === "tree" && !query;
  const index = matches.positions.length ? selected % matches.positions.length : 0;
  const start = matches.positions[index];
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
  useEffect(() => { setSelected(0); }, [text, query]);
  useEffect(() => { setCopied(""); }, [originalText, query]);
  useEffect(() => { if (searchRequest) { setQuery(searchRequest.query || ""); setSelected(0); } }, [searchRequest]);
  useEffect(() => { if (mark.current?.getClientRects().length) mark.current.scrollIntoView({ block: "nearest", inline: "nearest" }); }, [index, query, text, searchRequest]);
  async function copy() {
    setCopying(true); setCopied("");
    try { await navigator.clipboard.writeText(originalText); if (mounted.current) setCopied("복사했습니다."); }
    catch { if (mounted.current) { if (structured) setMode(parsed ? "raw" : "json"); setCopied("복사할 수 없습니다. 아래 텍스트를 선택해 복사하세요."); } }
    finally { if (mounted.current) setCopying(false); }
  }
  return <section className={`text-inspector${simple ? " inspector-compact" : ""}`} aria-label={`${label} 열람`}>
    {structured && <div className="inspector-json-head"><div className="inspector-modes" role="group" aria-label={`${label} 보기 방식`}><button type="button" aria-pressed={mode === "tree" && !query} disabled={!!query} onClick={() => setMode("tree")}>트리</button><button type="button" aria-pressed={!treeVisible && !rawMode} onClick={() => setMode("json")}>JSON</button>{parsed && <button type="button" aria-pressed={rawMode} onClick={() => setMode("raw")}>원문</button>}</div><div className="json-legend" aria-label="JSON 값의 색상"><span className="json-string">문자열</span><span className="json-number">숫자</span><span className="json-boolean">참/거짓</span><span className="json-null">null</span></div></div>}
    {jsonText && !structured && looksLikeJsonRecord(value) && <p className="ux-muted">값이 달라지거나 일부가 빠질 수 있어 트리 대신 원문을 표시합니다.</p>}
    <div className="inspector-tools">
      {!simple && <div className="inspector-search"><label className="sr-only" htmlFor={searchId}>{label}에서 찾기</label><input id={searchId} type="search" value={query} maxLength={160} placeholder="내용에서 찾기" onChange={event => setQuery(event.target.value)} autoComplete="off" spellCheck={false} />
        {query && <><span role="status">{matches.positions.length ? `${index + 1} / ${matches.positions.length}${matches.limited ? "+" : ""}` : "없음"}</span><button type="button" className="secondary" aria-label={`${label} 이전 일치`} disabled={!matches.positions.length} onClick={() => setSelected(index + matches.positions.length - 1)}>↑</button><button type="button" className="secondary" aria-label={`${label} 다음 일치`} disabled={!matches.positions.length} onClick={() => setSelected(index + 1)}>↓</button></>}
      </div>}
      <div className="ux-toolbar">{!simple && <button type="button" className="secondary" aria-pressed={wrap} onClick={() => setWrap(value => !value)}>자동 줄바꿈</button>}<button type="button" className="secondary" disabled={!text || copying} onClick={copy}>{copying ? "복사 중…" : simple ? "복사" : "전체 복사"}</button></div>
    </div>
    {query && <p className="ux-muted">{structured && `${rawMode ? "원문" : "JSON"} 전체 검색 · `}대소문자 구분{matches.limited && " · 처음 200곳까지 표시"}</p>}
    {parsed && <p className="ux-muted">보기만 정리하며 전체 복사는 저장된 원문을 복사합니다.</p>}
    {copied && <p className="ux-muted" role="status">{copied}</p>}
    {structured && <div hidden={!treeVisible}><JsonTree key={originalText} value={jsonValue} label={label} wrap={wrap} inspectJsonStrings={jsonText} onShowJson={() => setMode("json")} /></div>}
    {text ? !treeVisible && <pre className={`inspector-content${wrap ? " inspector-wrap" : ""}`} tabIndex={0} aria-label={label}><code>{structured && !rawMode ? <JsonText tokens={tokens} start={start} query={query} mark={mark} /> : start === undefined ? text : <>{text.slice(0, start)}<mark ref={mark}>{text.slice(start, start + query.length)}</mark>{text.slice(start + query.length)}</>}</code></pre> : <p className="empty">{empty}</p>}
  </section>;
}
