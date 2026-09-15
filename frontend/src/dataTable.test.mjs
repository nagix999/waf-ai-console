import assert from "node:assert/strict";
import test from "node:test";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";
import { runInNewContext } from "node:vm";
import { buildSync } from "esbuild";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";

const code = buildSync({ entryPoints: [fileURLToPath(new URL("./DataTable.jsx", import.meta.url))], bundle: true,
  write: false, platform: "node", format: "cjs", packages: "external", jsx: "automatic", loader: { ".css": "empty" }, logLevel: "silent" }).outputFiles[0].text;
const module = { exports: {} }; runInNewContext(code, { module, exports: module.exports, require: createRequire(import.meta.url), process });
const { default: DataTable, serverSorting, changedSort, Table } = module.exports;
const render = props => renderToStaticMarkup(createElement(DataTable, props));
const columns = [{ id: "name", header: "이름", sortable: true, render: row => row.name }];

test("TanStack preserves API row order and never sorts a fetched page locally", () => {
  const html = render({ label: "테스트 표", data: [{ id: "z", name: "Zulu" }, { id: "a", name: "Alpha" }], columns,
    sorting: serverSorting("name", "asc"), onSortingChange() {} });
  assert.ok(html.indexOf("Zulu") < html.indexOf("Alpha"));
  assert.match(html, /aria-sort="ascending"/); assert.match(html, /class="table-sort"/);
  assert.match(html, /<caption class="sr-only">테스트 표/); assert.match(html, /role="region"/);
});

test("fixed-order tables expose no misleading sort control and escape hostile data", () => {
  const html = render({ label: "고정 순서", data: [{ id: "hidden", name: '<img src="https://blocked.invalid/">' }], columns });
  assert.doesNotMatch(html, /class="table-sort"|<img|hidden/); assert.match(html, /&lt;img/);
  assert.match(render({ data: [], columns, empty: "결과 없음" }), /colSpan="1"[^>]*>결과 없음/);
  assert.match(renderToStaticMarkup(createElement(Table, { className: "matrix" }, createElement("caption", null, "행렬"))), /console-table matrix/);
});

test("controlled sort updates reset only pagination and preserve allowed query fields", () => {
  const current = serverSorting("name", "asc");
  const result = changedSort(() => [{ id: "created_at", desc: true }], current);
  assert.equal(result.sort_by, "created_at"); assert.equal(result.sort_order, "desc"); assert.equal(result.offset, 0);
  assert.equal(Object.keys(result).length, 3); assert.equal(current[0].id, "name");
});
