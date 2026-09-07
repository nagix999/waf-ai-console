import assert from "node:assert/strict";
import test from "node:test";
import { fileURLToPath } from "node:url";
import { buildSync } from "esbuild";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";

// Exercise the real JSX with the existing Vite/esbuild toolchain. No DOM,
// network, files generated on disk, added dependencies or decoder execution.
const bundled = buildSync({
  entryPoints: [fileURLToPath(new URL("./DecodingView.jsx", import.meta.url))],
  bundle: true, write: false, platform: "node", format: "esm", jsx: "automatic", logLevel: "silent",
  define: { "process.env.NODE_ENV": '"production"' },
}).outputFiles[0].text;
const { default: DecodingView } = await import(`data:text/javascript;base64,${Buffer.from(bundled).toString("base64")}`);

const fixture = (items) => ({ decoder_version: "synthetic-v2", scan_truncated: false, scanned_chars: 100, total_chars: 100, items, warnings: [] });
const render = (decoding) => renderToStaticMarkup(createElement(DecodingView, { decoding }));

test("actual comparison component labels unchanged JNDI as static inspection and never creates network links", () => {
  const original = "${jndi:ldap://synthetic.invalid/a}";
  const html = render(fixture([{ id: "plain-jndi", field: "payload", start: 0, end: original.length, original, decoded: original, steps: [], warnings: ["jndi_lookup_not_executed"] }]));
  assert.match(html, /변환하지 않음 · 정적 확인 안내/);
  assert.match(html, /변환 없이 보존한 문자열/);
  assert.match(html, /JNDI 조회와 외부 연결은 수행하지 않았습니다/);
  assert.match(html, /공격 성공을 확인할 수 없습니다/);
  assert.doesNotMatch(html, /변환 단계 0개|<a\b|<iframe\b/);
  assert.equal(html.split(original).length - 1, 2);
});

test("actual comparison component shows localized multi-step labels and escapes hostile decoded text", () => {
  const original = "%u003Cscript%u003E";
  const decoded = "<script>synthetic</script>\u0000\u202E";
  const item = { id: "extended", field: "payload.query", start: 0, end: original.length, original, decoded,
    steps: [{ encoding: "url_percent_u", input: original, output: decoded }, { encoding: "log4j_lookup_static", input: decoded, output: decoded }],
    warnings: ["legacy_percent_u_candidate", "unresolved_lookup"] };
  const html = render(fixture([item]));
  assert.match(html, /변환 단계 2개 보기/);
  assert.match(html, /이전 방식 %uNNNN 해석 후보/);
  assert.match(html, /실행 없는 정적 해석/);
  assert.match(html, /&lt;script&gt;synthetic&lt;\/script&gt;⟦U\+0000⟧⟦U\+202E⟧/);
  assert.doesNotMatch(html, /<script\b|\u0000|\u202E/);
  assert.match(html, /화면에서 복사하면 이 표시 문자열이 복사됩니다/);
  assert.equal(item.decoded, decoded);
});

test("actual comparison component does not mistake count-limited full character scans for exhaustive inspection", () => {
  const html = render({ ...fixture([]), scan_truncated: true, warnings: ["item_limit_reached"] });
  assert.match(html, /100 \/ 100/);
  assert.match(html, /크기·개수 등 도구 처리 한도/);
  assert.match(html, /표시 항목 개수 상한/);
  assert.match(html, /인코딩이나 공격이 없다는 뜻은 아닙니다/);
});
