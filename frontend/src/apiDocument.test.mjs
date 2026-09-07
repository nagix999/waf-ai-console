import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import { filterApiSections, parseApiDocument } from "./apiDocument.js";

function allBlocks(document) {
  return [...document.intro, ...document.sections.flatMap((section) => section.blocks)];
}

function allText(document) {
  return [document.title, ...document.sections.map((section) => section.title), ...allBlocks(document).flatMap((block) => {
    if (block.type === "list") return block.items;
    if (block.type === "table") return [...block.headers, ...block.rows.flat()];
    return block.text;
  })].join("\n");
}

test("empty documents have a stable empty shape", () => {
  for (const input of ["", "\n \n", undefined, null]) {
    assert.deepEqual(parseApiDocument(input), { title: "", intro: [], sections: [] });
  }
});

test("title, introduction, section order, subheadings and empty sections are retained", () => {
  const document = parseApiDocument("# API\n\n첫 문장\n다음 줄\n\n## 인증\n### 헤더\n설명\n## 빈 구간\n## 응답\n# 추가 제목\n마지막");
  assert.equal(document.title, "API");
  assert.deepEqual(document.intro, [{ type: "paragraph", text: "첫 문장\n다음 줄" }]);
  assert.deepEqual(document.sections, [
    { id: "api-section-0", title: "인증", blocks: [{ type: "heading", level: 3, text: "헤더" }, { type: "paragraph", text: "설명" }] },
    { id: "api-section-1", title: "빈 구간", blocks: [] },
    { id: "api-section-2", title: "응답", blocks: [{ type: "heading", level: 1, text: "추가 제목" }, { type: "paragraph", text: "마지막" }] },
  ]);
});

test("ordered and unordered lists preserve text, starting number and block order", () => {
  const document = parseApiDocument("## 접수\n3. 세 번째\n4. 네 번째\n- 다음 항목\n* 다른 불릿\n\n끝 문단");
  assert.deepEqual(document.sections[0].blocks, [
    { type: "list", ordered: true, start: 3, items: ["세 번째", "네 번째"] },
    { type: "list", ordered: false, start: 1, items: ["다음 항목", "다른 불릿"] },
    { type: "paragraph", text: "끝 문단" },
  ]);
});

test("HTML, links and literal HTTP payload stay plain strings in every block", () => {
  const html = '<img src=x onerror="alert(1)">';
  const literal = "GET /?q=%27%20OR%201%3D1-- HTTP/1.1\\r\\nCookie: session=synthetic\\r\\n";
  const document = parseApiDocument(`# API\n${html}\n\n## ${html}\n- [open](javascript:alert(1))\n\n| 항목 | 값 |\n| --- | --- |\n| payload | ${html} |\n\n\`\`\`http\n${literal}\n${html}\n\`\`\``);
  assert.equal(document.intro[0].text, html);
  assert.equal(document.sections[0].title, html);
  assert.deepEqual(document.sections[0].blocks[0].items, ["[open](javascript:alert(1))"]);
  assert.equal(document.sections[0].blocks[1].rows[0][1], html);
  assert.equal(document.sections[0].blocks[2].text, `${literal}\n${html}\n`);
  assert.equal(JSON.stringify(document).includes("<img"), true);
});

test("fenced examples preserve spaces, tabs, blank lines and original CRLF endings", () => {
  const content = '\r\n  {"payload": "synthetic\\r\\n"}  \r\n\t\r\n';
  const document = parseApiDocument("# API\r\n\r\n```json\r\n" + content + "```\r\n");
  assert.deepEqual(document.intro, [{ type: "code", language: "json", text: content }]);
});

test("empty, blank-only and unclosed fences do not lose their content", () => {
  assert.equal(parseApiDocument("```\n```").intro[0].text, "");
  assert.equal(parseApiDocument("```\n\n```").intro[0].text, "\n");
  assert.equal(parseApiDocument("```http\n  GET / HTTP/1.1\n\n").intro[0].text, "  GET / HTTP/1.1\n\n");
});

test("headings and shorter or different fences inside code never become document structure", () => {
  const document = parseApiDocument("````text\n## literal heading\n```\n~~~\n# literal title\n````\n## Real section\nDone");
  assert.equal(document.title, "");
  assert.equal(document.intro[0].text, "## literal heading\n```\n~~~\n# literal title\n");
  assert.equal(document.sections.length, 1);
  assert.equal(document.sections[0].title, "Real section");
  assert.equal(parseApiDocument("~~~json\n{}\n~~~~").intro[0].text, "{}\n");
});

