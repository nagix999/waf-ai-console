import { useEffect, useMemo, useRef, useState } from "react";
import { api } from "./api.js";
import { apiSectionNeighbors, filterApiSections, parseApiDocument } from "./apiDocument.js";
import { apiDocumentDownloadError, fetchApiDocumentPdf, saveApiDocumentPdf } from "./productionApiDownloads.js";
import { Icon } from "./Icon.jsx";
import Dialog from "./Dialog.jsx";
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

function Blocks({ blocks, section, inputSchema }) {
  return blocks.map((block, index) => {
    if (block.type === "code") return <CodeExample key={index} block={block} section={section} />;
    if (block.type === "table") return <div className="table-wrap api-table" key={index}><table><caption className="sr-only">{section} 정의</caption><thead><tr>{block.headers.map((cell, i) => <th key={i} scope="col"><Inline text={cell} /></th>)}</tr></thead><tbody>{block.rows.map((row, i) => <tr key={i}>{row.map((cell, j) => <td key={j}><Inline text={cell} /></td>)}</tr>)}</tbody></table></div>;
    if (block.type === "list") {
      const Tag = block.ordered ? "ol" : "ul";
      return <Tag key={index} {...(block.ordered ? { start: block.start } : {})}>{block.items.map((item, i) => <li key={i}><Inline text={item} /></li>)}</Tag>;
    }
    if (block.type === "heading") return <h3 key={index}><Inline text={block.text} /></h3>;
    const metadataText = inputSchema && `적용 입력 스키마: **v${inputSchema.version_number}** · \`${inputSchema.version_id}\`\n정의 SHA-256: \`${inputSchema.content_hash}\``;
    return <p key={index}><Inline text={metadataText && block.text === metadataText ? `적용 입력 스키마: **v${inputSchema.version_number}**` : block.text} /></p>;
  });
}

export function ApiDocumentBody({ document, sections, selectedId, query = "", inputSchema }) {
  const selected = sections.find(section => section.id === selectedId) || sections[0];
  if (selectedId === "intro" && !query.trim()) return <section className="panel api-doc-section api-doc-intro"><h2 tabIndex={-1}>정의서 안내</h2><Blocks blocks={document.intro} section="정의서 안내" inputSchema={inputSchema} /></section>;
  if (!selected) return null;
  return <section key={selected.id} className="panel api-doc-section" aria-labelledby={selected.id}><div className="api-section-title"><span>{String(document.sections.indexOf(selected) + 1).padStart(2, "0")}</span><h2 id={selected.id} tabIndex={-1}>{selected.title}</h2></div><Blocks blocks={selected.blocks} section={selected.title} inputSchema={inputSchema} /></section>;
}

export function ApiSectionNavigation({ sections, selectedId, query = "", onSelect }) {
  const { previous, next } = apiSectionNeighbors(sections, selectedId, query);
  if (!previous && !next) return null;
  return <nav className="api-section-navigation" aria-label="정의서 이전·다음 항목">
    {previous && <button type="button" className="secondary api-section-previous" aria-label={`이전 항목: ${previous.title}`} onClick={() => onSelect(previous.id)}><span aria-hidden="true">←</span><span>{previous.title}</span></button>}
    {next && <button type="button" className="secondary api-section-next" aria-label={`다음 항목: ${next.title}`} onClick={() => onSelect(next.id)}><span>{next.title}</span><span aria-hidden="true">→</span></button>}
  </nav>;
}

