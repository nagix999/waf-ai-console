import assert from "node:assert/strict";
import test from "node:test";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";
import { runInNewContext } from "node:vm";
import { buildSync } from "esbuild";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { api } from "./api.js";
import { affectsHome } from "./readInvalidation.js";
import { caseFilterFields, formatCaseFilter, newCaseFilter, newFilterRule, normalizeCaseFilter, operatorsFor, presetCaseFilter } from "./testCaseFilters.js";
import { clearGroupSelection, groupCases, initialStatistics, referenceQuery, statisticsQuery } from "./testStatistics.js";
import { testRunQuery } from "./testRuns.js";

test("reference, AI and comparison filters are separate and presets preserve false alarm/miss/hold directions", () => {
  for (const field of ["reference_verdict", "verdict", "company_name", "signature", "waf_action", "severity", "src_port"]) assert.ok(caseFilterFields.find(row => row.key === field));
  assert.equal(caseFilterFields.some(row => ["payload", "result_json", "extra_fields"].includes(row.key)), false);
  assert.equal(presetCaseFilter("all"), null);
  assert.equal(normalizeCaseFilter(presetCaseFilter("false_positive")).rules[0].rules[0].value, "false_positive");
  assert.equal(normalizeCaseFilter(presetCaseFilter("false_negative")).rules[0].rules[0].value, "false_negative");
  assert.deepEqual(presetCaseFilter("different").rules[0].rules.map(rule => rule.value), ["false_positive", "false_negative", "abstained", "expected_abstention_mismatch"]);
});

test("official filter UI uses expected-answer terms and untrusted condition values render as text", () => {
  const code = buildSync({ entryPoints: [fileURLToPath(new URL("./TestCaseFilter.jsx", import.meta.url))], bundle: true, write: false,
    platform: "node", format: "cjs", packages: "external", jsx: "automatic", loader: { ".css": "empty" }, logLevel: "silent" }).outputFiles[0].text;
  const module = { exports: {} };
  runInNewContext(code, { module, exports: module.exports, require: createRequire(import.meta.url), process });
  const state = { filter_open: true, case_filter: presetCaseFilter("different") };
  const official = renderToStaticMarkup(createElement(module.exports.default, { state, official: true, onChange() {} }));
  assert.match(official, /기대 판정/); assert.match(official, /기대 보류/); assert.doesNotMatch(official, /참고 판정|참고 보류/);
  assert.match(official, /class="primary"/); assert.match(official, /aria-label="조건 1.1 필드"/);
  const condition = { op: "and", rules: [{ op: "and", rules: [{ field: "company_name", operator: "eq", value: '<img src=x onerror="alert(1)">' }] }] };
  const text = renderToStaticMarkup(createElement(module.exports.default, { state: { case_filter: condition }, onChange() {} }));
  assert.match(text, /&lt;img/); assert.doesNotMatch(text, /<img src=x/);
});

test("explicit groups preserve (company A OR company B) AND reference/AI conditions", () => {
  const draft = newCaseFilter();
  draft.rules[0].op = "or";
  draft.rules[0].rules = [{ ...newFilterRule(), operator: "eq", value: "A" }, { ...newFilterRule(), operator: "eq", value: "B" }];
  draft.rules.push({ op: "and", rules: [{ field: "reference_verdict", operator: "eq", value: "false_positive" }, { field: "verdict", operator: "eq", value: "true_positive" }] });
  const expression = normalizeCaseFilter(draft);
  assert.equal(expression.op, "and"); assert.equal(expression.rules[0].op, "or");
  const description = formatCaseFilter(expression, (ko) => ko);
  assert.equal(description, "(회사 같음 A OR 회사 같음 B) AND (참고 판정 같음 오탐 AND AI 판정 같음 정탐)");
  assert.ok(formatCaseFilter(expression, (_ko, en) => en).includes("Reference Verdict Equals False positive"));
  assert.ok(formatCaseFilter(expression, (ko) => ko, true).includes("기대 판정 같음 오탐"));
});

