import assert from "node:assert/strict";
import test from "node:test";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";
import { runInNewContext } from "node:vm";
import { buildSync } from "esbuild";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { importCounts, importError } from "./groundTruthImport.js";

const bundle = buildSync({ entryPoints: [fileURLToPath(new URL("./R5Evaluation.jsx", import.meta.url))], bundle: true, write: false, platform: "node", format: "cjs", packages: "external", jsx: "automatic", loader: { ".css": "empty" }, logLevel: "silent" });
const module = { exports: {} };
runInNewContext(bundle.outputFiles[0].text, { module, exports: module.exports, require: createRequire(import.meta.url), process });
test("evaluation puts six risk directions before decided binary statistics without recomputing metrics", () => {
  const summary = { binary_evaluable: 120, binary_decided: 111, outcomes: {}, metrics: { accuracy: 110/111, precision: 1, recall: 55/56, f1: 110/111, coverage: 111/120, balanced_accuracy: .99, mcc: .98 },
    confusion_matrix: { tp: 55, fn: 1, abstained_positive: 4, tn: 55, fp: 0, abstained_negative: 5, expected_hold_positive: 11, expected_hold_negative: 9, expected_hold_match: 10 } };
  const before = JSON.stringify(summary), html = renderToStaticMarkup(createElement(module.exports.default, { summary, official: true }));
  for (const [label, count] of [["공격 → 정상", 1], ["공격 → 보류", 4], ["정상 → 공격", 0], ["정상 → 보류", 5], ["보류 답안 → 정탐 확정", 11], ["보류 답안 → 오탐 확정", 9]]) assert.ok(html.includes(`aria-label="${label}: ${count}"`));
  assert.ok(html.indexOf("보류 답안 → 오탐 확정") < html.indexOf("통계 상세"));
  assert.match(html, /<details class="r5-statistics"><summary>통계 상세/);
  assert.match(html, /전체 요청의 정확도가 아닙니다/); assert.doesNotMatch(html, /보류 답안에 확정 판정/);
  for (const metric of ["Accuracy", "Precision", "Recall", "F1", "Balanced Accuracy", "MCC", "Coverage"]) assert.ok(html.includes(metric));
  assert.equal(JSON.stringify(summary), before);
});
test("import categories are mutually exclusive, missing references are not counted twice", () => {
  const preview = { source_total: 15, new_count: 10, missing_reference_count: 3, duplicate_count: 2, reference_conflict_count: 1, unavailable_count: 2 };
  const counts = importCounts(preview);
  assert.deepEqual(counts, { new: 7, missing_reference: 3, duplicate: 2, reference_conflict: 1, unavailable: 2 });
  assert.equal(Object.values(counts).reduce((a, b) => a + b), preview.source_total);
});
test("import errors describe safe known codes, never arbitrary server text", () => {
  assert.match(importError({ message: "dataset_item_limit" })[0], /5,000/);
  assert.match(importError({ message: "test_must_finish_before_import" })[0], /접수도 닫아/);
  assert.match(importError({ message: "ground_truth_preview_expired" })[0], /만료/);
  assert.doesNotMatch(importError({ message: "private-payload-canary" }, "confirm").join(" "), /private-payload/);
});
