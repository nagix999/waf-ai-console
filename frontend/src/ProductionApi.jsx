import { useEffect, useMemo, useRef, useState } from "react";
import { api } from "./api.js";
import { filterApiSections, parseApiDocument } from "./apiDocument.js";
import { Icon } from "./Icon.jsx";
import "./productionApi.css";

// Server definitions are rendered as React text, never as executable HTML.
function Inline({ text }) {
  return text.split(/(`[^`]+`|\*\*[^*]+\*\*)/g).map((part, index) => {
    if (part.startsWith("`") && part.endsWith("`")) return <code key={index}>{part.slice(1, -1)}</code>;
    if (part.startsWith("**") && part.endsWith("**")) return <strong key={index}>{part.slice(2, -2)}</strong>;
    return part;
  });
}

function CodeExample({ block, section }) {
  const [message, setMessage] = useState("");
  const timer = useRef(null);
  useEffect(() => () => clearTimeout(timer.current), []);
  async function copy() {
    clearTimeout(timer.current);
    try {
      if (!navigator.clipboard?.writeText) throw new Error("clipboard_unavailable");
      await navigator.clipboard.writeText(block.text);
      setMessage("복사했습니다.");
    } catch {
      setMessage("복사할 수 없습니다. 아래 코드를 선택해서 직접 복사하세요.");
    }
    timer.current = setTimeout(() => setMessage(""), 5000);
  }
  return <div className="api-code">
    <div className="api-code-head"><span>{block.language || "TEXT"}</span><button type="button" className="text-button" aria-label={`${section} · ${block.language || "텍스트"} 예시 복사`} onClick={copy}><Icon name="copy" size={14} />복사</button></div>
    <pre tabIndex={0} aria-label={`${section} ${block.language || "텍스트"} 예시`}><code>{block.text}</code></pre>
    {message && <div className="api-copy-message" role="status">{message}</div>}
  </div>;
}

function Blocks({ blocks, section }) {
  return blocks.map((block, index) => {
    if (block.type === "code") return <CodeExample key={index} block={block} section={section} />;
    if (block.type === "table") return <div className="table-wrap api-table" key={index}><table><caption className="sr-only">{section} 정의</caption><thead><tr>{block.headers.map((cell, i) => <th key={i} scope="col"><Inline text={cell} /></th>)}</tr></thead><tbody>{block.rows.map((row, i) => <tr key={i}>{row.map((cell, j) => <td key={j}><Inline text={cell} /></td>)}</tr>)}</tbody></table></div>;
    if (block.type === "list") {
      const Tag = block.ordered ? "ol" : "ul";
      return <Tag key={index} {...(block.ordered ? { start: block.start } : {})}>{block.items.map((item, i) => <li key={i}><Inline text={item} /></li>)}</Tag>;
    }
    if (block.type === "heading") return <h3 key={index}><Inline text={block.text} /></h3>;
    return <p key={index}><Inline text={block.text} /></p>;
  });
}

export default function ProductionApi() {
  const [query, setQuery] = useState("");
  const [reference, setReference] = useState(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [attempt, setAttempt] = useState(0);
  const [downloadUrl, setDownloadUrl] = useState(null);
  const document = useMemo(() => parseApiDocument(reference?.markdown || ""), [reference]);
  const sections = useMemo(() => filterApiSections(document.sections, query), [document, query]);
  useEffect(() => {
    const controller = new AbortController(); let current = true;
    setLoading(true); setError(""); setReference(null);
    api.productionApi({ signal: controller.signal }).then(value => {
      if (typeof value?.markdown !== "string" || !value.markdown.trim() || !value.input_schema?.version_id) throw new Error("invalid_document");
      if (current) { setReference(value); setLoading(false); }
    }).catch(() => { if (current) { setError("input_schema_document_unavailable"); setLoading(false); } });
    return () => { current = false; controller.abort(); };
  }, [attempt]);
  useEffect(() => {
    setDownloadUrl(null);
    if (!reference) return;
    const url = URL.createObjectURL(new Blob([reference.markdown], { type: "text/markdown;charset=utf-8" }));
    setDownloadUrl(url);
    return () => URL.revokeObjectURL(url);
  }, [reference]);

  function jump(id) {
    const target = window.document.getElementById(id);
    target?.focus({ preventScroll: true });
    target?.scrollIntoView({ block: "start", behavior: "instant" });
  }

  return <div className="page-stack api-document-page">
    <section className="panel api-doc-hero">
      <div className="api-doc-heading"><span className="api-doc-icon"><Icon name="apiDocs" size={27} /></span><div><span className="eyebrow">INTEGRATION REFERENCE</span><h2>{document.title || "Production WAF Analysis API"}</h2><p>운영 수집기와 분석가 시스템을 위한 연동 정의서</p></div></div>
      <div className="api-doc-actions"><a className="primary" href="/docs" target="_blank" rel="noopener noreferrer">Swagger 열기<Icon name="arrow" size={15} /></a><a className="secondary" href="/openapi.json" target="_blank" rel="noopener noreferrer">OpenAPI JSON</a>{reference && downloadUrl && <a className="secondary" href={downloadUrl} download={`Production_API_v0.2.0_schema-v${reference.input_schema.version_number}.md`}><Icon name="file" size={15} />정의서 다운로드</a>}<button type="button" className="secondary" disabled={loading} onClick={() => setAttempt(value => value + 1)}>최신 정의서 새로고침</button></div>
      <div className="api-doc-meta"><span><strong>BASE PATH</strong><code>/api/v1</code></span><span><strong>AUTH</strong><code>X-API-Key</code></span><span><strong>FORMAT</strong>JSON · UTF-8</span></div>
    </section>
    {loading && <p className="loading" role="status">현재 운영 스키마로 정의서를 불러오는 중…</p>}
    {error && <div className="error" role="alert">현재 입력 스키마를 확인하지 못했습니다. 과거 정의서를 현재 기준으로 표시하지 않습니다. 새로고침해 다시 확인하세요.</div>}
    {reference && <p className="api-source-note">조회한 운영 입력 스키마 v{reference.input_schema.version_number} · 지문 <code>{reference.input_schema.content_hash}</code> · 설정 변경 후에는 최신 정의서를 다시 조회하세요.</p>}
    <div className="api-doc-notice"><Icon name="shield" size={18} /><p>이 페이지는 읽기 전용입니다. 예시를 복사해도 요청은 실행되지 않습니다. Swagger의 <strong>Try it out</strong>은 실제 API를 호출하므로 주의하세요. 관리자 로그인 세션은 API Key보다 우선합니다.</p></div>
    {reference && <div className="api-doc-layout">
      <div className="api-doc-index panel">
        <label htmlFor="api-doc-search">정의서 검색</label><div className="api-doc-search"><Icon name="search" size={16} /><input id="api-doc-search" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="필드, 경로, 오류 코드" type="search" maxLength={200} /></div>
        <span className="api-index-count" aria-live="polite">{sections.length} / {document.sections.length}개 항목</span>
        <div className="api-doc-toc" role="navigation" aria-label="API 정의서 목차">{sections.map((section) => <button type="button" key={section.id} onClick={() => jump(section.id)}>{section.title}</button>)}</div>
        <small>본문·예시에서 검색어를 포함한 항목 전체를 표시합니다.</small>
      </div>
      <div className="api-doc-content">
        {!query.trim() && <section className="panel api-doc-section api-doc-intro"><Blocks blocks={document.intro} section="정의서 안내" /><p className="api-source-note">화면과 다운로드는 서버에서 조회한 동일한 운영 스키마 정의서를 사용합니다. 스키마 변경에 프런트엔드 재빌드는 필요하지 않습니다.</p></section>}
        {sections.map((section) => <section key={section.id} className="panel api-doc-section" aria-labelledby={section.id}><div className="api-section-title"><span>{String(document.sections.indexOf(section) + 1).padStart(2, "0")}</span><h2 id={section.id} tabIndex={-1}>{section.title}</h2></div><Blocks blocks={section.blocks} section={section.title} /></section>)}
        {!sections.length && <section className="panel empty"><Icon name="search" size={28} /><strong>검색어에 해당하는 항목이 없습니다.</strong><small>다른 필드명이나 오류 코드로 검색해 보세요.</small><button type="button" className="secondary" onClick={() => setQuery("")}>검색 초기화</button></section>}
      </div>
    </div>}
  </div>;
}