test("typed operators validate values and do not silently drop incomplete rules", () => {
  for (const value of ["", "-1", "65536", "1.5", "NaN", "Infinity", true]) {
    assert.throws(() => normalizeCaseFilter({ op: "and", rules: [{ op: "and", rules: [{ field: "src_port", operator: "gte", value }] }] }), /invalid_test_case_filter/);
  }
  assert.throws(() => normalizeCaseFilter(newCaseFilter()), /invalid_test_case_filter/);
  assert.throws(() => normalizeCaseFilter({ op: "and", rules: [{ op: "and", rules: [{ field: "verdict", operator: "eq", value: "not-a-verdict" }] }] }), /invalid_test_case_filter/);
  const draft = { op: "and", rules: [{ op: "and", rules: [{ field: "src_port", operator: "eq", value: "0" }, { field: "confidence_score", operator: "gte", value: ".75" }, { field: "signature", operator: "is_empty", value: "unused draft" }] }] };
  assert.deepEqual(normalizeCaseFilter(draft).rules[0].rules, [{ field: "src_port", operator: "eq", value: 0 }, { field: "confidence_score", operator: "gte", value: .75 }, { field: "signature", operator: "is_empty" }]);
  assert.equal(operatorsFor(caseFilterFields.find(field => field.key === "verdict")).includes("contains"), false);
});

test("condition/group/byte limits are enforced before sending requests", () => {
  const rule = { field: "company_name", operator: "eq", value: "x" };
  for (const rules of [Array.from({ length: 6 }, () => ({ op: "and", rules: [rule] })), [{ op: "and", rules: Array(21).fill(rule) }],
    [{ op: "and", rules: Array(3).fill({ ...rule, value: "가".repeat(4096) }) }]]) {
    assert.throws(() => normalizeCaseFilter({ op: "and", rules }), /invalid_test_case_filter/);
  }
});

test("only applied filters reach statistics/drilldown; drafts, case filters and exports remain distinct", () => {
  const expression = presetCaseFilter("false_negative");
  const state = { ...initialStatistics(), case_filter: expression, filter_draft: newCaseFilter(), filter_open: true };
  const query = statisticsQuery({ status: "failed", case_filter: presetCaseFilter("false_positive") }, state);
  assert.deepEqual(query.case_filter, expression);
  assert.equal(query.filter_draft, undefined); assert.equal(query.filter_open, undefined); assert.equal(query.status, undefined);
  const filters = groupCases({ statistics: state, evaluation_id: "initial" }, ["company_name", "signature"], { company_name: "A", signature: "SQL" });
  assert.deepEqual(testRunQuery(filters).case_filter, expression);
  assert.deepEqual(referenceQuery(filters), { reference_basis: "initial" });
  assert.equal(testRunQuery(clearGroupSelection(filters)).case_filter, undefined);
  assert.equal(clearGroupSelection(filters).statistics.case_filter, expression);
});

test("filtered statistics/cases use authenticated no-store POST, not URLs or home-write invalidation", async () => {
  const oldFetch = globalThis.fetch, requests = [], expression = presetCaseFilter("false_positive");
  globalThis.fetch = async (path, options) => { requests.push({ path, options }); return new Response(JSON.stringify({ fixture: true }), { headers: { "content-type": "application/json" } }); };
  try {
    await api.testStatistics("fixture-id", { group_by: "company_name,signature", case_filter: expression });
    await api.testRun("fixture-id", { case_filter: expression, group_field: "signature", group_value: "private metadata" });
    for (const { path, options } of requests) {
      assert.doesNotMatch(path, /private|case_filter|true_positive|false_positive|\?/);
      assert.equal(options.method, "POST"); assert.equal(options.credentials, "include"); assert.equal(options.cache, "no-store");
      assert.deepEqual(JSON.parse(options.body).case_filter, expression); assert.equal(affectsHome(path, options.method), false);
    }
    assert.match(requests[0].path, /statistics\/search$/); assert.match(requests[1].path, /cases\/search$/);
  } finally { globalThis.fetch = oldFetch; }
});
