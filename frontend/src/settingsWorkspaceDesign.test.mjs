import assert from "node:assert/strict";
import test from "node:test";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";
import { runInNewContext } from "node:vm";
import { buildSync } from "esbuild";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";

function load(path) {
  const result = buildSync({ entryPoints: [fileURLToPath(new URL(path, import.meta.url))], bundle: true, write: false, platform: "node", format: "cjs", packages: "external", jsx: "automatic", loader: { ".css": "empty" }, logLevel: "silent" });
  const module = { exports: {} };
  runInNewContext(result.outputFiles[0].text, { module, exports: module.exports, require: createRequire(import.meta.url), process, URL, AbortController, setTimeout, clearTimeout });
  return module.exports;
}
const { SettingsSectionHeader, SettingsSummary, SettingsWorkflow } = load("./SettingsWorkspace.jsx");

test("page actions do not duplicate the shell title and compact context escapes supplied text", () => {
  const html = renderToStaticMarkup(createElement(SettingsSectionHeader, { icon: "key", title: "<script>unsafe</script>", description: "실제 연결 관리", actions: createElement("button", { type: "button" }, "새로고침") }, createElement("p", null, "운영 상태")));
  assert.match(html, /settings-page-context/);
  assert.match(html, /&lt;script&gt;unsafe&lt;\/script&gt;/);
  assert.doesNotMatch(html, /<script>/);
  assert.doesNotMatch(html, /<h[12]>/);
  assert.match(html, /settings-page-actions/);
  assert.match(html, /운영 상태/);
});

test("live summaries distinguish an actual zero from unavailable data", () => {
  const html = renderToStaticMarkup(createElement(SettingsSummary, { label: "연결 현황", items: [{ label: "현재 키", value: 0 }, { label: "Test", value: undefined }, { label: "Production", value: 7, tone: "blue", note: "조회된 키" }] }));
  assert.match(html, /<dd>0<\/dd>/);
  assert.match(html, /<dd>—<\/dd>/);
  assert.match(html, /<dd>7<\/dd>/);
  assert.match(html, /settings-summary-blue/);
  assert.doesNotMatch(html, /데모|예시 값|서버 정상/);
});

test("workflow explains the approval route without implying completed steps", () => {
  const html = renderToStaticMarkup(createElement(SettingsWorkflow));
  assert.equal((html.match(/<li>/g) || []).length, 3);
  assert.match(html, /공식 테스트/);
  assert.match(html, /운영 반영 검토/);
  assert.match(html, /운영에 반영/);
  assert.doesNotMatch(html, /aria-current|완료|active/);
});

test("deployment route is a read-only page rather than an automatic dialog", () => {
  const { Activity } = load("./LifecycleViews.jsx");
  const html = renderToStaticMarkup(createElement(Activity, { deploymentOnly: true }));
  assert.match(html, /deployment-workspace/);
  assert.match(html, /읽기 전용/);
  assert.match(html, /API 버전/);
  assert.match(html, /미제공/);
  assert.doesNotMatch(html, /<dialog|배포 실행|서버 재시작<\/button>|정상 실행 중/);
});

test("official evaluation metadata can be retained without repeating the same metric strip", () => {
  const { OfficialMetrics } = load("./LifecycleViews.jsx");
  const evaluation = { created_at: "2026-11-25T00:00:00Z", summary: { ground_truth: { dataset_name: "검토한 평가 데이터", dataset_revision: 2, sample_count: 30 }, evaluation_summary: { metrics: { accuracy: .9, precision: .8, recall: .7, f1: .75, coverage: 1 } } } };
  const html = renderToStaticMarkup(createElement(OfficialMetrics, { evaluation, showMetricStrip: false }));
  assert.match(html, /검토한 평가 데이터/);
  assert.match(html, /평가 문항.*30/);
  assert.match(html, /운영 전체 트래픽의 정확도가 아닙니다/);
  assert.doesNotMatch(html, /v5-metric-strip/);
  const defaultHtml = renderToStaticMarkup(createElement(OfficialMetrics, { evaluation }));
  assert.match(defaultHtml, /v5-metric-strip/);
});

test("role-card workspace keeps all Production settings read-only and avoids duplicated role rows", () => {
  const { ReadOnlyAgentRoles } = load("./R5TestDefaults.jsx");
  const html = renderToStaticMarkup(createElement(ReadOnlyAgentRoles));
  assert.equal((html.match(/class="panel settings-role-card /g) || []).length, 3);
  assert.equal((html.match(/settings-readonly-badge/g) || []).length, 4);
  const roleCards = html.slice(html.indexOf('class="settings-role-cards"'), html.indexOf('class="settings-detail-grid'));
  assert.doesNotMatch(roleCards, /<select|<input|주 분석 모델 · Primary/);
  assert.match(html, /기본 테스트 설정/);
  assert.match(html, /운영 모델과 기존 테스트는 바뀌지 않습니다/);
  for (const value of ["분석 지침", "입력 스키마", "파서", "시그니처 설명"]) assert.match(html, new RegExp(value));
});