export default function ProductionApi() {
  const [query, setQuery] = useState("");
  const [reference, setReference] = useState(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [attempt, setAttempt] = useState(0);
  const [downloading, setDownloading] = useState(false);
  const [downloadError, setDownloadError] = useState("");
  const downloadRequest = useRef(null);
  const content = useRef(null); const focusRequested = useRef(false);
  const [selectedId, setSelectedId] = useState("intro");
  const [technical, setTechnical] = useState(false);
  const document = useMemo(() => parseApiDocument(reference?.markdown || ""), [reference]);
  const sections = useMemo(() => filterApiSections(document.sections, query), [document, query]);
  const selected = sections.find(section => section.id === selectedId) || sections[0];
  const showIntro = selectedId === "intro" && !query.trim();
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
    setDownloading(false); setDownloadError("");
    return () => { downloadRequest.current?.abort(); downloadRequest.current = null; };
  }, [reference]);
  function focusSection() {
    const heading = content.current?.querySelector(".api-doc-section h2");
    heading?.scrollIntoView({ behavior: "auto", block: "start" }); heading?.focus({ preventScroll: true });
  }
  function selectSection(id) {
    if (id === (showIntro ? "intro" : selected?.id)) { focusSection(); return; }
    focusRequested.current = true; setSelectedId(id);
  }
  useEffect(() => {
    if (focusRequested.current) { focusRequested.current = false; focusSection(); }
  }, [selectedId, selected?.id, showIntro]);
  async function download() {
    if (!reference || loading || downloadRequest.current) return;
    const controller = new AbortController(); downloadRequest.current = controller;
    setDownloading(true); setDownloadError("");
    try {
      const blob = await fetchApiDocumentPdf(reference.input_schema, { signal: controller.signal });
      if (!controller.signal.aborted && downloadRequest.current === controller) saveApiDocumentPdf(blob, reference.input_schema.version_number);
    } catch (error) {
      if (!controller.signal.aborted && downloadRequest.current === controller) setDownloadError(apiDocumentDownloadError(error));
    } finally {
      if (downloadRequest.current === controller) { downloadRequest.current = null; setDownloading(false); }
    }
  }

  return <div className="page-stack api-document-page">
    <section className="panel api-doc-hero">
      <div className="api-doc-heading"><span className="api-doc-icon"><Icon name="apiDocs" size={27} /></span><div><h2>운영 API 정의서</h2><p>항목을 선택해 요청·응답과 예시를 확인하세요.</p></div></div>
      <div className="api-doc-actions"><button type="button" className="primary" disabled={!reference || loading || downloading} onClick={download}><Icon name="file" size={15} />{downloading ? "PDF 작성 중…" : "PDF 다운로드"}</button><a className="secondary" href="/docs" target="_blank" rel="noopener noreferrer">Swagger 열기<Icon name="arrow" size={15} /></a><a className="secondary" href="/openapi.json" target="_blank" rel="noopener noreferrer">OpenAPI JSON</a><button type="button" className="secondary" disabled={loading || downloading} onClick={() => setAttempt(value => value + 1)}>최신 정의서 새로고침</button></div>
      <div className="api-doc-meta"><span><strong>기본 경로</strong><code>/api/v1</code></span><span><strong>인증</strong><code>X-API-Key</code></span><span><strong>형식</strong>JSON · UTF-8</span></div>
    </section>
    {loading && <p className="loading" role="status">현재 운영 스키마로 정의서를 불러오는 중…</p>}
    {error && <div className="error" role="alert">현재 입력 스키마를 확인하지 못했습니다. 과거 정의서를 현재 기준으로 표시하지 않습니다. 새로고침해 다시 확인하세요.</div>}
    {downloadError && <p className="error" role="alert">{downloadError}</p>}
    {reference && <div className="api-source-note">입력 스키마 v{reference.input_schema.version_number}<button type="button" className="text-button" onClick={() => setTechnical(true)}>기술정보</button></div>}
    <div className="api-doc-notice"><Icon name="shield" size={18} /><p>Swagger의 <strong>Try it out</strong>은 실제 API를 호출합니다. 로그인 중에는 API Key보다 관리자 권한이 우선하므로 호출할 API를 확인하세요.</p></div>
    {reference && <div className="api-doc-layout">
      <div className="api-doc-index panel">
        <label htmlFor="api-doc-search">정의서 검색</label><div className="api-doc-search"><Icon name="search" size={16} /><input id="api-doc-search" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="필드, 경로, 오류 코드" type="search" maxLength={200} /></div>
        <span className="api-index-count" aria-live="polite">{sections.length} / {document.sections.length}개 항목</span>
        <div className="api-doc-toc" role="navigation" aria-label="API 정의서 목차">{!query.trim() && <button type="button" aria-current={showIntro ? "page" : undefined} onClick={() => selectSection("intro")}>정의서 안내</button>}{sections.map((section) => <button type="button" key={section.id} aria-current={!showIntro && selected?.id === section.id ? "page" : undefined} onClick={() => selectSection(section.id)}>{section.title}</button>)}</div>
      </div>
      <div className="api-doc-content" ref={content}>
        <ApiDocumentBody document={document} sections={sections} selectedId={selectedId} query={query} inputSchema={reference.input_schema} />
        <ApiSectionNavigation sections={sections} selectedId={showIntro ? "intro" : selected?.id} query={query} onSelect={selectSection} />
        {!sections.length && <section className="panel empty"><Icon name="search" size={28} /><strong>검색어에 해당하는 항목이 없습니다.</strong><small>다른 필드명이나 오류 코드로 검색해 보세요.</small><button type="button" className="secondary" onClick={() => setQuery("")}>검색 초기화</button></section>}
      </div>
    </div>}
    <Dialog open={technical && Boolean(reference)} title="API 정의서 기술정보" onClose={() => setTechnical(false)} className="api-technical-dialog">{reference && <dl><dt>문서</dt><dd>{document.title}</dd><dt>입력 스키마 ID</dt><dd>{reference.input_schema.version_id}</dd><dt>필드 정의 지문</dt><dd>{reference.input_schema.content_hash}</dd></dl>}</Dialog>
  </div>;
}
