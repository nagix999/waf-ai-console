import assert from "node:assert/strict";
import test from "node:test";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";
import { runInNewContext } from "node:vm";
import { buildSync } from "esbuild";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { paginationState } from "./pagination.js";
import { initialTestComparisonState, testComparisonChange } from "./testComparison.js";

const code = buildSync({ entryPoints: [fileURLToPath(new URL("./Pagination.jsx", import.meta.url))], bundle: true, write: false, platform: "node", format: "cjs", packages: "external", jsx: "automatic", loader: { ".css": "empty" }, logLevel: "silent" }).outputFiles[0].text;
const module = { exports: {} };
runInNewContext(code, { module, exports: module.exports, require: createRequire(import.meta.url) });
const Pagination = module.exports.default;

test("pages are bounded for empty, partial final, middle and huge lists", () => {
  assert.deepEqual(paginationState(0, 25, 0).pages, []);
  assert.equal(paginationState(0, 25, 0).pageCount, 0);
  const last = paginationState(1234, 25, 1225);
  assert.equal(last.page, 50); assert.equal(last.pageCount, 50);
  assert.equal(last.firstRow, 1226); assert.equal(last.lastRow, 1234);
  assert.deepEqual(last.pages, [46, 47, 48, 49, 50]);
  assert.deepEqual(paginationState(2500, 25, 1225).pages, [48, 49, 50, 51, 52]);
  assert.equal(paginationState(10_000_000, 25, 0).pages.length, 5);
  assert.equal(paginationState(26, 25, 100).lastOffset, 25);
  assert.equal(paginationState(undefined, 0, -1).page, 0);
});

test("controls expose current page and disabled first/last/loading states", () => {
  const render = props => renderToStaticMarkup(createElement(Pagination, { total: 51, limit: 25, offset: 0, onOffsetChange() {}, ...props }));
  const first = render();
  assert.match(first, /aria-label="첫 페이지" disabled/);
  assert.match(first, /aria-label="이전 페이지" disabled/);
  assert.match(first, /aria-label="1페이지" aria-current="page"/);
  assert.match(first, /1–25 \/ 51건.*1 \/ 3페이지/);
  const last = render({ offset: 50 });
  assert.match(last, /aria-label="마지막 페이지" disabled/);
  assert.match(last, /aria-label="다음 페이지" disabled/);
  assert.equal((render({ total: 0 }).match(/disabled=""/g) || []).length, 4);
  assert.equal((render({ disabled: true }).match(/disabled=""/g) || []).length, 7);
});

test("new comparison page jumps do not change pairing, filters, sort or the other list", () => {
  const base = { ...initialTestComparisonState(), baselineId: "selected", changes_only: true, offset: 25,
    query: { q: "keep", sort_by: "name", sort_order: "asc", limit: 10, offset: 10 } };
  const rows = testComparisonChange(base, { type: "offset", value: 125 });
  assert.deepEqual(rows, { ...base, offset: 125 });
  assert.deepEqual(testComparisonChange(base, { type: "search_offset", value: 90 }), { ...base, query: { ...base.query, offset: 90 } });
});
