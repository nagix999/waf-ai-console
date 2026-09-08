// Browser checks use only fabricated records and intercepted API responses.
import assert from "node:assert/strict";
import { existsSync, readdirSync, mkdtempSync } from "node:fs";
import { homedir, tmpdir } from "node:os";
import { join } from "node:path";
import { pathToFileURL } from "node:url";

const origin = "http://127.0.0.1:15173";
const ids = Object.fromEntries(["complete", "failed", "retry", "run", "key", "profile"].map((name, i) => [name, `${i + 1}`.repeat(8) + "-1111-4111-8111-111111111111"]));
const date = "2026-09-08T00:00:00Z";
const window = { created_from: "2026-09-01T00:00:00Z", created_to: date };
const evaluation = { total: 2, labeled: 2, evaluable: 2, matches: 1, binary_evaluable: 2, binary_decided: 2, support_positive: 1, support_negative: 1, label_coverage: 1, source_groups: [], outcomes: { match: 1, false_negative: 1 }, confusion_matrix: { tp: 0, fn: 1, fp: 0, tn: 1, abstained_positive: 0, abstained_negative: 0 }, metrics: { accuracy: .5, precision: null, recall: 0, f1: 0, coverage: 1, abstention_rate: 0 } };
const profile = { id: ids.profile, name: "테스트 모델", model_name: "example-model", provider: "openai", external_data_approved: true, is_test: true, status: "production", can_assign: true, profile_fingerprint: "a".repeat(64) };
const counts = { total: 2, completed: 1, failed: 1, pending: 0, processing: 0, true_positive: 1, false_positive: 0, inconclusive: 0, critical_high_allowed: 1, false_positive_denied: 0 };
function record(id = ids.complete) {
  const failed = id === ids.failed;
  return { id, status: failed ? "failed" : "completed", source_system: "test-source", analysis_purpose: "test", ingest_channel: "test_lab", event_id: "hidden-event-id", company_name: "테스트 회사", src_ip: "192.0.2.10", dest_ip: "198.51.100.20", src_port: 12345, dest_port: 443, signature: "요청 경로 점검", waf_action: "A", waf_vendor: "test-vendor", created_at: date, total_elapsed_ms: 1200, verdict: failed ? null : "true_positive", severity: "HIGH", model_profile: "test-model", prompt_version: "policy-2", evaluation: { outcome: "unlabeled" }, summary_ko: failed ? null : "요청에서 경로 탐색 구문을 확인했습니다.", retry_of_analysis_id: id === ids.retry ? ids.failed : null, result: failed ? null : { schema_version: "waf-analysis-v2", verdict: "true_positive", confidence_score: .9, summary_ko: "요청에서 경로 탐색 구문을 확인했습니다.", threat_analysis: { severity: "HIGH", category: "경로 탐색", target: "payload", technique_ko: "상위 경로 접근", potential_impact_ko: "파일 노출 가능성", obfuscations: [] }, signature_assessment: { relation: "exact", explanation_ko: "경로 탐색 구문과 일치합니다." }, evidence: [], recommended_checks: [], analyst_guidance: { checks: [], limitations: [] }, tuning_recommendation: { recommended: false }, agent: { framework: "moduagent", execution_mode: "moduagent" } } };
}
const run = { id: ids.run, name: "테스트 결과", kind: "upload", status: "completed", created_at: date, total: 2, accepted: 2, rejected: 0, pending: 0, processing: 0, completed: 2, failed: 0, total_elapsed_ms: 1200, evaluation_summary: evaluation, execution_mode: "moduagent", profile_metadata: profile, source_system: "test-run-source", prompt_version: "policy-2", total_items: 1, facets: { difficulties: [], test_categories: [] }, items: [{ id: "row-1", row_number: 1, analysis_id: ids.complete, case_name: "경로 점검", status: "completed", verdict: "true_positive", ingest_status: "accepted", evaluation: { outcome: "unlabeled" } }] };
const archive = join(homedir(), ".cache/uv/archive-v0");
const modulePath = readdirSync(archive).map(name => join(archive, name, "playwright/driver/package/index.mjs")).find(existsSync);
if (!modulePath) throw new Error("Existing Playwright required");
const { chromium } = await import(pathToFileURL(modulePath).href);
const browser = await chromium.launch({ executablePath: join(homedir(), ".cache/ms-playwright/chromium-1208/chrome-linux64/chrome"), headless: true, args: ["--no-sandbox"] });
const output = mkdtempSync(join(tmpdir(), "waf-ux-workflows-"));
const passed = [];
const rawPayload = "GET /%3Cscript%3E HTTP/1.1\r\nHost: example.invalid\r\nCookie: fixture-only=abc\r\n\r\n<script>untrusted text</script>\n" + "plain text ".repeat(9000);
const decoding = { decoder_version: "fixture-v2", scanned_chars: 100, total_chars: 200, scan_truncated: true, warnings: [], items: [
  { id: "decoded-1", field: "payload", start: 5, end: 17, original: "%3Cscript%3E", decoded: "<script>", steps: [{ encoding: "url_percent", input: "%3Cscript%3E", output: "<script>" }], warnings: [] },
  { id: "decoded-2", field: "payload", start: 20, end: 30, original: "${jndi:ldap://example.invalid/a}", decoded: "${jndi:ldap://example.invalid/a}", steps: [], warnings: ["jndi_lookup_not_executed"] },
] };
const agentRuns = [{ id: "agent-id-hidden", fingerprint: "fingerprint-hidden", framework_run_id: "framework-hidden", status: "failed", started_at: date, duration_ms: 1500, steps: [
  { id: "step-1", sequence: 1, step_type: "parser", name: "범용 HTTP 파싱", status: "completed", duration_ms: 0, input: "parser-input-hidden", output: "parser-output-visible", metadata: { tool: "fixture-only" }, tool_calls: [], started_at: date },
  { id: "step-2", sequence: 2, step_type: "llm_primary", name: "Primary LLM 판정", status: "failed", duration_ms: 1500, input: "llm-input-hidden", output: "llm-output-visible", metadata: { output_validation_retry: { attempts: [{ usage: { total_tokens: 33 } }] } }, tool_calls: [], started_at: date },
] }];
async function scenario(name, check, options = {}) {
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 }, acceptDownloads: true, serviceWorkers: "block" });
  let signedIn = options.signedIn !== false;
  await context.addInitScript(() => { window.__testCopies = []; Object.defineProperty(navigator, "clipboard", { value: { writeText: async value => { window.__testCopies.push(value); } } }); });
  const calls = [], violations = [], errors = [];
  await context.route("**/*", async route => {
    const request = route.request(); const url = new URL(request.url()); const method = request.method(); const path = url.pathname;
    const json = (value, status = 200) => route.fulfill({ status, contentType: "application/json", body: JSON.stringify(value) });
    if (url.origin !== origin) { violations.push("external request"); return route.abort(); }
    if (!path.startsWith("/api/")) { if (method !== "GET") { violations.push("non-api write"); return route.abort(); } return route.continue(); }
    const body = request.postDataJSON(); calls.push({ path, method, query: Object.fromEntries(url.searchParams), body });
    if (method === "POST" && path === "/api/v1/auth/login") { signedIn = body.password === "fixture-correct"; return signedIn ? json({ authenticated: true }) : json({ detail: "invalid_credentials" }, 401); }
    if (method === "POST" && path === "/api/v1/test-runs") return json({ id: ids.run, accepted: 1, rejected: 0, duplicates: 0 }, 202);
    if (method === "POST" && path === `/api/v1/analyses/${ids.failed}/retry`) return json({ analysis_id: ids.retry, retry_of_analysis_id: ids.failed, status: "pending", duplicate: false }, 202);
    if (method !== "GET") { violations.push("unexpected write"); return json({ detail: "blocked" }, 403); }
    if (path === "/api/v1/auth/me") return signedIn ? json({ role: "admin", scopes: ["admin"], username: "test-admin" }) : json({ detail: "not_authenticated" }, 401);
    if (path === "/api/v1/dashboard/summary") return json({ window, counts, runtime: { agent_mode: "moduagent", production_profile: profile }, service_api_key: url.searchParams.has("service_api_key_id") ? { id: ids.key, name: "웹 서비스" } : null, evaluation_summary: evaluation, trend: [null, .5, 1].map((value, i) => ({ date: `2026-09-0${i + 1}`, total: 2, evaluation_summary: { ...evaluation, metrics: { ...evaluation.metrics, accuracy: value } } })), attribution_unknown_count: 0 });
    if (path === "/api/v1/admin/service-api-keys") return json({ items: [{ id: ids.key, name: "웹 서비스", source_system: "test-source" }] });
    if (path === "/api/v1/model-profiles") return json([profile]);
    if (path === "/api/v1/analyses") return json({ items: [record()], total: 1, evaluation_summary: evaluation });
    if (path === "/api/v1/test-runs") return json({ items: [run], total: 1 });
    if (path === `/api/v1/test-runs/${ids.run}`) return json(run);
    if (path.endsWith("/evaluation-labels")) return json({ items: [] });
    if (path.endsWith("/agent-runs")) return json(agentRuns);
    if (path.endsWith("/event")) return json({ payload: rawPayload, extra_fields: { fixture_field: "<script>no execution</script>" }, decoding });
    if (path.endsWith("/retry-eligibility")) return json({ analysis_id: ids.failed, allowed: true, model_name: "original-model", provider: "openai", prompt_version: "policy-1", costs_may_apply: true });
    if (path.endsWith("/report.pdf")) return route.fulfill({ status: 200, contentType: "application/pdf", body: Buffer.from("%PDF-1.4\n%%EOF") });
    if (path.endsWith("/report.xlsx")) return route.fulfill({ status: 200, contentType: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", body: Buffer.from("PK-test-file") });
    if (/^\/api\/v1\/analyses\/[^/]+$/.test(path)) return json(record(path.split("/").at(-1)));
    violations.push(`unmocked ${path}`); return json({ detail: "unmocked" }, 500);
  });
  const page = await context.newPage(); page.setDefaultTimeout(10000); page.on("pageerror", error => errors.push(error.message));
  const go = async hash => { await page.goto(`${origin}/${hash}`); await page.locator(".app-header h1").waitFor(); assert.ok(await page.locator(".page-description").innerText()); assert.equal(await page.locator(".app-header .metric-help-trigger").count(), 0); };
  try { await check({ page, go, calls }); assert.deepEqual(violations, []); assert.deepEqual(errors, []); passed.push(name); console.log(`PASS ${name}`); }
  finally { await context.close(); }
}
try {
  await scenario("single-input-empty-placeholders-and-automatic-name", async ({ page, go, calls }) => {
    await go("#test"); await page.getByRole("button", { name: "분석 시작", exact: true }).waitFor();
    assert.equal(await page.locator(".test-run-history").count(), 0);
    assert.ok(!calls.some(call => call.path === "/api/v1/test-runs"));
    const direct = page.locator("#test-panel-direct");
    for (const value of await direct.locator('input:not([type="checkbox"]),textarea').evaluateAll(nodes => nodes.map(node => node.value))) assert.equal(value, "");
    assert.ok(await direct.getByLabel("HTTP 원문", { exact: true }).getAttribute("placeholder"));
    await direct.getByLabel("회사명", { exact: true }).fill("테스트 회사");
    await page.getByRole("tab", { name: "배치 파일 분석", exact: true }).click();
    await page.getByRole("tab", { name: "단건 분석", exact: true }).click();
    assert.equal(await direct.getByLabel("회사명", { exact: true }).inputValue(), "테스트 회사");
    await direct.getByLabel("WAF 벤더", { exact: true }).fill("test-vendor");
    await direct.getByLabel("출발지 IP", { exact: true }).fill("192.0.2.10");
    await direct.getByLabel("목적지 IP", { exact: true }).fill("198.51.100.20");
    await direct.getByLabel("WAF 조치", { exact: true }).selectOption("A");
    await direct.getByLabel("HTTP 원문", { exact: true }).fill("GET /entered HTTP/1.1\nHost: example.invalid\n\n");
    await page.screenshot({ path: join(output, "single-test.png") });
    await direct.getByRole("button", { name: "분석 시작", exact: true }).click();
    await page.waitForFunction(id => location.hash === `#test-runs/${id}`, ids.run);
    const sent = calls.find(call => call.method === "POST" && call.path === "/api/v1/test-runs").body;
    assert.equal(sent.name, sent.idempotency_key); assert.equal(sent.event.event_id, `event-${sent.idempotency_key}`);
    assert.ok(sent.event.payload.includes("/entered")); assert.ok(!sent.event.payload.includes("/search?q=example"));
    assert.ok(!Object.hasOwn(sent.event, "src_port"));
    await page.getByRole("heading", { name: "테스트 결과", exact: true }).waitFor();
    assert.equal(await page.getByRole("table").count(), 1, "Metrics tables stay in the detail dialog.");
    await page.getByRole("button", { name: "평가 상세", exact: true }).click();
    await page.getByRole("button", { name: "추가 지표", exact: true }).click();
    const more = page.getByRole("dialog", { name: "추가 평가 지표", exact: true });
    await more.getByRole("button", { name: "MCC 설명", exact: true }).focus();
    await page.getByRole("tooltip").waitFor();
    await page.keyboard.press("Escape");
    assert.equal(await more.isVisible(), true);
    await page.keyboard.press("Escape");
    assert.equal(await more.isVisible(), false);
    assert.equal(await page.getByRole("dialog", { name: "평가 상세", exact: true }).isVisible(), true);
    await page.getByRole("button", { name: "FN · 미탐 방향 1건 문항 보기", exact: true }).click();
    assert.equal(await page.locator("dialog[open]").count(), 0);
    await page.screenshot({ path: join(output, "test-run.png") });
  });
  await scenario("dashboard-key-period-drilldown-and-back", async ({ page, go, calls }) => {
    await go("#dashboard"); await page.getByLabel("연동 키", { exact: true }).selectOption(ids.key);
    await page.getByRole("button", { name: "요청 경로 점검", exact: true }).waitFor();
    assert.ok(calls.some(call => call.path === "/api/v1/analyses" && call.query.service_api_key_id === ids.key && call.query.created_from === window.created_from && call.query.created_to === window.created_to));
    await page.screenshot({ path: join(output, "dashboard.png") });
    await page.locator(".stats-grid article").filter({ hasText: "실패" }).getByRole("button", { name: "결과 보기 →" }).click();
    await page.waitForFunction(() => location.hash === "#analyses/production");
    await page.getByRole("button", { name: "요청 경로 점검", exact: true }).waitFor();
    assert.ok(calls.some(call => call.path === "/api/v1/analyses" && call.query.status === "failed" && call.query.service_api_key_id === ids.key));
    await page.goBack(); await page.getByLabel("연동 키", { exact: true }).waitFor();
    assert.equal(await page.getByLabel("연동 키", { exact: true }).inputValue(), ids.key);
  });
  await scenario("failed-rerun-confirmation-and-original-settings", async ({ page, go, calls }) => {
    await go(`#analyses/${ids.failed}`); await page.getByRole("button", { name: "재실행", exact: true }).click();
    const dialog = page.getByRole("dialog", { name: "실패한 분석 재실행", exact: true });
    await dialog.getByText("original-model", { exact: true }).waitFor();
    assert.equal(await dialog.getByRole("button", { name: "재실행 시작", exact: true }).isEnabled(), false);
    assert.ok((await dialog.innerText()).includes("마스킹 없이 OpenAI"));
    assert.ok(!calls.some(call => call.method === "POST"));
    await dialog.getByRole("checkbox").check();
    await dialog.getByRole("button", { name: "재실행 시작", exact: true }).dblclick();
    await page.waitForFunction(id => location.hash === `#analyses/${id}`, ids.retry);
    const writes = calls.filter(call => call.method === "POST"); assert.equal(writes.length, 1); assert.equal(writes[0].body.cost_acknowledged, true);
    assert.deepEqual(Object.keys(writes[0].body).sort(), ["cost_acknowledged", "idempotency_key"]);
    await page.getByRole("button", { name: "이전 실패 보기", exact: true }).waitFor();
  });
  await scenario("detail-downloads-hidden-identifiers-tooltip-and-mobile", async ({ page, go, calls }) => {
    await go(`#analyses/${ids.complete}`); await page.getByRole("button", { name: "PDF 다운로드", exact: true }).waitFor();
    assert.ok(!(await page.locator("body").innerText()).includes("hidden-event-id"));
    assert.ok(!calls.some(call => call.path.endsWith("/event") || call.path.endsWith("/agent-runs")));
    for (const format of ["PDF", "Excel"]) { const download = page.waitForEvent("download"); await page.getByRole("button", { name: `${format} 다운로드`, exact: true }).click(); assert.ok((await download).suggestedFilename().includes(ids.complete)); }
    await page.getByRole("button", { name: "이벤트·실행 정보", exact: true }).click();
    const dialog = page.getByRole("dialog", { name: "이벤트·실행 정보", exact: true }); await dialog.getByText("hidden-event-id", { exact: true }).waitFor();
    await page.keyboard.press("Escape");
    assert.equal(await page.getByRole("button", { name: "판정 요약 설명", exact: true }).count(), 0);
    assert.equal(await page.getByRole("button", { name: "새로고침 설명", exact: true }).count(), 0);
    assert.equal(await page.locator(".decision-summary-line strong").evaluate(node => getComputedStyle(node).webkitLineClamp), "none");
    await page.getByRole("button", { name: "다운로드 범위 설명", exact: true }).focus();
    await page.getByRole("tooltip").waitFor(); await page.keyboard.press("Escape"); assert.equal(await page.getByRole("tooltip").count(), 0);
    await page.setViewportSize({ width: 390, height: 844 });
    await page.getByRole("button", { name: "다크 모드로 전환", exact: true }).click();
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
    await page.screenshot({ path: join(output, "detail-mobile.png") });
    assert.ok(!calls.some(call => call.method !== "GET"));
  });
  await scenario("login-empty-fields-password-toggle-and-recovery", async ({ page, calls }) => {
    await page.goto(origin); const login = page.locator(".login-card"); await login.waitFor();
    assert.equal(await login.getByLabel("아이디", { exact: true }).inputValue(), "");
    assert.equal(await login.getByLabel("비밀번호", { exact: true }).inputValue(), "");
    await login.getByRole("button", { name: "로그인", exact: true }).click(); assert.equal(calls.filter(call => call.method === "POST").length, 0);
    await login.getByLabel("아이디", { exact: true }).fill("fixture-admin"); await login.getByLabel("비밀번호", { exact: true }).fill("fixture-wrong");
    await login.getByRole("button", { name: "비밀번호 표시", exact: true }).click(); assert.equal(await login.getByLabel("비밀번호", { exact: true }).getAttribute("type"), "text");
    await login.getByRole("button", { name: "비밀번호 숨기기", exact: true }).click();
    await login.getByRole("button", { name: "로그인", exact: true }).click(); await login.getByRole("alert").waitFor();
    assert.match(await login.getByRole("alert").innerText(), /아이디와 비밀번호/);
    await page.setViewportSize({ width: 390, height: 844 }); assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
    await page.screenshot({ path: join(output, "login-mobile.png") });
    await login.getByLabel("비밀번호", { exact: true }).fill("fixture-correct"); await login.getByRole("button", { name: "로그인", exact: true }).click(); await page.locator(".app-header").waitFor();
    assert.equal(calls.filter(call => call.path === "/api/v1/auth/login").length, 2);
  }, { signedIn: false });
  await scenario("raw-inspection-search-copy-wrap-and-lazy-audit", async ({ page, go, calls }) => {
    await go(`#analyses/${ids.complete}`); assert.equal(calls.filter(call => call.path.endsWith("/event")).length, 0);
    await page.getByRole("tab", { name: "HTTP 원문", exact: true }).click(); const view = page.getByRole("region", { name: "HTTP 원문 열람", exact: true }); await view.waitFor();
    assert.equal(await view.locator("pre").textContent(), rawPayload);
    await view.getByRole("searchbox", { name: "HTTP 원문에서 찾기", exact: true }).fill("Cookie"); await view.locator("mark").waitFor(); assert.equal(await view.locator("mark").innerText(), "Cookie");
    await view.getByRole("button", { name: "자동 줄바꿈", exact: true }).click(); assert.equal(await view.getByRole("button", { name: "자동 줄바꿈", exact: true }).getAttribute("aria-pressed"), "false");
    await view.getByRole("button", { name: "전체 복사", exact: true }).click(); await view.getByText("복사했습니다.", { exact: true }).waitFor(); assert.equal(await page.evaluate(() => window.__testCopies.at(-1)), rawPayload);
    await view.getByRole("searchbox").fill("plain text"); assert.match(await view.getByRole("status").first().innerText(), /200\+/);
    assert.equal(await view.locator("script,a,iframe").count(), 0);
    await page.screenshot({ path: join(output, "http-inspector.png") });
    await page.getByRole("tab", { name: "추가 필드 · 1", exact: true }).click(); await page.getByRole("region", { name: "추가 필드 열람", exact: true }).waitFor();
    await page.getByRole("tab", { name: "판정 결과", exact: true }).click(); await page.getByRole("button", { name: "문자열 비교", exact: true }).click();
    const decode = page.getByRole("dialog", { name: "인코딩·난독화 문자열", exact: true }); await decode.getByText("문자열 2개", { exact: true }).waitFor();
    await decode.getByRole("button", { name: "확인 범위", exact: true }).click(); const scope = page.getByRole("dialog", { name: "문자열 확인 범위", exact: true }); await scope.getByText("100 / 200자", { exact: true }).waitFor();
    await scope.getByRole("button", { name: "문자열 확인 범위 닫기", exact: true }).click();
    await decode.getByRole("button", { name: "위치·변환 단계", exact: true }).first().click(); const steps = page.getByRole("dialog", { name: "문자열 1 변환 상세", exact: true }); await steps.waitFor();
    assert.equal(await steps.getByRole("region", { name: "단계 1 변환 후 열람", exact: true }).locator("pre").textContent(), "<script>");
    await page.keyboard.press("Escape"); assert.equal(await decode.isVisible(), true);
    await page.setViewportSize({ width: 390, height: 844 }); await page.screenshot({ path: join(output, "decoding-mobile.png") });
    assert.ok(await decode.evaluate(node => node.scrollWidth <= node.clientWidth));
    await page.keyboard.press("Escape"); assert.equal(calls.filter(call => call.path.endsWith("/event")).length, 1); assert.ok(!calls.some(call => call.method !== "GET"));
  });
  await scenario("agent-step-readability-and-technical-drilldown", async ({ page, go, calls }) => {
    await go(`#analyses/${ids.failed}`); await page.getByRole("button", { name: "이벤트·실행 정보", exact: true }).click();
    const outer = page.getByRole("dialog", { name: "이벤트·실행 정보", exact: true }); await outer.getByRole("tab", { name: "Agent 실행 이력", exact: true }).click();
    await outer.getByText("parser-output-visible", { exact: true }).waitFor(); assert.ok(!(await outer.innerText()).includes("parser-input-hidden")); assert.ok(!(await outer.innerText()).includes("fingerprint-hidden"));
    await outer.getByRole("navigation", { name: "분석 단계", exact: true }).getByRole("button", { name: /위협 분석/ }).click(); await outer.getByText("llm-output-visible", { exact: true }).waitFor();
    await outer.getByRole("tab", { name: "분석 입력", exact: true }).click(); await outer.getByText("llm-input-hidden", { exact: true }).waitFor();
    await outer.getByRole("tab", { name: "실행 정보", exact: true }).click(); assert.match(await outer.getByRole("region", { name: "단계 실행 정보 열람", exact: true }).innerText(), /33/);
    await outer.getByRole("button", { name: "기술 식별정보", exact: true }).click(); const identity = page.getByRole("dialog", { name: "실행 식별정보", exact: true }); await identity.waitFor(); assert.ok((await identity.innerText()).includes("fingerprint-hidden"));
    await page.keyboard.press("Escape"); assert.ok(await outer.isVisible());
    await page.screenshot({ path: join(output, "agent-history.png") }); await page.setViewportSize({ width: 390, height: 844 }); assert.ok(await outer.evaluate(node => node.scrollWidth <= node.clientWidth)); await page.screenshot({ path: join(output, "agent-mobile.png") });
    assert.equal(calls.filter(call => call.path.endsWith("/agent-runs")).length, 1); assert.ok(!calls.some(call => call.method !== "GET"));
  });
  console.log(JSON.stringify({ passed: passed.length, output, realApiWrites: 0, realLlmCalls: 0 }));
} finally { await browser.close(); }
