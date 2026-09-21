import assert from "node:assert/strict";
import test from "node:test";
import { buildSync } from "esbuild";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";
import { runInNewContext } from "node:vm";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { comparisonDelta } from "./testComparison.js";

function load(file) {
  const code = buildSync({ entryPoints: [fileURLToPath(new URL(file, import.meta.url))], bundle: true, write: false, platform: "node", format: "cjs", packages: "external", jsx: "automatic", loader: { ".css": "empty" }, logLevel: "silent" }).outputFiles[0].text;
  const module = { exports: {} }; runInNewContext(code, { module, exports: module.exports, require: createRequire(import.meta.url), process, URL, Date }); return module.exports;
}
const render = (component, props) => renderToStaticMarkup(createElement(component, props));

test("comparison uses percentage points and preserves missing values without inferring improvement", () => {
  assert.equal(comparisonDelta(.7, .75), "+5.0 %p");
  assert.equal(comparisonDelta(.75, .7), "−5.0 %p");
  assert.equal(comparisonDelta(0, 0), "0.0 %p");
  assert.equal(comparisonDelta(12, 10, false), "−2");
  for (const value of [null, undefined, NaN, "0", Infinity]) assert.equal(comparisonDelta(value, .5), "—");
});

test("official and reference overviews identify their basis without implying Production accuracy", () => {
  const Overview = load("./EvaluationOverview.jsx").default;
  const summary = { total: 3, binary_evaluable: 2, binary_decided: 1, metrics: { accuracy: 1, coverage: .5 } };
  const official = render(Overview, { summary, evaluationMode: "ground_truth" });
  assert.match(official, /공식 평가 지표/); assert.match(official, /실행 시 고정한 승인 답안 기준/); assert.match(official, /운영 전체 정확도는 아닙니다/);
  const reference = render(Overview, { summary }); assert.match(reference, /참고 답안 표본/); assert.doesNotMatch(reference, /공식 평가 지표/);
  const missing = render(Overview, { summary: { metrics: {} } }); assert.match(missing, /확정 —건/); assert.doesNotMatch(missing, /확정 0건/);
});

test("assignment diff uses saved and proposed roles, including inherited Verifier and editor disabling", () => {
  const { AssignmentChanges } = load("./AgentSettings.jsx");
  const role = { primary_profile_id: "a", verifier_profile_id: null, evidence_editor_enabled: true, evidence_editor_profile_id: null };
  const catalog = { profiles: [{ id: "a", name: "<old-model>" }, { id: "b", name: "new-model" }], assignments: { production: role, test: role } };
  const html = render(AssignmentChanges, { catalog, draft: { production: { ...role, primary_profile_id: "b", evidence_editor_enabled: false }, test: role } });
  assert.equal((html.match(/<li>/g) || []).length, 3); assert.match(html, /Primary/); assert.match(html, /Verifier/); assert.match(html, /사용 안 함/);
  assert.match(html, /&lt;old-model&gt;/); assert.doesNotMatch(html, /<old-model>/);
  assert.match(render(AssignmentChanges, { catalog, draft: catalog.assignments }), /변경 사항이 없습니다/);
});

test("API keys show metadata only with ten-row desktop pagination and collapsed secondary actions", () => {
  const { ServiceApiKeysView } = load("./ServiceApiKeys.jsx");
  const items = Array.from({ length: 12 }, (_, n) => ({ id: `private-id-${n}`, name: `Collector ${n}`, source_system: "fixture", purpose: "test", scopes: ["ingest"], api_key: "NEVER_RENDER_THIS", key_prefix: "HIDDEN_PREFIX" }));
  const html = render(ServiceApiKeysView, { state: { catalog: { items }, draft: { scopes: [] } }, controller: {} });
  assert.match(html, /<table/); assert.match(html, /API 키 목록 페이지/); assert.match(html, /Collector 9/); assert.doesNotMatch(html, /Collector 10|NEVER_RENDER_THIS|private-id-|HIDDEN_PREFIX/);
  assert.match(html, /관리" aria-expanded="false"/); assert.doesNotMatch(html, />삭제<\/button>/);
});

test("overflow trigger has a visible affordance and no mounted destructive action until opened", () => {
  const More = load("./MoreActions.jsx").default;
  const html = render(More, { children: createElement("button", null, "삭제") });
  assert.match(html, /aria-label="더보기"/); assert.equal((html.match(/<circle/g) || []).length, 3); assert.match(html, /aria-expanded="false"/); assert.doesNotMatch(html, />삭제<\/button>/);
});
