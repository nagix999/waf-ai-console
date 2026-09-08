/** Synthetic browser checks. Every API request is mocked; no LLM or server API is contacted.
 * Start Vite on 127.0.0.1:15173, then run with modern Node.
 * Uses an existing Playwright/Chromium installation; never downloads dependencies.
 */
import assert from "node:assert/strict";
import { existsSync, readdirSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";
import { pathToFileURL } from "node:url";

const origin = "http://127.0.0.1:15173";
const policy1 = "11111111-1111-4111-8111-111111111111";
const baselineId = "33333333-3333-4333-8333-333333333333";
const candidateId = "44444444-4444-4444-8444-444444444444";
const baselineAnalysis = "55555555-5555-4555-8555-555555555555";
const candidateAnalysis = "66666666-6666-4666-8666-666666666666";
const now = "2026-09-08T00:00:00Z";
const event = { event_id: "synthetic-prompt-case", company_name: "Synthetic", src_ip: "192.0.2.1", dest_ip: "198.51.100.2", waf_vendor: "generic", waf_action: "D", payload: "GET /synthetic HTTP/1.1\r\nHost: example.invalid\r\n\r\n" };
const metrics = { accuracy: 1, precision: 1, recall: 1, f1: 1, coverage: 1, abstention_rate: 0, overall_binary_correct_rate: 1 };
const evaluation = { total: 1, labeled: 1, evaluable: 1, matches: 1, binary_evaluable: 1, binary_decided: 1, label_coverage: 1, outcomes: { match: 1 }, source_groups: [], confusion_matrix: { tp: 1, fn: 0, fp: 0, tn: 0, abstained_positive: 0, abstained_negative: 0 }, metrics };
const baselineEvaluation = { ...evaluation, matches: 0, outcomes: { false_negative: 1 }, confusion_matrix: { ...evaluation.confusion_matrix, tp: 0, fn: 1 }, metrics: { ...metrics, accuracy: 0, precision: null, recall: 0, f1: 0, overall_binary_correct_rate: 0 } };
function run(id) {
  const baseline = id === baselineId;
  return { id, name: baseline ? "합성 기준 테스트" : "합성 후보 테스트", kind: "upload", created_at: now, completed_at: now, status: "completed", total: 1, accepted: 1, rejected: 0, duplicates: 0, pending: 0, processing: 0, completed: 1, failed: 0, execution_mode: "moduagent", profile_metadata: { model_name: "synthetic-model" }, prompt_version: `waf-judgment-v2.${baseline ? 5 : 6}/policy-1`, prompt_policy_version_id: policy1, source_system: `synthetic-${id}`, total_elapsed_ms: 1000, evaluation_summary: baseline ? baselineEvaluation : evaluation, total_items: 1, facets: { difficulties: ["easy"], test_categories: ["synthetic"] }, items: [{ id: "synthetic-item", analysis_id: baseline ? baselineAnalysis : candidateAnalysis, row_number: 1, event_id: event.event_id, case_name: "합성 비교 문항", status: "completed", ingest_status: "accepted", verdict: baseline ? "false_positive" : "true_positive", evaluation: { outcome: baseline ? "false_negative" : "match" } }] };
}
const timing = { count: 1, missing_count: 0, sum_ms: 1000, mean_ms: 1000, p50_ms: 1000, p95_ms: 1000 };
const token = { known_sum: 100, measured_steps: 1, missing_steps: 0 };
const performance = { processing_ms: timing, llm_step_ms: timing, tokens: { input_tokens: token, output_tokens: token, total_tokens: token }, llm_steps: 1, missing_agent_histories: 0, output_repair_steps: 0 };
function comparison(query) {
  return { baseline: run(baselineId), candidate: run(candidateId), baseline_evaluation: baselineEvaluation, candidate_evaluation: evaluation, counts: { accepted_pairs: 1, comparable_pairs: 1, changed: 1, improved: 1, regressed: 0, exclusions: {} }, warnings: ["comparison_is_not_causal_proof", "prompt_budget_may_change_submitted_input"], performance: { scope: "comparable_pairs_all_recorded_attempts", baseline: performance, candidate: performance }, total_items: 1, limit: Number(query.get("limit") || 25), offset: Number(query.get("offset") || 0), changes_only: query.get("changes_only") === "true", items: [{ event_id: event.event_id, case_name: "합성 비교 문항", difficulty: "easy", test_category: "synthetic", baseline_analysis_id: baselineAnalysis, candidate_analysis_id: candidateAnalysis, baseline_verdict: "false_positive", candidate_verdict: "true_positive", baseline_outcome: "false_negative", candidate_outcome: "match", reference_verdict: "true_positive", comparison_status: "comparable", change: "improved" }] };
}
async function existingPlaywright() {
  if (process.env.NAVIGATION_PLAYWRIGHT_MODULE) return import(pathToFileURL(process.env.NAVIGATION_PLAYWRIGHT_MODULE).href);
  try { return await import("playwright"); } catch { /* Existing Python package may include the JS driver. */ }
  const archive = join(homedir(), ".cache/uv/archive-v0");
  for (const entry of existsSync(archive) ? readdirSync(archive).sort() : []) {
    const modulePath = join(archive, entry, "playwright/driver/package/index.mjs");
    if (existsSync(modulePath)) return import(pathToFileURL(modulePath).href);
  }
  throw new Error("Existing Playwright is required.");
}
const { chromium } = await existingPlaywright();
const executablePath = process.env.NAVIGATION_CHROMIUM || join(homedir(), ".cache/ms-playwright/chromium-1208/chrome-linux64/chrome");
const browser = await chromium.launch({ executablePath, headless: true, args: ["--no-sandbox"] });
const passed = [];
async function scenario(name, check) {
  const context = await browser.newContext({ viewport: { width: 1360, height: 960 }, serviceWorkers: "block" });
  const writes = [], reads = [], violations = [], errors = [];
  const controls = { submission: null };
  await context.route("**/*", async route => {
    const request = route.request(), url = new URL(request.url()), method = request.method(), path = url.pathname;
    const json = (body, status = 200) => route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });
    if (url.origin !== origin) { violations.push("external_request"); return route.abort(); }
    if (!path.startsWith("/api/")) {
      if (method !== "GET") { violations.push("unexpected_static_write"); return route.abort(); }
      return route.continue();
    }
    if (method !== "GET") {
      writes.push({ path, body: request.postData(), method });
      if (!["/api/v1/test-runs", "/api/v1/test-runs/uploads"].includes(path) || method !== "POST") { violations.push("unexpected_api_write"); return json({}, 403); }
      if (controls.submission) return controls.submission({ route, request, json });
      return json(run(candidateId), 202);
    }
    reads.push({ path, query: Object.fromEntries(url.searchParams) });
    if (path === "/api/v1/auth/me") return json({ kind: "admin", username: "synthetic", role: "admin", scopes: ["admin"] });
    if (path === "/api/v1/dashboard/summary") return json({ counts: {}, window: {}, runtime: { agent_mode: "moduagent" } });
    if (path === "/api/v1/model-profiles") return json([{ id: policy1, provider: "openai", name: "Synthetic", model_name: "synthetic-model", is_test: true, status: "verified", can_assign: true, profile_fingerprint: "a".repeat(64), external_data_approved: true }]);
    if (path === "/api/v1/test-runs") return json({ items: [run(baselineId), run(candidateId)], total: 2, limit: 20, offset: 0 });
    if (path === `/api/v1/test-runs/${candidateId}/comparison`) return json(comparison(url.searchParams));
    if (path === `/api/v1/test-runs/${candidateId}` || path === `/api/v1/test-runs/${baselineId}`) return json(run(path.split("/").at(-1)));
    if (/^\/api\/v1\/analyses\/[^/]+\/evaluation-labels$/.test(path)) return json({ items: [] });
    if (/^\/api\/v1\/analyses\/[^/]+$/.test(path)) {
      const verdict = path.endsWith(`/${baselineAnalysis}`) ? "false_positive" : "true_positive";
      return json({ ...event, id: path.split("/").at(-1), analysis_purpose: "test", source_system: "synthetic", status: "completed", created_at: now, completed_at: now, verdict, summary_ko: "합성 분석 결과", evaluation: { outcome: "unlabeled" }, result: { verdict, summary_ko: "합성 분석 결과", agent: { framework: "moduagent" } } });
    }
    violations.push(`unmocked_api:${path}`); return json({}, 500);
  });
  const page = await context.newPage(); page.setDefaultTimeout(10000);
  page.on("pageerror", error => errors.push(error.name));
  try {
    await check({ page, writes, reads, controls });
    assert.deepEqual(violations, []); assert.deepEqual(errors, []); passed.push(name);
  } finally { await context.close(); }
}
try {
  await scenario("common prompt submission, retained drafts and retry key", async ({ page, writes, controls }) => {
    await page.goto(`${origin}/#test`);
    assert.equal(await page.locator(".test-prompt-selector").count(), 0);
    await page.locator("#test-panel-direct .test-name-field input").fill("합성 직접 비교");
    await page.getByRole("tab", { name: "배치 파일 분석", exact: true }).click();
    await page.getByRole("tab", { name: "단건 분석", exact: true }).click();
    assert.equal(await page.locator("#test-panel-direct .test-name-field input").inputValue(), "합성 직접 비교");
    const direct = page.locator("#test-panel-direct");
    await direct.getByLabel("회사명", { exact: true }).fill("테스트 회사");
    await direct.getByLabel("WAF 벤더", { exact: true }).fill("test-vendor");
    await direct.getByLabel("출발지 IP", { exact: true }).fill("192.0.2.10");
    await direct.getByLabel("목적지 IP", { exact: true }).fill("198.51.100.20");
    await direct.getByLabel("WAF 조치", { exact: true }).selectOption("A");
    await direct.getByLabel("HTTP 원문", { exact: true }).fill("GET /test HTTP/1.1\nHost: example.invalid\n\n");
    let release;
    controls.submission = async ({ json }) => { await new Promise(resolve => { release = resolve; }); return json({ detail: "synthetic_response_unavailable" }, 503); };
    await page.locator("#test-panel-direct button.primary").click();
    await page.waitForFunction(() => document.querySelector(".test-submission-fields").disabled);
    assert.equal(writes.length, 1);
    const first = JSON.parse(writes[0].body); assert.equal(first.prompt_policy_version_id, undefined);
    assert.equal(first.fixed_rules_version, undefined);
    assert.equal(first.event.prompt_policy_version_id, undefined);
    assert.equal(first.event.fixed_rules_version, undefined);
    release();
    await page.waitForFunction(() => !document.querySelector(".test-submission-fields").disabled);
    controls.submission = ({ json }) => json(run(candidateId), 202);
    await page.locator("#test-panel-direct button.primary").click();
    await page.waitForURL(`**/#test-runs/${candidateId}`);
    assert.equal(JSON.parse(writes[1].body).idempotency_key, first.idempotency_key);
    assert.equal(JSON.parse(writes[1].body).prompt_policy_version_id, undefined);
    assert.equal(JSON.parse(writes[1].body).fixed_rules_version, undefined);
  });
  await scenario("file submission uses the common prompt without an override", async ({ page, writes }) => {
    await page.goto(`${origin}/#test`);
    await page.getByRole("tab", { name: "배치 파일 분석", exact: true }).click();
    await page.locator("#test-panel-file .test-name-field input").fill("합성 파일 비교");
    await page.locator("#test-panel-file input[type=file]").setInputFiles({ name: "synthetic.json", mimeType: "application/json", buffer: Buffer.from(JSON.stringify([{ ...event, expected_verdict: "true_positive" }])) });
    await page.getByRole("button", { name: "배치 분석 시작", exact: true }).click();
    await page.waitForURL(`**/#test-runs/${candidateId}`);
    assert.equal(writes.length, 1); assert.equal(writes[0].path, "/api/v1/test-runs/uploads");
    assert.doesNotMatch(writes[0].body, /name="(?:prompt_policy_version_id|fixed_rules_version|prompt_template)"/);
  });
  await scenario("comparison is read-only and browser back restores the baseline and filters", async ({ page, writes, reads }) => {
    await page.goto(`${origin}/#test-runs/${candidateId}`);
    const panel = page.locator(".test-run-comparison");
    await page.getByRole("button", { name: "다른 테스트와 비교", exact: true }).click();
    await page.getByLabel("비교 기준 테스트명 검색").fill("합성 기준");
    await panel.getByRole("button", { name: "검색", exact: true }).click();
    await panel.locator(".comparison-baselines button").filter({ hasText: "합성 기준 테스트" }).click();
    await page.locator(".test-comparison-items").waitFor();
    assert.equal(writes.length, 0);
    await page.getByLabel("판정이 변경된 비교 가능 문항만 보기").check();
    await page.waitForFunction(() => document.querySelector(".test-comparison-items"));
    await page.getByRole("button", { name: "기준 분석 상세", exact: true }).click();
    await page.waitForURL(`**/#analyses/${baselineAnalysis}`);
    await page.goBack(); await page.waitForURL(`**/#test-runs/${candidateId}`);
    await page.locator(".test-comparison-items").waitFor();
    assert.equal(await page.getByLabel("비교 기준 테스트명 검색").inputValue(), "합성 기준");
    assert.equal(await page.getByLabel("판정이 변경된 비교 가능 문항만 보기").isChecked(), true);
    assert.equal(await panel.locator(".comparison-baselines button[aria-pressed=true]").count(), 1);
    await page.getByRole("button", { name: "후보 분석 상세", exact: true }).click();
    await page.waitForURL(`**/#analyses/${candidateAnalysis}`);
    await page.goBack(); await page.waitForURL(`**/#test-runs/${candidateId}`);
    await page.locator(".test-comparison-items").waitFor();
    const persisted = await page.evaluate(() => JSON.stringify({ url: location.href, state: history.state, local: { ...localStorage }, session: { ...sessionStorage } }));
    assert.ok(!persisted.includes("합성 기준"), "Comparison search must not enter URL/history/storage.");
    assert.equal(writes.length, 0);
    assert.ok(reads.some(read => read.path.endsWith("/comparison") && read.query.baseline_id === baselineId && read.query.changes_only === "true"));
    await page.setViewportSize({ width: 390, height: 844 });
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), "Narrow UI must not overflow the page horizontally.");
    assert.ok(await page.locator(".test-comparison-items").evaluate(table => {
      const wrapper = table.parentElement;
      wrapper.scrollLeft = wrapper.scrollWidth;
      return wrapper.clientWidth < table.offsetWidth && wrapper.scrollLeft > 0;
    }), "Wide comparison tables must remain readable through their own horizontal scroll.");
  });
  console.log(JSON.stringify({ passed, scenarios: passed.length, server_api_requests: 0, llm_calls: 0 }, null, 2));
} finally { await browser.close(); }