test("tables preserve all cells including empty cells and extra columns", () => {
  const document = parseApiDocument("## 필드\n| 이름 | 설명 |\n| :--- | ---: |\n| id | 고유 ID |\n| | null | 추가 정보 |\n\n끝");
  assert.deepEqual(document.sections[0].blocks, [
    { type: "table", headers: ["이름", "설명"], rows: [["id", "고유 ID"], ["", "null", "추가 정보"]] },
    { type: "paragraph", text: "끝" },
  ]);
});

test("pipes in inline code spans or escaped text do not split table cells", () => {
  const document = parseApiDocument("| key | expression |\n| --- | --- |\n| `a|b` | ``c`|d`` |\n| escaped | left\\|right |\n| unmatched | `text | last |");
  assert.deepEqual(document.intro[0].rows, [
    ["`a|b`", "``c`|d``"],
    ["escaped", "left\\|right"],
    ["unmatched", "`text", "last"],
  ]);
});

test("table-like prose without a matching delimiter is not consumed as a table", () => {
  assert.deepEqual(parseApiDocument("left | right\nnot a delimiter\n\nnext").intro, [
    { type: "paragraph", text: "left | right\nnot a delimiter" },
    { type: "paragraph", text: "next" },
  ]);
  assert.equal(parseApiDocument("left | right\n--- | ---\na | b").intro[0].type, "table");
});

test("section search covers titles, paragraphs, lists, tables and code while retaining order", () => {
  const document = parseApiDocument("## Token 제목\n일반 문단\n## 둘\n- TOKEN 항목\n## 셋\n| 헤더 | 설명 |\n| --- | --- |\n| key | token 셀 |\n## 넷\n```http\nX-TOKEN: synthetic\n```\n## 다섯\n### TOKEN 하위제목\n## 여섯\n다른 내용");
  const before = JSON.stringify(document);
  const found = filterApiSections(document.sections, "  ToKeN  ");
  assert.deepEqual(found.map((section) => section.id), ["api-section-0", "api-section-1", "api-section-2", "api-section-3", "api-section-4"]);
  assert.equal(found[0], document.sections[0]);
  assert.equal(filterApiSections(document.sections, "  "), document.sections);
  assert.deepEqual(filterApiSections(document.sections, "missing"), []);
  assert.equal(filterApiSections(document.sections, "다른 내용")[0].id, "api-section-5");
  assert.equal(JSON.stringify(document), before);
});

test("the canonical Production API document retains every example and meaningful source line", () => {
  const source = readFileSync(new URL("../../docs/Production_API_v0.1.md", import.meta.url), "utf8");
  const document = parseApiDocument(source);
  assert.equal(document.title, "Production WAF Analysis API v0.2.0");
  const sectionTitles = source.split(/\r?\n/).filter((line) => /^## /.test(line)).map((line) => line.slice(3));
  assert.deepEqual(document.sections.map((section) => section.title), sectionTitles);
  const examples = [...source.matchAll(/^```[^\r\n]*\r?\n([\s\S]*?)^```[ \t]*(?:\r?\n|$)/gm)].map((match) => match[1]);
  assert.deepEqual(allBlocks(document).filter((block) => block.type === "code").map((block) => block.text), examples);

  const retained = allText(document);
  let inCode = false;
  for (const sourceLine of source.split(/\r?\n/)) {
    const line = sourceLine.trim();
    if (line.startsWith("```")) { inCode = !inCode; continue; }
    if (!line || inCode) continue;
    if (line.startsWith("|")) {
      const cells = line.split("|").slice(1, -1).map((cell) => cell.trim());
      if (cells.every((cell) => /^:?-{3,}:?$/.test(cell))) continue;
      for (const cell of cells) assert.ok(retained.includes(cell), `Missing table cell: ${cell}`);
    } else {
      const text = line.replace(/^(?:#{1,6}[ \t]+|[-+*][ \t]+|\d+[.)][ \t]+)/, "");
      assert.ok(retained.includes(text), `Missing source line: ${text}`);
    }
  }
  assert.ok(filterApiSections(document.sections, "event_id_mismatch").length);
  assert.ok(filterApiSections(document.sections, "X-API-Key: <SERVICE_API_KEY>").length);
});
