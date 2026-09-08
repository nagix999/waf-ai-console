import assert from "node:assert/strict";
import test from "node:test";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";
import { runInNewContext } from "node:vm";
import { buildSync } from "esbuild";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { apiSectionNeighbors, parseApiDocument, filterApiSections } from "./apiDocument.js";

const bundled = buildSync({ entryPoints: [fileURLToPath(new URL("./ProductionApi.jsx", import.meta.url))], bundle: true, write: false, platform: "node", format: "cjs", packages: "external", jsx: "automatic", loader: { ".css": "empty" }, logLevel: "silent" }).outputFiles[0].text;
const module = { exports: {} }; runInNewContext(bundled, { module, exports: module.exports, require: createRequire(import.meta.url), process, URL });
const { ApiDocumentBody, ApiSectionNavigation } = module.exports;
const document = parseApiDocument("# 예시 API\n안내 본문\n## 인증\n인증 본문\n## 응답\n응답 본문\n```json\n{\"name\":\"합성이라는 사용자 입력\"}\n```\n## 안전한 표시\n<img src=x onerror=alert(1)>");
const render = (extra = {}) => renderToStaticMarkup(createElement(ApiDocumentBody, { document, sections: document.sections, selectedId: "api-section-0", ...extra }));

test("API reader renders one selected section, retaining complete examples and user text", () => {
  const first = render(); assert.match(first, /인증 본문/); assert.doesNotMatch(first, /응답 본문|안내 본문/);
  const second = render({ selectedId: "api-section-1" }); assert.match(second, /응답 본문/); assert.match(second, /합성이라는 사용자 입력/); assert.doesNotMatch(second, /인증 본문|안내 본문/);
  assert.match(render({ selectedId: "intro" }), /안내 본문/); assert.doesNotMatch(render({ selectedId: "intro" }), /인증 본문|응답 본문/);
});

test("search falls back to its first matching section and never leaves unrelated content visible", () => {
  const found = filterApiSections(document.sections, "응답");
  assert.match(render({ sections: found, selectedId: "api-section-0", query: "응답" }), /응답 본문/);
  assert.equal(render({ sections: [], query: "존재하지 않음" }), "");
  assert.doesNotMatch(render({ selectedId: "api-section-2" }), /<img/);
});

test("generated schema IDs and hashes stay out of the body without changing source Markdown", () => {
  const inputSchema = { version_id: "synthetic-id", version_number: 3, content_hash: "a".repeat(64) };
  const markdown = `# API\n## 입력\n적용 입력 스키마: **v3** · \`synthetic-id\`\n정의 SHA-256: \`${inputSchema.content_hash}\`\n\n입력 설명`;
  const doc = parseApiDocument(markdown);
  const html = render({ document: doc, sections: doc.sections, inputSchema });
  assert.match(html, /v3/); assert.match(html, /입력 설명/); assert.doesNotMatch(html, /synthetic-id|a{64}/);
  assert.match(doc.sections[0].blocks[0].text, /synthetic-id/);
});

test("API section navigation follows document order without wrapping at either boundary", () => {
  const sections = document.sections;
  assert.deepEqual(apiSectionNeighbors(sections, "intro"), { previous: null, next: sections[0] });
  assert.deepEqual(apiSectionNeighbors(sections, sections[0].id), { previous: { id: "intro", title: "정의서 안내" }, next: sections[1] });
  assert.deepEqual(apiSectionNeighbors(sections, sections.at(-1).id), { previous: sections[1], next: null });
  const html = renderToStaticMarkup(createElement(ApiSectionNavigation, { sections, selectedId: sections[1].id, onSelect() {} }));
  assert.match(html, /이전 항목: 인증/); assert.match(html, /다음 항목: 안전한 표시/);
  assert.match(html, /←/); assert.match(html, /→/);
});

test("API filtered navigation stays within search results and has no controls for empty or single matches", () => {
  const sections = [document.sections[0], document.sections[2]];
  assert.deepEqual(apiSectionNeighbors(sections, sections[0].id, "검색"), { previous: null, next: sections[1] });
  assert.deepEqual(apiSectionNeighbors(sections, sections[1].id, "검색"), { previous: sections[0], next: null });
  for (const filtered of [[], [sections[0]]]) assert.equal(renderToStaticMarkup(createElement(ApiSectionNavigation, { sections: filtered, selectedId: sections[0].id, query: "검색", onSelect() {} })), "");
});
