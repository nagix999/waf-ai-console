/** Offline browser regression: every API is a fixture; unexpected writes blocked. */
import assert from "node:assert/strict";
import { existsSync, readdirSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";
import { pathToFileURL } from "node:url";

const origin = "http://127.0.0.1:15173";
const archive = join(homedir(), ".cache/uv/archive-v0");
const driver = readdirSync(archive).map(entry => join(archive, entry, "playwright/driver/package/index.mjs")).find(existsSync);
assert.ok(driver, "An installed Playwright driver is required");
const { chromium } = await import(pathToFileURL(driver).href);
const executablePath = join(homedir(), ".cache/ms-playwright/chromium-1208/chrome-linux64/chrome");
const browser = await chromium.launch({ executablePath, headless: true, args: ["--no-sandbox"] });
const id = n => `${String(n).padStart(8, "0")}-1111-4111-8111-111111111111`;
const hash = "a".repeat(64);
const profiles = [
  { id: id(1), name: "내부 판정 모델", provider: "vllm", model_name: "fixture-gemma", status: "production", is_test: true },
  { id: id(2), name: "별도 검증 모델", provider: "openai", model_name: "fixture-external", status: "verified", is_test: false },
  { id: id(3), name: "미검증 모델", provider: "vllm", model_name: "fixture-draft", status: "draft", is_test: false },
].map(p => ({ ...p, can_assign: p.status !== "draft", profile_fingerprint: hash, agent_roles: [],
  external_data_approved: p.provider === "openai", base_url: p.provider === "openai" ? "https://api.openai.com/v1" : "http://10.1.2.3:8000/v1",
  context_window: 32768, max_output_tokens: 3072, timeout_seconds: 120, test_concurrency: 1, tls_verify: true, has_api_key: true }));
const policy = { id: id(4), name: "공통 지침", version_number: 1, policy_text: "입력 근거와 해석을 구분하세요.",
  change_note: "브라우저 점검", created_by: "fixture", created_at: "2026-09-10T00:00:00Z", content_hash: hash, parent_version_id: null };
try {
  for (const [name, width, height, theme] of [["desktop", 1440, 1000, "light"], ["mobile", 390, 844, "dark"]]) {
    const context = await browser.newContext({ viewport: { width, height } });
    await context.addInitScript(value => localStorage.setItem("waf-console-theme", value), theme);
    const violations = []; const writes = []; const errors = [];
    let configuration = { revision: 0, state_token: hash, profiles,
      assignments: { production: { primary_profile_id: id(1), verifier_profile_id: null }, test: { primary_profile_id: id(1), verifier_profile_id: null } } };
    await context.route("**/*", async route => {
      const request = route.request(); const url = new URL(request.url());
      if (url.origin !== origin) { violations.push("external_traffic"); return route.abort(); }
      if (!url.pathname.startsWith("/api/")) return route.continue();
      const path = url.pathname; const json = (body, status = 200) => route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });
      if (request.method() === "PUT" && path === "/api/v1/admin/agent-settings") {
        const body = request.postDataJSON(); writes.push(body);
        assert.equal(body.expected_state_token, configuration.state_token);
        assert.equal(body.external_transfer_acknowledged, true);
        configuration = { ...configuration, revision: 1, state_token: "b".repeat(64), assignments: { production: body.production, test: body.test } };
        return json(configuration);
      }
      if (request.method() !== "GET") { violations.push(`unexpected_write:${path}`); return json({ detail: "blocked" }, 403); }
      if (path === "/api/v1/auth/me") return json({ kind: "admin", username: "fixture-admin", scopes: ["admin"] });
      if (path === "/api/v1/admin/agent-settings") return json(configuration);
      if (path === "/api/v1/admin/agent-settings/diagnostics") return json({ days: 7, purpose: "all", counts: {
        total: 12, completed: 10, failed: 1, measured: 8, inconclusive: 2, primary_evidence_rejected: 1,
        verifier_evidence_rejected: 0, primary_model_inconclusive: 1, verifier_model_inconclusive: 1,
        verifier_failed: 0, verdict_disagreement: 0, primary_repair_attempted: 3, primary_repair_recovered: 2,
        verifier_repair_attempted: 1, verifier_repair_recovered: 1, parser_incomplete: 2, input_truncated: 0,
      }, inconclusive_rate: .25, unmeasured_completed: 2, llm_failure_counts: { output_validation_failed: 1 } });
      if (path === "/api/v1/dashboard/summary") return json({ counts: { total: 0 }, runtime: { agent_mode: "moduagent", production_profile: profiles[0] } });
      if (path === "/api/v1/model-profiles") return json(profiles);
      if (/^\/api\/v1\/model-profiles\/[^/]+\/tests$/.test(path)) return json([]);
      if (path === "/api/v1/admin/internal-egress") return json([{ id: id(5), ip_address: "10.1.2.3", port: 8000, revision: 1 }]);
      if (path === "/api/v1/admin/prompt-policies") return json({ items: [policy], active_version_id: policy.id,
        revision: 1, max_policy_chars: 4000, fixed_instructions: "고정 규칙", fixed_rules_version: "fixture" });
      if (path === `/api/v1/admin/prompt-policies/${policy.id}`) return json(policy);
      violations.push(`unmocked_read:${path}`); return json({ detail: "unmocked" }, 500);
    });
    const page = await context.newPage(); page.setDefaultTimeout(10000);
    page.on("pageerror", error => errors.push(error.message));
    await page.goto(origin + "/#settings/agents");
    await page.getByRole("heading", { name: "역할별 모델", exact: true }).waitFor();
    const production = page.getByRole("group", { name: "프로덕션", exact: true });
    const verifier = production.getByLabel(/Verifier/);
    await verifier.selectOption(id(2));
    assert.equal(await production.getByRole("option", { name: "미검증 모델 · 검증 필요" }).first().evaluate(node => node.disabled), true);
    await page.getByRole("button", { name: "배정 저장", exact: true }).click();
    const dialog = page.getByRole("dialog", { name: "모델 배정 확인" });
    await dialog.waitFor(); assert.equal(await dialog.getByRole("button", { name: "적용", exact: true }).isDisabled(), true);
    await dialog.getByRole("checkbox").check(); await dialog.getByRole("button", { name: "적용", exact: true }).click();
    await dialog.waitFor({ state: "hidden" }); assert.equal(writes.length, 1);
    await page.getByRole("tab", { name: "공통 지침", exact: true }).click();
    await page.getByRole("heading", { name: "공통 판정 지침", exact: true }).waitFor();
    await page.getByRole("tab", { name: "모델 배정", exact: true }).click();
    assert.equal(await verifier.inputValue(), id(2));
    const dimensions = await page.evaluate(() => ({ width: innerWidth, document: document.documentElement.scrollWidth }));
    assert.ok(dimensions.document <= dimensions.width + 1, JSON.stringify(dimensions));
    await page.screenshot({ path: `/tmp/waf-agent-settings-${name}.png`, fullPage: true });
    await page.getByRole("tab", { name: "처리 현황", exact: true }).click();
    await page.getByText("원문 인용", { exact: false }).first().waitFor();
    await page.getByRole("tab", { name: "LLM 프로필", exact: true }).click();
    await page.getByRole("heading", { name: "모델 목록", exact: true }).waitFor();
    await page.goBack(); await page.getByRole("heading", { name: "역할별 모델", exact: true }).waitFor();
    assert.deepEqual(violations, []); assert.deepEqual(errors, []); assert.equal(writes.length, 1);
    console.log(`${name}: role assignment, external consent, common policy, diagnostics and Back passed`);
    await context.close();
  }
} finally { await browser.close(); }
