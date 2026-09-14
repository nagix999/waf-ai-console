import assert from "node:assert/strict";
import test from "node:test";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";
import { runInNewContext } from "node:vm";
import { buildSync } from "esbuild";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { JSON_PAGE_SIZE, JSON_PREVIEW_LENGTH, jsonStringPreview, jsonTokens, jsonType, parseJsonRecord } from "./jsonInspection.js";
import { recordText, textMatches } from "./inspection.js";

function component(file) {
  const bundle = buildSync({ entryPoints: [fileURLToPath(new URL(file, import.meta.url))], bundle: true, write: false, platform: "node", format: "cjs", packages: "external", jsx: "automatic", loader: { ".css": "empty" }, logLevel: "silent" }).outputFiles[0].text;
  const module = { exports: {} };
  runInNewContext(bundle, { module, exports: module.exports, require: createRequire(import.meta.url), process, URL, URLSearchParams, setTimeout, clearTimeout });
  return module.exports.default;
}
const TextInspector = component("./TextInspector.jsx");
const AgentHistory = component("./AgentHistory.jsx");
const render = (value, props = {}) => renderToStaticMarkup(createElement(TextInspector, { value, ...props }));

test("JSON values default to a typed tree; strings and absent records remain text", () => {
  const html = render({ text: "97", score: 0.97, checked: true, retry: false, missing: null, array: [], object: {} }, { label: "결과 JSON" });
  assert.match(html, /결과 JSON 트리/);
  assert.match(html, /aria-pressed="true">트리/);
  assert.match(html, /json-string">&quot;97&quot;/);
  assert.match(html, /json-number">0.97/);
  assert.match(html, /json-boolean">true/);
  assert.match(html, /json-boolean">false/);
  assert.match(html, /json-null">null/);
  assert.match(html, /배열 · 0개/);
  assert.match(html, /객체 · 0개/);
  assert.match(html, /결과 JSON에서 찾기/);
  assert.match(html, /전체 복사/);
  assert.doesNotMatch(html, /<pre|role="tree"/); // Native disclosure buttons, not an incomplete ARIA tree.
  for (const value of ['{"payload":"not parsed"}', "GET / HTTP/1.1\r\nCookie: raw=+%20\r\n\r\n", "", null]) {
    const textHtml = render(value);
    assert.doesNotMatch(textHtml, /json-tree|JSON 값의 색상|보기 방식/);
  }
  assert.match(render(null), /기록된 내용이 없습니다/);
});

test("containers are lazily mounted and array pages are bounded", () => {
  const html = render({ nested: { hidden: "collapsed-value-canary" }, items: ["array-value-canary"] });
  assert.match(html, /aria-expanded="false"[^>]*aria-label="nested 펼치기"/);
  assert.match(html, /aria-controls=/);
  assert.match(html, /\{…\}/);
  assert.match(html, /\[…\]/);
  assert.doesNotMatch(html, /collapsed-value-canary|array-value-canary/);
  const items = Array.from({ length: 501 }, (_, index) => `entry-${index}-canary`);
  const page = render(items);
  assert.match(page, new RegExp(`entry-${JSON_PAGE_SIZE - 1}-canary`));
  assert.doesNotMatch(page, new RegExp(`entry-${JSON_PAGE_SIZE}-canary`));
  assert.match(page, /다음 50개 보기/);
  assert.match(page, /451개 남음/);
  assert.ok(recordText(items).includes("entry-500-canary"));
});

test("tree matches copied JSON without changing the source object or parsing embedded strings", () => {
  const original = Object.freeze({ omitted: undefined, count: 0, empty: "", payload: '{"a":true}', actualNull: null });
  const before = recordText(original);
  const html = render(original);
  assert.doesNotMatch(html, /&quot;omitted&quot;|json-undefined/);
  assert.match(html, /json-number">0/);
  assert.match(html, /json-string">&quot;&quot;/);
  assert.match(html, /json-string">&quot;\{\\&quot;a\\&quot;:true\}&quot;/);
  assert.equal(recordText(original), before);
  assert.ok(Object.hasOwn(original, "omitted"));
});

test("long strings and keys have visible previews; full JSON remains searchable and copyable", () => {
  const long = "가".repeat(20000) + "hidden-tail-canary";
  const source = { long, ["key".repeat(2000)]: true };
  const html = render(source);
  assert.match(html, /자 더 있음/);
  assert.match(html, /문자열 더 보기/);
  assert.doesNotMatch(html, /hidden-tail-canary/);
  assert.ok(html.length < 6500);
  assert.equal(textMatches(recordText(source), "hidden-tail-canary").positions.length, 1);
  const preview = jsonStringPreview("가".repeat(JSON_PREVIEW_LENGTH - 1) + "😀끝");
  assert.equal(preview.remaining, 3);
  assert.equal(JSON.parse(preview.text), "가".repeat(JSON_PREVIEW_LENGTH - 1));
});

test("untrusted JSON keys and values render only as text, never HTML or active links", () => {
  const value = JSON.parse('{"__proto__":{"safe":true},"<img src=x onerror=alert(1)>":"<script>alert(1)</script>","url":"https://example.invalid/","cookie":"a=+%20\\r\\nCookie: raw"}');
  const before = recordText(value);
  const html = render(value);
  assert.match(html, /&lt;img src=x onerror=alert\(1\)&gt;/);
  assert.match(html, /&lt;script&gt;alert\(1\)&lt;\/script&gt;/);
  assert.doesNotMatch(html, /<script|<img\b|<a\b|<iframe/);
  assert.match(html, /Cookie: raw/);
  assert.equal(recordText(value), before);
  assert.equal({}.safe, undefined);
});

test("syntax tokens preserve exact formatted JSON and distinguish types, keys and escaped strings", () => {
  const source = { "quote\"key": "true null 12 \\ \"\n", yes: true, no: false, none: null, negative: -2.5e30, zero: 0, arr: ["<script>", 4] };
  const text = recordText(source);
  const tokens = jsonTokens(text);
  assert.equal(tokens.map(token => token.text).join(""), text);
  for (const token of tokens) assert.equal(text.slice(token.start, token.start + token.text.length), token.text);
  assert.equal(tokens.find(token => token.text === '"yes"').type, "key");
  assert.equal(tokens.find(token => token.text === "true").type, "boolean");
  assert.equal(tokens.find(token => token.text === "null").type, "null");
  assert.equal(tokens.find(token => token.text === "-2.5e+30").type, "number");
  assert.equal(tokens.find(token => token.text.startsWith('"true null')).type, "string");
  assert.equal(jsonType(null), "null");
  assert.equal(jsonType([]), "array");
  assert.equal(jsonType({}), "object");
});

test("large text and dense JSON fall back to full plain JSON instead of an unbounded syntax DOM", () => {
  for (const value of [{ huge: "a".repeat(300000) }, Array.from({ length: 10000 }, () => true)]) {
    const text = recordText(value);
    assert.deepEqual(jsonTokens(text), [{ text, type: "plain", start: 0 }]);
  }
});

test("Agent stage output uses the shared JSON tree without exposing unselected input or metadata", () => {
  // Match AgentStepResponse: input/output are decrypted JSON STRINGS, unlike
  // metadata/tool_calls. An object-only fixture missed this production bug.
  const runs = [{ id: "run", status: "completed", steps: [{ id: "step", step_type: "parser", status: "completed", output: '{"parse_status":"partial","count":2}', input: '{"secret":"hidden-input"}', metadata: { secret: "hidden-metadata" } }] }];
  const html = renderToStaticMarkup(createElement(AgentHistory, { runs }));
  assert.match(html, /단계 처리 결과 트리/);
  assert.match(html, /json-string">&quot;partial&quot;/);
  assert.doesNotMatch(html, /hidden-input|hidden-metadata/);
});

test("serialized Agent documents support typed trees and an unchanged raw view, only when opted in", () => {
  const source = ' \r\n{ "score":9.7e-1,"ok":true,"missing":null,"payload":"Cookie: demo=+%20\\r\\n","items":[] }\n';
  const parsed = parseJsonRecord(source);
  assert.equal(parsed.value.score, 0.97);
  assert.equal(parsed.value.payload, 'Cookie: demo=+%20\r\n');
  assert.equal(parsed.text, JSON.stringify(JSON.parse(source), null, 2));
  const html = render(source, {jsonText:true, label:"단계 분석 입력"});
  assert.match(html, /단계 분석 입력 트리/);
  assert.match(html, /json-number">0.97/);
  assert.match(html, /json-boolean">true/);
  assert.match(html, /json-null">null/);
  assert.match(html, />원문<\/button>/);
  assert.match(html, /저장된 원문을 복사/);
  assert.doesNotMatch(render(source), /json-tree/);
  assert.equal(recordText(source), source);
  assert.match(render('[{"item":1},2,true,null]', {jsonText:true}), /배열 · 4개/);
});

test("invalid, non-container, duplicate-key, rounded and oversized JSON keep the original text", () => {
  const unsafe = [
    '{"x":', '{"x":1,}', '```json\n{"x":1}\n```', 'GET / HTTP/1.1\r\n\r\n{"x":1}',
    'null', 'true', '123', '"text"', JSON.stringify('{"nested":"double encoded"}'),
    '{"x":1,"x":2}', '{"x":1,"\\u0078":2}', '{"a":{"x":1,"x":2}}',
    '{"x":9007199254740993}', '{"x":1e400}', '{"x":1e-400}', '{"x":1.0000000000000001}', '{"x":-0}',
    '{"x":"' + 'a'.repeat(2097152) + '"}',
    '['.repeat(129) + '0' + ']'.repeat(129),
  ];
  for (const source of unsafe) assert.equal(parseJsonRecord(source), null);
  const source = '{"number":9007199254740993}';
  const html = render(source, {jsonText:true});
  assert.doesNotMatch(html, /json-tree/);
  assert.match(html, /9007199254740993/);
  for (const source of ['{"a":{"x":1},"b":{"x":2}}', '[{"x":1},{"x":2}]', '{"x":0.8499999999999999}', '{"x":1.2500e2}']) {
    assert.ok(parseJsonRecord(source));
  }
});

test("embedded model input can be explicitly inspected, without auto-parsing payloads or prompts", () => {
  const source = JSON.stringify({user_input:'{"event":{"method":"POST"},"count":2}', payload:'{"must":"remain a string"}', system_instructions:'{"prompt":"not a structured record"}'});
  const html = render(source, {jsonText:true});
  assert.match(html, /user_input JSON 펼치기/);
  assert.doesNotMatch(html, /payload JSON 펼치기|system_instructions JSON 펼치기|json-embedded/);
  assert.match(html, /json-string/);
  assert.equal(recordText(source), source);
});

test("serialized JSON content is escaped and never creates active links or scripts", () => {
  const source = '{"<img src=x>":"<script>window.executed=true</script>","url":"https://example.invalid"}';
  const html = render(source, {jsonText:true});
  assert.match(html, /&lt;img src=x&gt;/);
  assert.match(html, /&lt;script&gt;/);
  assert.doesNotMatch(html, /<img|<script|<a\b/);
});
