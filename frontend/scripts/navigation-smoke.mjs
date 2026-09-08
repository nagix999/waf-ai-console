/**
 * Synthetic-only browser navigation regression checks. No API request reaches a
 * server, and any unexpected write or external request fails the run.
 *
 * Start Vite separately on 127.0.0.1:15173, then run:
 *   node frontend/scripts/navigation-smoke.mjs
 * Optional: NAVIGATION_PLAYWRIGHT_MODULE=/existing/playwright/index.mjs
 *           NAVIGATION_CHROMIUM=/existing/chrome
 * Uses an existing Playwright installation; never installs packages/browsers.
 */
import assert from "node:assert/strict";
import { existsSync, readdirSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";
import { pathToFileURL } from "node:url";

const origin = "http://127.0.0.1:15173";
const analysisId = "11111111-1111-4111-8111-111111111111";
const itemAnalysisId = "22222222-2222-4222-8222-222222222222";
const runId = "33333333-3333-4333-8333-333333333333";
const schemaId = "44444444-4444-4444-8444-444444444444";
const searchCanary = "synthetic-private-search-navigation";
const runCanary = "synthetic-private-test-name";
const now = "2026-09-08T00:00:00Z";

async function existingPlaywright() {
  if (process.env.NAVIGATION_PLAYWRIGHT_MODULE) return import(pathToFileURL(process.env.NAVIGATION_PLAYWRIGHT_MODULE).href);
  try { return await import("playwright"); } catch { /* Python Playwright includes the same JS driver. */ }
  const archive = join(homedir(), ".cache/uv/archive-v0");
  for (const entry of existsSync(archive) ? readdirSync(archive).sort() : []) {
    const candidate = join(archive, entry, "playwright/driver/package/index.mjs");
    if (existsSync(candidate)) return import(pathToFileURL(candidate).href);
  }
  throw new Error("An existing Playwright installation is required; set NAVIGATION_PLAYWRIGHT_MODULE.");
}

const evaluation = {
  total: 75, labeled: 75, evaluable: 75, matches: 50, binary_evaluable: 75,
  binary_decided: 75, support_positive: 50, support_negative: 25, label_coverage: 1,
  outcomes: { match: 50, false_negative: 25 }, source_groups: [],
  confusion_matrix: { tp: 25, fn: 25, fp: 0, tn: 25, abstained_positive: 0, abstained_negative: 0 },
  metrics: { accuracy: 2 / 3, precision: 1, recall: .5, f1: 2 / 3, coverage: 1, abstention_rate: 0 },
};

function analysis(id = analysisId, offset = 0, purpose = "production") {
  return {
    id, event_id: `synthetic-event-${offset}`, status: "completed", analysis_purpose: purpose,
    source_system: "synthetic-navigation", company_name: "합성 회사", src_ip: "192.0.2.10",
    dest_ip: "198.51.100.20", src_port: 12345, dest_port: 443, waf_action: "D", waf_vendor: "Synthetic",
    signature: `합성 탐지 항목 ${offset}`, created_at: now, completed_at: now, review_state: "unreviewed",
    verdict: "true_positive", severity: "HIGH", total_elapsed_ms: 1234, queue_wait_ms: 100,
    processing_duration_ms: 1134, ingest_channel: purpose === "test" ? "test_lab" : "service_api",
    summary_ko: "합성 요청에 포함된 공격 구문을 확인했습니다.", evaluation: { outcome: "unlabeled" },
    result: {
      schema_version: "waf-analysis-v2", verdict: "true_positive", confidence_score: .9,
      summary_ko: "합성 요청에 포함된 공격 구문을 확인했습니다.",
      threat_analysis: { severity: "HIGH", category: "sql_injection", target: "payload", technique_ko: "합성 탐색 구문입니다.", obfuscations: [], potential_impact_ko: "운영 트래픽이 아닌 모의 자료입니다." },
      signature_assessment: { relation: "exact", explanation_ko: "합성 시그니처와 일치합니다." },
      evidence: [], recommended_checks: [], conflicting_evidence: [], input_truncated: false,
      tuning_recommendation: { recommended: false }, agent: { framework: "moduagent", execution_mode: "moduagent" },
    },
  };
}

function run(offset = 0) {
  return {
    id: runId, name: `합성 테스트 ${offset}`, kind: "upload", status: "completed", created_at: now,
    execution_mode: "moduagent", profile_metadata: { model_name: "synthetic-model" },
    prompt_version: "synthetic-prompt", source_system: "synthetic-test-navigation", total_elapsed_ms: 1234,
    total: 75, accepted: 75, rejected: 0, pending: 0, processing: 0, completed: 75, failed: 0,
    evaluation_summary: evaluation,
  };
}

const { chromium } = await existingPlaywright();
const executablePath = process.env.NAVIGATION_CHROMIUM || join(homedir(), ".cache/ms-playwright/chromium-1208/chrome-linux64/chrome");
assert.ok(existsSync(executablePath), "An existing Chromium executable is required.");
const browser = await chromium.launch({ executablePath, headless: true, args: ["--no-sandbox"] });
const passed = [];
let totalMockRequests = 0;

async function scenario(name, check) {
  const context = await browser.newContext({ viewport: { width: 1280, height: 900 }, serviceWorkers: "block" });
  const violations = [], pageErrors = [], requests = [];
  let signedIn = true;
  await context.route("**/*", async route => {
    const request = route.request(), url = new URL(request.url()), method = request.method();
    const json = (body, status = 200) => route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });
    if (url.origin !== origin) { violations.push("external_request"); return route.abort("blockedbyclient"); }
    if (!url.pathname.startsWith("/api/")) {
      const staticRead = method === "GET" && (url.pathname === "/" || /^\/(src|node_modules|assets|@vite|@react-refresh|@fs)\b/.test(url.pathname) || url.pathname === "/favicon.ico");
      if (!staticRead) { violations.push("unexpected_non_api_request"); return route.abort("blockedbyclient"); }
      return route.continue();
    }
    requests.push({ method, path: url.pathname, query: Object.fromEntries(url.searchParams) });
    totalMockRequests++;
    if (method === "POST" && url.pathname === "/api/v1/auth/logout") { signedIn = false; return json({}); }
    if (method === "POST" && url.pathname === "/api/v1/auth/login") { signedIn = true; return json({}); }
    if (method !== "GET") { violations.push("unexpected_api_write"); return json({ detail: "blocked_synthetic_test_write" }, 403); }
    if (!signedIn) return json({ detail: "not_authenticated" }, 401);
    const path = url.pathname;
    if (path === "/api/v1/auth/me") return json({ kind: "admin", id: "synthetic-admin", username: "synthetic-admin", role: "admin", scopes: ["admin"] });
    if (path === "/api/v1/dashboard/summary") return json({
      counts: { total: 75, pending: 0, processing: 0, completed: 75, failed: 0, critical_high_allowed: 0, false_positive_denied: 0, true_positive: 50, false_positive: 25, inconclusive: 0 },
      window: { created_from: "2026-09-01T00:00:00Z", created_to: now },
      runtime: { agent_mode: "moduagent", production_profile: { name: "Synthetic model" } },
    });
    if (path === "/api/v1/analyses") {
      const offset = Number(url.searchParams.get("offset") || 0);
      return json({ items: [analysis(analysisId, offset, url.searchParams.get("analysis_purpose") || "production")], total: 75, evaluation_summary: evaluation });
    }
    if (/^\/api\/v1\/analyses\/[^/]+\/evaluation-labels$/.test(path)) return json({ items: [] });
    if (/^\/api\/v1\/analyses\/[^/]+\/agent-runs$/.test(path)) return json([]);
    if (/^\/api\/v1\/analyses\/[^/]+\/event$/.test(path)) return json({ payload: "GET /synthetic-navigation HTTP/1.1\r\nHost: example.invalid\r\n\r\n", extra_fields: {} });
    if (/^\/api\/v1\/analyses\/[^/]+$/.test(path)) return json(analysis(path.split("/").at(-1), 0, path.endsWith(itemAnalysisId) ? "test" : "production"));
    if (path === "/api/v1/test-runs") return json({ items: [run(Number(url.searchParams.get("offset") || 0))], total: 30 });
    if (path === `/api/v1/test-runs/${runId}`) {
      const offset = Number(url.searchParams.get("offset") || 0);
      return json({ ...run(), total_items: 75, facets: { difficulties: ["easy", "hard"], test_categories: ["sqli", "xss"] },
        items: [{ id: `synthetic-item-${offset}`, analysis_id: itemAnalysisId, row_number: offset + 1, case_name: `합성 문항 ${offset}`, event_id: "synthetic-case", difficulty: "hard", test_category: "sqli", status: "completed", ingest_status: "accepted", verdict: "true_positive", summary_ko: "합성 문항 결과", evaluation: { outcome: "unlabeled" } }],
      });
    }
    if (path === "/api/v1/model-profiles" || path === "/api/v1/admin/internal-egress") return json([]);
    if (path === "/api/v1/admin/service-api-keys") return json({ items: [] });
    if (path === "/api/v1/admin/prompt-policies") return json({ items: [], active_version_id: null, revision: 0, max_policy_chars: 4000 });
    if (path === "/api/v1/admin/input-schemas") return json({ items: [], active_version_id: null, revision: 0 });
    if (path === "/api/v1/admin/input-schemas/activation-history") return json({ items: [] });
    if (path === "/api/v1/production-api") return json({ markdown: "# Synthetic Production API\n\n## 입력\n\n합성 문서입니다.", input_schema: { version_id: schemaId, version_number: 1, content_hash: "synthetic-document-hash" } });
    violations.push("unmocked_api_read");
    return json({ detail: "unmocked_synthetic_endpoint" }, 500);
  });
  const page = await context.newPage();
  page.setDefaultTimeout(10000);
  page.on("pageerror", error => pageErrors.push(error.name));
  const hash = expected => page.waitForFunction(value => window.location.hash === value, expected);
  const menu = label => page.getByRole("navigation", { name: "주 메뉴" }).getByRole("button", { name: label, exact: true });
  const go = async route => { await page.goto(`${origin}/${route}`); await page.locator(".app-header h1").waitFor(); await hash(route); };
  const back = async route => { await page.goBack(); await hash(route); };
  const forward = async route => { await page.goForward(); await hash(route); };
  const privateStateAbsent = async () => {
    const stored = await page.evaluate(() => JSON.stringify({ url: location.href, state: history.state, local: { ...localStorage }, session: { ...sessionStorage } }));
    assert.ok(!stored.includes(searchCanary) && !stored.includes(runCanary), "Private filters must remain outside URLs/history/storage.");
  };
  try {
    await check({ page, hash, menu, go, back, forward, requests, privateStateAbsent });
    assert.deepEqual(violations, [], `${name}: all traffic must be mocked or local static assets`);
    assert.deepEqual(pageErrors, [], `${name}: no browser runtime errors`);
    passed.push(name);
    console.log(`PASS ${name}`);
  } finally { await context.close(); }
}

