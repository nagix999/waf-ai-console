import assert from "node:assert/strict";
import test from "node:test";
import { buildSync } from "esbuild";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";
import { runInNewContext } from "node:vm";
import { Children, createElement, Fragment } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { comparisonDelta } from "./testComparison.js";

function load(file, globals = {}) {
  const code = buildSync({ entryPoints: [fileURLToPath(new URL(file, import.meta.url))], bundle: true, write: false, platform: "node", format: "cjs", packages: "external", jsx: "automatic", loader: { ".css": "empty" }, logLevel: "silent" }).outputFiles[0].text;
  const module = { exports: {} }; runInNewContext(code, { module, exports: module.exports, require: createRequire(import.meta.url), process, URL, Date, ...globals }); return module.exports;
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
  assert.match(official, /공식 평가 지표/); assert.match(official, /실행 시 고정한 승인 기대 판정 기준/); assert.match(official, /운영 전체 정확도는 아닙니다/);
  const reference = render(Overview, { summary }); assert.match(reference, /참고 판정 표본/); assert.doesNotMatch(reference, /공식 평가 지표/);
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

test("API keys show metadata with ten-row pagination and explicit delete without exposing technical identifiers", () => {
  const { ServiceApiKeysView } = load("./ServiceApiKeys.jsx");
  const items = Array.from({ length: 12 }, (_, n) => ({ id: `private-id-${n}`, name: `Collector ${n}`, source_system: "fixture", purpose: "test", scopes: ["ingest"], api_key: "NEVER_RENDER_THIS", key_prefix: "HIDDEN_PREFIX" }));
  const html = render(ServiceApiKeysView, { state: { catalog: { items }, draft: { scopes: [] } }, controller: {} });
  assert.match(html, /<table/); assert.match(html, /API 키 목록 페이지/); assert.match(html, /Collector 9/); assert.doesNotMatch(html, /Collector 10|NEVER_RENDER_THIS|private-id-|HIDDEN_PREFIX/);
  assert.match(html, /관리" aria-expanded="false"/); assert.match(html, />삭제<\/button>/);
  assert.match(html, /<th scope="col">용도<\/th>/); assert.match(html, /<th scope="col">권한<\/th>/); assert.match(html, /<th scope="col">발급일<\/th>/);
  assert.doesNotMatch(html, /<dialog/);
});

test("overflow trigger has a visible affordance and no mounted destructive action until opened", () => {
  const More = load("./MoreActions.jsx").default;
  const html = render(More, { children: createElement("button", null, "삭제") });
  assert.match(html, /aria-label="더보기"/); assert.equal((html.match(/<circle/g) || []).length, 3); assert.match(html, /aria-expanded="false"/); assert.doesNotMatch(html, />삭제<\/button>/);
});

test("overflow actions close explicitly after their native button handler, including nested fragments", () => {
  const More = load("./MoreActions.jsx").default;
  const events = [];
  let tree;
  function Capture() {
    tree = More({ children: [createElement("button", { key: "normal", onClick: () => events.push("action") }, "Open"),
      createElement(Fragment, { key: "fragment" }, createElement("button", { disabled: true, onClick: () => events.push("disabled") }, "Disabled"))] });
    return null;
  }
  render(Capture);
  const content = tree.props.children(() => events.push("closed"));
  assert.equal(content.props.onClick, undefined); // A non-interactive wrapper never handles clicks.
  const [button, fragment] = Children.toArray(content.props.children);
  button.props.onClick({});
  assert.deepEqual(events, ["action", "closed"]);
  const disabled = Children.toArray(fragment.props.children)[0];
  disabled.props.onClick({});
  assert.deepEqual(events, ["action", "closed"]);
});

test("dialogs focus only an enabled in-dialog target and restore the connected opener on close", () => {
  for (const [inside, disabled, connected] of [[true, false, true], [true, true, true], [false, false, true], [true, false, false]]) {
    const calls = [], effects = [], originalRequire = createRequire(import.meta.url);
    const initial = { disabled, focus: () => calls.push("initial") };
    const previous = { isConnected: connected, focus: () => calls.push("previous") };
    const node = { open: false, contains: target => inside && target === initial,
      showModal() { this.open = true; calls.push("show"); }, close() { this.open = false; calls.push("close"); } };
    const componentRequire = id => id === "react" ? { ...originalRequire("react"), useEffect: effect => effects.push(effect) } : originalRequire(id);
    const Dialog = load("./Dialog.jsx", { document: { body: { nodeType: 1 }, activeElement: previous }, require: componentRequire }).default;
    let portal;
    function Capture() { portal = Dialog({ open: true, title: "Confirm", initialFocusRef: { current: initial }, onClose() {} }); return null; }
    render(Capture);
    portal.children.props.ref.current = node;
    const close = effects[0]();
    assert.deepEqual(calls, inside && !disabled ? ["show", "initial"] : ["show"]);
    close();
    assert.equal(node.open, false);
    assert.deepEqual(calls.slice(inside && !disabled ? 2 : 1), connected ? ["close", "previous"] : ["close"]);
  }
});
