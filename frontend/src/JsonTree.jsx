import { useId, useMemo, useState } from "react";
import { JSON_PAGE_SIZE, JSON_PREVIEW_LENGTH, JSON_TREE_DEPTH, jsonStringPreview, jsonType, looksLikeJsonRecord, parseJsonRecord } from "./jsonInspection.js";

function JsonValue({ value, name, inspectJsonStrings, depth, onShowJson }) {
  const [length, setLength] = useState(JSON_PREVIEW_LENGTH);
  const [jsonOpen, setJsonOpen] = useState(false);
  const canInspect = inspectJsonStrings && depth < JSON_TREE_DEPTH && ["user_input", "correction_user_input"].includes(name) && looksLikeJsonRecord(value);
  const parsed = useMemo(() => canInspect && jsonOpen ? parseJsonRecord(value) : null, [canInspect, jsonOpen, value]);
  const type = jsonType(value);
  const preview = type === "string" ? jsonStringPreview(value, length) : null;
  return <><span className="json-leaf-value">
    {parsed ? <span className="json-count">JSON 문자열</span> : <span className={`json-${type}`}>{preview ? preview.text : String(value)}</span>}
    {canInspect && <button type="button" className="json-more" aria-expanded={jsonOpen} aria-label={`${name} ${jsonOpen ? "문자열 보기" : "JSON 펼치기"}`} onClick={() => setJsonOpen(current => !current)}>{jsonOpen ? "문자열 보기" : "JSON 펼치기"}</button>}
    {!parsed && preview?.remaining > 0 && <><span className="json-count"> … {preview.remaining.toLocaleString()}자 더 있음</span> <button type="button" className="json-more" aria-label={`${name} 문자열 더 보기`} onClick={() => setLength(current => current + 2000)}>더 보기</button></>}
    {!parsed && length > JSON_PREVIEW_LENGTH && <button type="button" className="json-more" aria-label={`${name} 문자열 접기`} onClick={() => setLength(JSON_PREVIEW_LENGTH)}>접기</button>}
  </span>{parsed && <div className="json-embedded"><ul><JsonNode value={parsed.value} depth={depth + 1} initiallyOpen onShowJson={onShowJson} /></ul></div>}{jsonOpen && !parsed && <p className="ux-muted">내용을 그대로 보존하기 위해 문자열로 표시합니다.</p>}</>;
}

function JsonNode({ value, name, arrayItem = false, depth = 0, onShowJson, inspectJsonStrings = false, initiallyOpen = false }) {
  const [open, setOpen] = useState(depth === 0 || initiallyOpen);
  const [visible, setVisible] = useState(JSON_PAGE_SIZE);
  const childrenId = useId();
  const type = jsonType(value);
  const container = type === "object" || type === "array";
  const keys = useMemo(() => type === "object" ? Object.keys(value) : null, [value, type]);
  const count = type === "array" ? value.length : keys?.length || 0;
  const atLimit = depth >= JSON_TREE_DEPTH;
  const expandable = container && count > 0 && !atLimit;
  const expanded = expandable && open;
  const nodeName = name === undefined ? "전체 JSON" : String(name);
  const displayName = typeof name === "string" ? jsonStringPreview(name, 160) : null;
  const field = name === undefined ? null : <><span className={arrayItem ? "json-index" : "json-key"}>{displayName ? `${displayName.text}${displayName.remaining ? "…" : ""}` : name}</span><span className="json-punctuation">: </span></>;
  const opening = type === "array" ? "[" : "{";
  const closing = type === "array" ? "]" : "}";
  return <li className="json-node">
    <div className="json-row">
      {expandable ? <button type="button" className="json-toggle" aria-expanded={expanded} aria-controls={expanded ? childrenId : undefined} aria-label={`${nodeName.slice(0, 160)} ${expanded ? "접기" : "펼치기"}`} onClick={() => setOpen(current => !current)}><svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true"><path d="m6 3 5 5-5 5" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" /></svg></button> : <span className="json-toggle-space" aria-hidden="true" />}
      <div className="json-node-content">{field}{container ? <><span className="json-punctuation">{opening}{!expanded && `${count ? "…" : ""}${closing}`}</span><span className="json-count">{type === "array" ? "배열" : "객체"} · {count.toLocaleString()}개</span>{atLimit && count > 0 && <button type="button" className="json-more" onClick={onShowJson}>하위 내용은 JSON으로 보기</button>}</> : <JsonValue value={value} name={nodeName.slice(0, 160)} inspectJsonStrings={inspectJsonStrings} depth={depth} onShowJson={onShowJson} />}</div>
    </div>
    {expanded && <>
      <ul id={childrenId} className="json-children">
        {Array.from({ length: Math.min(count, visible) }, (_, index) => {
          const key = type === "array" ? index : keys[index];
          return <JsonNode key={key} value={value[key]} name={key} arrayItem={type === "array"} depth={depth + 1} onShowJson={onShowJson} inspectJsonStrings={inspectJsonStrings} />;
        })}
        {visible < count && <li className="json-page"><button type="button" className="json-more" onClick={() => setVisible(current => current + JSON_PAGE_SIZE)}>다음 {Math.min(JSON_PAGE_SIZE, count - visible)}개 보기</button><span className="json-count">{(count - visible).toLocaleString()}개 남음</span></li>}
      </ul>
      <div className="json-closing json-punctuation" aria-hidden="true">{closing}</div>
    </>}
  </li>;
}

export default function JsonTree({ value, label, wrap, onShowJson, inspectJsonStrings = false }) {
  return <div className={`json-tree inspector-content${wrap ? " inspector-wrap" : ""}`} tabIndex={0} aria-label={`${label} 트리`}>
    <ul className="json-root"><JsonNode value={value} onShowJson={onShowJson} inspectJsonStrings={inspectJsonStrings} /></ul>
  </div>;
}