try {
  await scenario("menu-back-forward-and-existing-api-link", async ({ page, go, hash, menu, back, forward }) => {
    await go("#dashboard");
    await menu("분석 결과").click(); await hash("#analyses");
    await menu("Production API").click(); await hash("#production-api");
    await menu("설정").click(); await hash("#settings/models");
    await back("#production-api"); await back("#analyses");
    await forward("#production-api"); await forward("#settings/models");
    const before = await page.evaluate(() => history.length);
    await menu("설정").click();
    assert.equal(await page.evaluate(() => history.length), before, "Same route must not create a duplicate entry.");
  });

  await scenario("production-search-page-detail-restoration", async ({ page, go, hash, back, forward, requests, privateStateAbsent }) => {
    await go("#analyses/production");
    await page.locator('input[name="q"]').fill(searchCanary);
    await page.locator(".filter-panel").getByRole("button", { name: "검색", exact: true }).click();
    await page.locator(".pagination").getByRole("button", { name: "다음", exact: true }).click();
    await page.getByRole("button", { name: "합성 탐지 항목 25", exact: true }).click();
    await hash(`#analyses/${analysisId}`);
    await privateStateAbsent();
    await back("#analyses/production");
    assert.equal(await page.locator('input[name="q"]').inputValue(), searchCanary);
    await page.getByRole("button", { name: "합성 탐지 항목 25", exact: true }).waitFor();
    assert.ok(requests.some(item => item.path === "/api/v1/analyses" && item.query.offset === "25" && item.query.q === searchCanary && item.query.analysis_purpose === "production"));
    await forward(`#analyses/${analysisId}`);
    await page.getByRole("tab", { name: "HTTP 원문", exact: true }).click(); await hash(`#analyses/${analysisId}/raw`);
    await page.getByRole("heading", { name: "요청 원문", exact: true }).waitFor();
    await back(`#analyses/${analysisId}`); await forward(`#analyses/${analysisId}/raw`);
    await privateStateAbsent();
  });

  await scenario("test-history-run-item-back-twice-forward-twice", async ({ page, go, hash, back, forward, requests, privateStateAbsent }) => {
    await go("#analyses/test");
    await page.getByLabel("테스트명 검색", { exact: true }).fill(runCanary);
    await page.locator(".test-run-search").getByRole("button", { name: "검색", exact: true }).click();
    await page.locator(".test-run-history .pagination").getByRole("button", { name: "다음", exact: true }).click();
    await page.getByRole("button", { name: "합성 테스트 10", exact: true }).click(); await hash(`#test-runs/${runId}`);
    await page.locator('select[name="difficulty"]').selectOption("value:hard");
    await page.locator('select[name="test_category"]').selectOption("value:sqli");
    await page.getByRole("button", { name: "평가 상세", exact: true }).click();
    await page.getByRole("button", { name: "FN · 미탐 방향 25건 문항 보기", exact: true }).click();
    await page.locator(".test-run-cases .pagination").getByRole("button", { name: "다음", exact: true }).click();
    await page.getByRole("button", { name: "합성 문항 25", exact: true }).click(); await hash(`#analyses/${itemAnalysisId}`);
    await privateStateAbsent();
    await back(`#test-runs/${runId}`);
    assert.equal(await page.locator('select[name="difficulty"]').inputValue(), "value:hard");
    assert.equal(await page.locator('select[name="test_category"]').inputValue(), "value:sqli");
    await page.getByRole("button", { name: "평가 상세", exact: true }).click();
    assert.equal(await page.getByRole("button", { name: "FN · 미탐 방향 25건 문항 보기", exact: true }).getAttribute("aria-pressed"), "true");
    await page.getByRole("button", { name: "평가 상세 닫기", exact: true }).click();
    await page.getByRole("button", { name: "합성 문항 25", exact: true }).waitFor();
    await back("#analyses/test");
    assert.equal(await page.getByLabel("테스트명 검색", { exact: true }).inputValue(), runCanary);
    await page.getByRole("button", { name: "합성 테스트 10", exact: true }).waitFor();
    await forward(`#test-runs/${runId}`); await page.getByRole("button", { name: "합성 문항 25", exact: true }).waitFor();
    await forward(`#analyses/${itemAnalysisId}`);
    assert.ok(requests.some(item => item.path === `/api/v1/test-runs/${runId}` && item.query.offset === "25" && item.query.difficulty === "hard" && item.query.test_category === "sqli"));
  });

  await scenario("settings-tabs-history", async ({ page, go, hash, back, forward }) => {
    await go("#settings/models");
    await page.getByRole("tab", { name: "프롬프트", exact: true }).click(); await hash("#settings/prompts");
    await page.getByRole("tab", { name: "입력 스키마", exact: true }).click(); await hash("#settings/schema");
    await back("#settings/prompts");
    assert.equal(await page.getByRole("tab", { name: "프롬프트", exact: true }).getAttribute("aria-selected"), "true");
    await forward("#settings/schema");
    assert.equal(await page.getByRole("tab", { name: "입력 스키마", exact: true }).getAttribute("aria-selected"), "true");
  });

  await scenario("dashboard-detail-in-app-back", async ({ page, go, hash }) => {
    await go("#dashboard");
    await page.getByRole("button", { name: "합성 탐지 항목 0", exact: true }).click(); await hash(`#analyses/${analysisId}`);
    await page.locator("button.back").click(); await hash("#dashboard");
    await page.getByRole("heading", { name: "대시보드", exact: true }).waitFor();
  });

  await scenario("production-return-ignores-remembered-test-run", async ({ page, go, hash, menu, back }) => {
    await go(`#test-runs/${runId}`);
    await page.locator(".test-run-header").waitFor();
    await menu("대시보드").click(); await hash("#dashboard");
    await page.locator(".distribution-true_positive").click(); await hash("#analyses/production");
    await page.getByRole("button", { name: "합성 탐지 항목 0", exact: true }).click(); await hash(`#analyses/${analysisId}`);
    await page.locator("button.back").click(); await hash("#analyses/production");
    await back("#dashboard");
    await back(`#test-runs/${runId}`);
  });

  await scenario("direct-link-reload-and-safe-fallback-back", async ({ page, go, hash }) => {
    await go(`#analyses/${analysisId}/report`);
    assert.equal(await page.getByRole("tab", { name: "보고서", exact: true }).getAttribute("aria-selected"), "true");
    await page.reload(); await hash(`#analyses/${analysisId}/report`);
    await page.getByRole("tab", { name: "보고서", exact: true }).waitFor();
    await page.locator("button.back").click(); await hash("#analyses");
    assert.equal(new URL(page.url()).origin, origin);
  });

  await scenario("skip-link-does-not-change-page", async ({ page, go }) => {
    await go("#analyses/production");
    await page.getByRole("link", { name: "본문으로 이동", exact: true }).focus();
    await page.keyboard.press("Enter");
    await page.waitForFunction(() => document.activeElement?.id === "workspace-content");
    assert.equal(await page.locator(".app-header h1").innerText(), "분석 결과");
    assert.equal(await page.getByRole("tab", { name: "프로덕션", exact: true }).getAttribute("aria-selected"), "true");
  });

  await scenario("logout-back-authentication-and-private-memory-reset", async ({ page, go, hash, menu, privateStateAbsent }) => {
    await go("#analyses/production");
    await page.locator('input[name="q"]').fill(searchCanary);
    await page.locator(".filter-panel").getByRole("button", { name: "검색", exact: true }).click();
    await page.getByRole("button", { name: "합성 탐지 항목 0", exact: true }).click(); await hash(`#analyses/${analysisId}`);
    await page.getByRole("button", { name: "로그아웃", exact: true }).click();
    await page.getByRole("heading", { name: "WAF AI 분석 콘솔", exact: true }).waitFor();
    await page.goBack();
    await page.getByRole("heading", { name: "WAF AI 분석 콘솔", exact: true }).waitFor();
    assert.equal(await page.locator(".app-shell").count(), 0, "Back must not resurrect authenticated data.");
    await privateStateAbsent();
    await page.getByLabel("아이디", { exact: true }).fill("synthetic-admin");
    await page.getByLabel("비밀번호", { exact: true }).fill("synthetic-only-password");
    await page.getByRole("button", { name: "로그인", exact: true }).click();
    await menu("분석 결과").click();
    await page.getByRole("tab", { name: "프로덕션", exact: true }).click(); await hash("#analyses/production");
    assert.equal(await page.locator('input[name="q"]').inputValue(), "", "A new login must not reuse previous private filters.");
  });
  console.log(JSON.stringify({ passed: passed.length, scenarios: passed, mocked_api_requests: totalMockRequests, actual_api_requests: 0, llm_calls: 0 }));
} finally { await browser.close(); }
