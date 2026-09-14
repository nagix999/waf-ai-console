// Runs the real app against intercepted fixture APIs. No deployment, real
// authentication, real payload, external request, or LLM call is permitted.
import assert from "node:assert/strict";
import { existsSync, readdirSync, mkdtempSync, readFileSync } from "node:fs";
import { homedir, tmpdir } from "node:os";
import { join } from "node:path";
import { pathToFileURL } from "node:url";
import { createServer } from "vite";
import { decisionExplanation, genericCheck, genericHoldSummary } from "../src/decisionExplanation.js";

const archive = join(homedir(), ".cache/uv/archive-v0");
const modulePath = readdirSync(archive).map(name => join(archive, name, "playwright/driver/package/index.mjs")).find(existsSync);
if (!modulePath) throw Error("Use the existing Playwright installation; do not install a dependency");
const { chromium } = await import(pathToFileURL(modulePath).href);
// Optional deployment check still intercepts every API request with fixtures.
const deploymentOrigin = process.env.WAF_SMOKE_ORIGIN;
if (deploymentOrigin && deploymentOrigin !== "http://127.0.0.1:18080") {
  throw Error("Deployment checks are restricted to the local console");
}
const server = deploymentOrigin ? null : await createServer({ server: { host: "127.0.0.1", port: 0 } });
let browser;
const output = mkdtempSync(join(tmpdir(), "waf-decision-review-"));
const id = "11111111-2222-4333-8444-555555555555";
const assessmentFixture = JSON.parse(readFileSync(new URL("../../backend/tests/fixtures/analyst_assessment_cases.json", import.meta.url), "utf8"))[0].result.analyst_assessment;
const reviewFixtures = JSON.parse(readFileSync(new URL("../../backend/tests/fixtures/hold_review_cases.json", import.meta.url), "utf8"));
const base = {
  id, event_id: "fixture-event", status: "completed", analysis_purpose: "test", ingest_channel: "test_lab",
  company_name: "테스트 회사", source_system: "fixture", src_ip: "192.0.2.1", dest_ip: "198.51.100.1",
  created_at: "2026-09-11T00:00:00Z", total_elapsed_ms: 1200, model_profile: "fixture-model",
  evaluation: { outcome: "unlabeled", reference_label: null },
  result: { verdict: "inconclusive", summary_ko: genericHoldSummary, schema_version: "waf-analysis-v2",
    threat_analysis: { severity: "UNKNOWN", category: "HTTP 요청", target: "payload.body", technique_ko: "저장된 요청 내용을 검토했습니다.", potential_impact_ko: "실제 영향은 확인되지 않았습니다." },
    evidence: [{ field: "payload.body", excerpt: "fixture-only <img src=https://blocked.invalid>", interpretation_ko: "테스트용 원문 발췌입니다." }],
    analyst_guidance: { checks: [genericCheck], limitations: [] }, agent: { framework: "moduagent" },
    tuning_recommendation: { recommended: false, risk_ko: "추가 확인 전에는 WAF 설정 변경을 제안하지 않습니다." },
  },
};
const variants = [
  ["two-sided-evidence", r => { r.analyst_assessment = structuredClone(assessmentFixture); r.diagnostics = { inconclusive_reasons: ["verdict_disagreement"] }; }],
  ["failure-with-evidence", r => { r.analyst_assessment = structuredClone(assessmentFixture); r.diagnostics = { inconclusive_reasons: ["verifier_failed"] }; }],
  ["recorded-missing-body", r => { r.summary_ko = "요청 본문이 수집되지 않아 전송 내용이 공격 구문인지 판단하지 못했습니다."; r.diagnostics = { inconclusive_reasons: ["primary_model_inconclusive"] }; r.analyst_guidance = { checks: [{ source_ko: "같은 요청의 WAF 수집 기록", check_ko: "요청 본문이 전부 수집됐는지 확인하세요.", why_ko: "누락된 요청 내용을 검토해야 합니다." }] }; }],
  ["judgment-pending", r => { r.diagnostics = { inconclusive_reasons: ["verdict_disagreement"] }; r.threat_analysis.technique_ko = "정상 파일명으로 해석됩니다."; }],
  ["evidence-failure", r => { r.diagnostics = { inconclusive_reasons: ["primary_evidence_rejected"] }; r.evidence = []; }],
  ["execution-incomplete", r => { r.diagnostics = { inconclusive_reasons: ["verifier_failed"] }; }],
  ["false-positive", r => { r.verdict = "false_positive"; r.summary_ko = "통화 기호가 가격 문자열에 포함된 정상 요청입니다."; r.threat_analysis.severity = "NONE"; r.analyst_guidance = { checks: [] }; }],
  ["observed-missing-body", r => { r.diagnostics = { inconclusive_reasons: ["input_integrity_limited"], request_integrity: { downgraded_to_inconclusive: true, affected_issue_codes: ["declared_body_not_captured"] } }; r.analyst_guidance = { checks: [] }; }],
  ...["linked_missing_condition", "null_condition_is_source_review", "missing_point_anchors_saved_body", "hostile_condition_is_literal_not_executed"].map(name => [name, r => Object.assign(r, structuredClone(reviewFixtures.find(item => item.name === name).detail.result))]),
];
const passed = [];
try {
  await server?.listen();
  const origin = deploymentOrigin || `http://127.0.0.1:${server.httpServer.address().port}`;
  browser = await chromium.launch({ executablePath: join(homedir(), ".cache/ms-playwright/chromium-1208/chrome-linux64/chrome"), headless: true, args: ["--no-sandbox"] });
  for (const mobile of [false, true]) for (const [name, revise] of variants) {
    const detail = structuredClone(base); revise(detail.result);
    const context = await browser.newContext({ viewport: mobile ? { width: 390, height: 844 } : { width: 1440, height: 1000 }, serviceWorkers: "block" });
    const violations = [], errors = [];
    await context.addInitScript(dark => localStorage.setItem("waf-console-theme", dark ? "dark" : "light"), mobile);
    await context.route("**/*", route => {
      const request = route.request(), url = new URL(request.url());
      const json = value => route.fulfill({ contentType: "application/json", body: JSON.stringify(value) });
      if (url.origin !== origin || request.method() !== "GET") { violations.push("unapproved request"); return route.abort(); }
      if (!url.pathname.startsWith("/api/")) return route.continue();
      if (url.pathname === "/api/v1/auth/me") return json({ username: "fixture-admin", scopes: ["admin"] });
      if (url.pathname === "/api/v1/dashboard/summary") return json({ runtime: { agent_mode: "moduagent" } });
      if (url.pathname === `/api/v1/analyses/${id}`) return json(detail);
      if (url.pathname === `/api/v1/analyses/${id}/evaluation-labels`) return json({ items: [] });
      violations.push("unmocked API"); return route.abort();
    });
    const page = await context.newPage(); page.setDefaultTimeout(10000);
    page.on("pageerror", error => errors.push(error.message));
    try {
      await page.goto(`${origin}/#analyses/${id}`);
      await page.locator(".decision-summary-line").waitFor();
      const decision = decisionExplanation(detail);
      if (decision) assert.equal(await page.locator(".decision-reason-label").innerText(), decision.title_ko);
      assert.equal(await page.locator(".decision-card .metric-help-trigger").count(), 0);
      assert.equal(await page.locator(".tuning-card").count(), 0);
      assert.equal(await page.locator(".evidence-list img").count(), 0);
      assert.equal(await page.locator(".evidence-sides img").count(), 0);
      assert.equal(await page.locator(".decision-issues img, .analyst-checks img").count(), 0);
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
      if (name === "two-sided-evidence" || name === "failure-with-evidence") {
        assert.equal(await page.getByRole("heading", { name: "정탐 근거", exact: true }).count(), 1);
        assert.equal(await page.getByRole("heading", { name: "오탐 근거", exact: true }).count(), 1);
        assert.equal(await page.locator(".evidence-item").count(), 3);
        assert.match(await page.locator(".evidence-sides").innerText(), /code_verifier/);
        assert.equal(await page.locator(".decision-issues").count(), name === "two-sided-evidence" ? 1 : 0);
        const left = await page.locator(".evidence-true_positive").boundingBox();
        const right = await page.locator(".evidence-false_positive").boundingBox();
        if (mobile) assert.ok(right.y >= left.y + left.height);
        else assert.ok(right.x >= left.x + left.width);
      }
      if (name === "false-positive") {
        assert.match(await page.locator(".detailed-analysis").innerText(), /탐지 유형/);
        assert.equal(await page.locator(".analyst-checks").count(), 0);
      } else {
        assert.match(await page.locator(".detailed-analysis").innerText(), /확정된 결론으로 사용하지 마세요/);
        assert.ok(!(await page.locator(".analyst-checks").innerText()).includes(genericCheck.check_ko));
        assert.equal(await page.evaluate(() => document.querySelector(".analyst-checks").getBoundingClientRect().top > document.querySelector(".evidence-card").getBoundingClientRect().top), true);
      }
      if (name === "observed-missing-body") {
        assert.match(await page.locator(".decision-summary-line").innerText(), /수집된 로그에는 본문이 없습니다/);
        assert.match(await page.locator(".analyst-checks").innerText(), /실제로 비어 있던 것인지/);
      }
      if (reviewFixtures.some(item => item.name === name)) {
        const checks = await page.locator(".analyst-checks").innerText();
        assert.equal(await page.locator(".decision-issues").count(), 1);
        if (name === "linked_missing_condition") assert.match(checks, /해당 필드의 허용 형식/);
        else if (name === "hostile_condition_is_literal_not_executed") assert.match(checks, /blocked.invalid/);
        else assert.match(checks, /추가 자료가 부족하다고 확인된 것은 아닙니다/);
      }
      await page.screenshot({ path: join(output, `${name}-${mobile ? "mobile-dark" : "desktop"}.png`), fullPage: true });
      const summary = await page.locator(".decision-summary-line").innerText();
      await page.getByRole("tab", { name: "보고서", exact: true }).click();
      const report = page.getByRole("region", { name: "보고서 보기 설정", exact: true });
      await report.waitFor(); assert.ok((await report.innerText()).includes(summary));
      if (name === "two-sided-evidence") {
        assert.match(await report.innerText(), /정탐 근거/);
        assert.match(await report.innerText(), /오탐 근거/);
        assert.match(await report.innerText(), /판단이 필요한 부분/);
      }
      assert.deepEqual(violations, []); assert.deepEqual(errors, []);
      passed.push(`${name}-${mobile ? "mobile-dark" : "desktop"}`);
    } finally { await context.close(); }
  }
  console.log(JSON.stringify({ passed, target: deploymentOrigin ? "local-docker" : "vite", screenshots: output, actual_api_calls: 0, llm_calls: 0 }));
} finally { await browser?.close(); await server?.close(); }
