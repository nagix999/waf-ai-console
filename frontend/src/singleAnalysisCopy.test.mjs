import assert from "node:assert/strict";
import test from "node:test";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";
import { runInNewContext } from "node:vm";
import { buildSync } from "esbuild";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { api } from "./api.js";
import { transformSingleAnalysisTemplate } from "./singleAnalysisTemplate.js";

const bundle = buildSync({ entryPoints: [fileURLToPath(new URL("./App.jsx", import.meta.url))], bundle: true,
  write: false, platform: "node", format: "cjs", packages: "external", jsx: "automatic",
  loader: { ".css": "empty", ".md": "text" }, logLevel: "silent" });
const module = { exports: {} };
runInNewContext(bundle.outputFiles[0].text, { module, exports: module.exports, require: createRequire(import.meta.url), process, URL, Date });
const { SingleTest, TestAnalysisPage, AnalysisResultsPage, AnalysisTable } = module.exports;
const render = (Component, props) => renderToStaticMarkup(createElement(Component, props));
const identifier = "11111111-1111-4111-8111-111111111111";
const source = () => transformSingleAnalysisTemplate({ source_analysis_id: identifier, internal_only: true,
  event: { event_id: "synthetic-event", company_name: "Synthetic company", src_ip: "192.0.2.1", dest_ip: "198.51.100.1",
    src_port: 0, dest_port: null, waf_vendor: "fixture-vendor", waf_action: "D", signature: "fixture-signature",
    event_name: "Synthetic event", payload: '<script>synthetic-only</script>\r\n', extra_fields: { score: 0, enabled: false, empty: null } },
  name: "Source Test", expected_verdict: "false_positive", difficulty: "hard", test_category: "fixture-category", case_name: "Synthetic case",
  initial_assessment: { initial_verdict: "true_positive", initial_probability: 0, initial_model_version: "fixture-model" } });

test("copied single form prepopulates all inputs and makes optional fields visible without execution", () => {
  let created = 0;
  const html = render(SingleTest, { sourceTemplate: source(), onCreated: () => created++ });
  for (const value of ["Source Test", "Synthetic company", "192.0.2.1", "198.51.100.1", "fixture-vendor", "fixture-signature", "Synthetic event", "synthetic-event", "fixture-model", "fixture-category", "Synthetic case"]) assert.ok(html.includes(value), value);
  assert.match(html, /&lt;script&gt;synthetic-only&lt;\/script&gt;/);
  assert.doesNotMatch(html, /<script|id="single-test-extra" hidden/);
  assert.match(html, /value="false_positive" selected/);
  assert.match(html, /value="true_positive" selected/);
  assert.match(html, /value="0"/);
  assert.match(html, /&quot;enabled&quot;: false/);
  assert.match(html, /&quot;empty&quot;: null/);
  assert.match(html, /vLLM/);
  assert.equal(created, 0);
});

test("ordinary new single test remains blank and does not claim copied input", () => {
  const html = render(SingleTest, {});
  assert.match(html, /id="single-test-extra" hidden/);
  assert.doesNotMatch(html, /기존 입력을 가져왔습니다|synthetic-event|Source Test/);
});

test("source model restrictions are readable without exposing technical loading codes", () => {
  const loading = render(SingleTest, { sourceTemplate: source(), disabledReason: "configuration_unavailable" });
  assert.doesNotMatch(loading, /configuration_unavailable/);
  assert.match(loading, /type="submit"[^>]*disabled/);
  const message = "이 입력은 내부 전용입니다. 모든 분석 모델을 vLLM으로 선택하세요.";
  const blocked = render(SingleTest, { sourceTemplate: source(), internalModelBlocked: true, disabledReason: message });
  assert.ok(blocked.includes(message));
  assert.match(blocked, /role="status"/);
});

test("source route waits for authorized input rather than offering an empty form", () => {
  const html = render(TestAnalysisPage, { sourceAnalysisId: identifier });
  assert.match(html, /단건 분석 입력을 불러오는 중/);
  assert.doesNotMatch(html, /<form|분석 시작|운영 반영/);
});

test("both Production history and Test result lists receive the same navigation callback", () => {
  const onSingleTest = () => {};
  for (const purpose of ["production", "test"]) {
    let tree;
    function Capture() { tree = AnalysisResultsPage({ purpose, state: {}, testState: { view: "items" }, onSingleTest }); return null; }
    render(Capture, {});
    assert.equal(tree.props.onSingleTest, onSingleTest);
  }
});

test("analysis table action navigates using only the source ID", () => {
  let tree;
  const selected = [];
  function Capture() { tree = AnalysisTable({ items: [], onSingleTest: id => selected.push(id) }); return null; }
  render(Capture, {});
  const table = tree.props.children.find(child => child.props?.columns);
  const action = table.props.columns.find(column => column.id === "actions").render({ id: identifier, payload: "NEVER_NAVIGATE_RAW" });
  const button = action.props.children;
  assert.equal(button.props.children, "단건 분석으로 가져오기");
  button.props.onClick();
  assert.deepEqual(selected, [identifier]);
});

test("template request is an abortable no-store GET without input in the URL", async () => {
  const previous = globalThis.fetch;
  const controller = new AbortController();
  let requested;
  globalThis.fetch = async (path, options) => { requested = { path, options }; return new Response(JSON.stringify({ source_analysis_id: identifier }), { status: 200, headers: { "Content-Type": "application/json" } }); };
  try {
    await api.singleTestTemplate(identifier, { signal: controller.signal });
    assert.equal(requested.path, `/api/v1/analyses/${identifier}/single-test-template`);
    assert.equal(requested.options.cache, "no-store");
    assert.equal(requested.options.signal, controller.signal);
    assert.equal(requested.options.body, undefined);
    assert.equal(requested.options.credentials, "include");
  } finally { globalThis.fetch = previous; }
});
