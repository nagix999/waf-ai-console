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

test("the SSR fallback preserves API row order and never sorts a fetched page locally", () => {
  const html = render({ label: "테스트 표", data: [{ id: "z", name: "Zulu" }, { id: "a", name: "Alpha" }], columns,
    sorting: serverSorting("name", "asc"), onSortingChange() {} });
  assert.ok(html.indexOf("Zulu") < html.indexOf("Alpha"));
  assert.match(html, /aria-sort="ascending"/); assert.match(html, /class="table-sort"/);
  assert.match(html, /<caption class="sr-only">테스트 표/); assert.match(html, /role="region"/);
});

test("grouped headers and React checkbox headers stay semantic during server rendering", () => {
  const html = render({ label: "검토 문항", data: [{ id: "one", name: "문항 1" }],
    columns: [{ id: "selected", width: 44, header: createElement("input", { type: "checkbox", "aria-label": "전체 선택", checked: true, readOnly: true }), render: () => "선택" }, ...columns],
    headerGroups: [{ label: "문항 정보", span: 2 }], sorting: serverSorting("name", "desc"), onSortingChange() {} });
  assert.match(html, /scope="colgroup" colSpan="2">문항 정보/);
  assert.match(html, /aria-label="전체 선택"[^>]*checked/);
  assert.match(html, /aria-sort="descending"/);
});

const browserCode = buildSync({ entryPoints: [fileURLToPath(new URL("./TabulatorTable.jsx", import.meta.url))], bundle: true,
  write: false, platform: "node", format: "cjs", packages: "external", jsx: "automatic", loader: { ".css": "empty" }, logLevel: "silent" }).outputFiles[0].text;
const browserModule = { exports: {} }; runInNewContext(browserCode, { module: browserModule, exports: browserModule.exports, require: createRequire(import.meta.url), process });
const { nextServerSorting, tablePageRows, tableColumnMinWidth, sameTablePageOrder } = browserModule.exports;

test("table columns retain readable minimum widths instead of squeezing mobile summaries", () => {
  assert.equal(tableColumnMinWidth({ id: "select", width: 44 }), 44);
  assert.equal(tableColumnMinWidth({ id: "summary" }), 230);
  assert.equal(tableColumnMinWidth({ id: "reference", width: "20%" }), 160);
  assert.equal(tableColumnMinWidth({ id: "count", className: "numeric" }), 88);
  assert.equal(tableColumnMinWidth({ id: "field", width: "42%" }), 120);
});

test("Tabulator's sort control only requests the next server order", () => {
  assert.equal(JSON.stringify(nextServerSorting([], "name")), '[{"id":"name","desc":false}]');
  assert.equal(JSON.stringify(nextServerSorting([{ id: "name", desc: false }], "name")), '[{"id":"name","desc":true}]');
  assert.equal(JSON.stringify(nextServerSorting([{ id: "name", desc: true }], "name")), '[{"id":"name","desc":false}]');
  assert.equal(JSON.stringify(nextServerSorting([{ id: "name", desc: true }], "created_at")), '[{"id":"created_at","desc":false}]');
});

test("Tabulator uses private collision-safe keys without modifying public row objects", () => {
  const input = [{ name: "no id" }, { id: "same", name: "first" }, { id: "same", name: "second" }];
  const result = tablePageRows(input);
  assert.equal(new Set(result.map(row => row.__consoleRowKey)).size, input.length);
  assert.equal(result[0].__consoleRow, input[0]);
  assert.equal(result[2].__consoleRowIndex, 2);
  assert.equal(Object.keys(input[0]).join(","), "name");
  assert.equal(Object.keys(input[1]).join(","), "id,name");
  assert.equal(tablePageRows(input, row => row.name)[0].__consoleRowKey, "id:no id:0");
});

test("polling preserves row hosts only for the same supplied page keys and order", () => {
  const before = tablePageRows([{ id: "a", status: "pending" }, { id: "b", status: "pending" }]);
  const keys = before.map(row => row.__consoleRowKey);
  const update = tablePageRows([{ id: "a", status: "completed" }, { id: "b", status: "failed" }]);
  assert.equal(sameTablePageOrder(keys, update), true);
  assert.equal(sameTablePageOrder(keys, [...update].reverse()), false);
  assert.equal(sameTablePageOrder(keys, update.slice(1)), false);
  assert.equal(sameTablePageOrder(keys, tablePageRows([{ id: "a" }, { id: "other" }])), false);
  assert.equal(sameTablePageOrder(undefined, update), false);
  assert.equal(sameTablePageOrder(null, []), false);
  assert.equal(sameTablePageOrder([], []), true);
  assert.equal(update[0].__consoleRow.status, "completed");
  assert.equal(before[0].__consoleRow.status, "pending");
});

test("same-page refresh identity retains duplicate occurrences and custom row ids", () => {
  const before = tablePageRows([{ id: "same" }, { id: "same" }]);
  assert.equal(sameTablePageOrder(before.map(row => row.__consoleRowKey), tablePageRows([{ id: "same", value: 1 }, { id: "same", value: 2 }])), true);
  const custom = tablePageRows([{ signature: "one" }, { signature: "two" }], row => row.signature);
  assert.equal(sameTablePageOrder(custom.map(row => row.__consoleRowKey), tablePageRows([{ signature: "two" }, { signature: "one" }], row => row.signature)), false);
});

test("unkeyed rows never transfer cell-local state to a new row at the same index", () => {
  const original = { summary: "first row" };
  const before = tablePageRows([original]);
  const keys = before.map(row => row.__consoleRowKey);
  assert.equal(sameTablePageOrder(keys, tablePageRows([original]), before), true);
  assert.equal(sameTablePageOrder(keys, tablePageRows([{ summary: "different row" }]), before), false);
  assert.equal(sameTablePageOrder(keys, tablePageRows([{ ...original }]), before), false);
  assert.equal(sameTablePageOrder(keys, tablePageRows([original])), false);
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
