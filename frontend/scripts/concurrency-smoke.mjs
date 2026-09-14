// Isolated loopback UI, fabricated API responses, all remote traffic blocked.
import assert from "node:assert/strict";
import { existsSync, mkdtempSync, readdirSync } from "node:fs";
import { homedir, tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { createServer } from "vite";
import react from "@vitejs/plugin-react";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const output = mkdtempSync(join(tmpdir(), "waf-concurrency-ui-"));
const archive = join(homedir(), ".cache/uv/archive-v0");
const driver = readdirSync(archive).map(name => join(archive, name, "playwright/driver/package/index.mjs")).find(existsSync);
assert.ok(driver, "Existing Playwright installation is required");
const { chromium } = await import(pathToFileURL(driver).href);
const server = await createServer({ root, configFile: false, plugins: [react()], logLevel: "error", cacheDir: join(output, "cache"),
  server: { host: "127.0.0.1", port: 0, hmr: false, fs: { allow: [resolve(root, ".."), output] } } });
let browser;
try {
  await server.listen();
  const origin = `http://127.0.0.1:${server.httpServer.address().port}`;
  browser = await chromium.launch({ executablePath: join(homedir(), ".cache/ms-playwright/chromium-1208/chrome-linux64/chrome"), headless: true, args: ["--no-sandbox"] });
  for (const [name, width, theme] of [["desktop", 1360, "light"], ["mobile", 390, "dark"]]) {
    const context = await browser.newContext({ viewport: { width, height: 950 }, serviceWorkers: "block" });
    await context.addInitScript(value => localStorage.setItem("waf-console-theme", value), theme);
    const violations = [], errors = [], writes = [];
    const hash = "a".repeat(64);
    let concurrency = { state_token: hash, revision: 0, production: 1, test: 1, active: { production: 1, test: 0 },
      servers: [{ server_key: "10.0.0.10:8000", max_calls: 1, profiles: [{ id: "fixture-1", name: "판정 모델" }, { id: "fixture-2", name: "검증 모델" }] }] };
    let conflict = false;
    const profile = { id: "fixture-1", name: "판정 모델", provider: "vllm", model_name: "fixture-model", status: "production", is_test: true, can_assign: true };
    await context.routeWebSocket("**/*", socket => socket.close());
    await context.route("**/*", async route => {
      const request = route.request(), url = new URL(request.url()), path = url.pathname;
      const json = (body, status = 200) => route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });
      if (url.origin !== origin) { violations.push("remote traffic"); return route.abort(); }
      if (!path.startsWith("/api/")) return route.continue(); // Local static server has no API proxy.
      if (path === "/api/v1/admin/agent-settings/concurrency") {
        if (request.method() === "GET") return json(concurrency);
        if (request.method() === "PUT") {
          const body = request.postDataJSON(); writes.push(body);
          if (conflict) return json({ detail: "concurrency_configuration_changed" }, 409);
          assert.equal(body.expected_state_token, concurrency.state_token);
          concurrency = { ...concurrency, state_token: "b".repeat(64), revision: 1, production: body.production, test: body.test,
            servers: body.servers.map(row => ({ ...row, profiles: concurrency.servers.find(server => server.server_key === row.server_key).profiles })) };
          return json(concurrency);
        }
      }
      if (request.method() !== "GET") { violations.push("unexpected write"); return route.abort(); }
      if (path === "/api/v1/auth/me") return json({ kind: "admin_session", username: "fixture", scopes: ["admin"] });
      if (path === "/api/v1/dashboard/summary") return json({ runtime: { agent_mode: "moduagent", production_profile: profile } });
      if (path === "/api/v1/model-profiles") return json([profile]);
      if (path === "/api/v1/admin/agent-settings") return json({ state_token: hash, revision: 0, profiles: [profile], assignments:
        { production: { primary_profile_id: profile.id, verifier_profile_id: null }, test: { primary_profile_id: profile.id, verifier_profile_id: null } } });
      violations.push("unmocked API " + path); return route.abort();
    });
    const page = await context.newPage(); page.setDefaultTimeout(10000);
    page.on("pageerror", error => errors.push(error.message));
    try {
      await page.goto(origin + "/#settings/agents");
      await page.getByRole("tab", { name: "동시 처리", exact: true }).click();
      const production = page.getByRole("group", { name: "프로덕션", exact: true }).getByLabel("최대 분석 수");
      const test = page.getByRole("group", { name: "테스트", exact: true }).getByLabel("최대 분석 수");
      const limit = page.getByLabel("10.0.0.10:8000 최대 호출 수");
      await production.fill("10"); await test.fill("4"); await limit.fill("10");
      await page.getByRole("tab", { name: "모델 배정", exact: true }).click();
      await page.getByRole("tab", { name: "동시 처리", exact: true }).click();
      assert.equal(await production.inputValue(), "10");
      await test.fill(""); assert.ok(await page.getByRole("button", { name: "설정 저장", exact: true }).isDisabled());
      await test.fill("4");
      await page.getByRole("button", { name: "설정 저장", exact: true }).click();
      let dialog = page.getByRole("dialog", { name: "동시 처리 변경", exact: true });
      await dialog.getByRole("button", { name: "취소", exact: true }).click(); assert.equal(writes.length, 0);
      await page.getByRole("button", { name: "설정 저장", exact: true }).click();
      await dialog.getByRole("button", { name: "적용", exact: true }).click(); await dialog.waitFor({ state: "hidden" });
      assert.equal(writes.length, 1); assert.equal(writes[0].production, 10); assert.equal(writes[0].servers[0].max_calls, 10);
      assert.ok(await page.getByRole("button", { name: "설정 저장", exact: true }).isDisabled());
      const dimensions = await page.evaluate(() => ({ width: innerWidth, document: document.documentElement.scrollWidth }));
      await page.screenshot({ path: join(output, name + ".png"), fullPage: true });
      assert.ok(dimensions.document <= dimensions.width + 1, JSON.stringify({ ...dimensions, screenshot: join(output, name + ".png") }));
      conflict = true; await production.fill("9");
      await page.getByRole("button", { name: "설정 저장", exact: true }).click();
      await dialog.getByRole("button", { name: "적용", exact: true }).click();
      await dialog.getByText("설정이나 서버 목록이 바뀌었습니다. 새로고침 후 다시 확인하세요.", { exact: true }).waitFor();
      assert.ok(await dialog.getByRole("button", { name: "적용", exact: true }).isDisabled());
      await dialog.getByRole("button", { name: "취소", exact: true }).click();
      await page.getByRole("button", { name: "새로고침", exact: true }).click();
      await page.getByText("조회 시 처리 중 1건 · LLM 호출 대기 포함", { exact: true }).waitFor();
      assert.equal(await production.inputValue(), "10"); assert.equal(writes.length, 2);
      assert.deepEqual(errors, []); assert.deepEqual(violations, []);
      console.log(`PASS ${name}: bounds, shared endpoint, draft retention, confirm/cancel, save, conflict and refresh`);
    } finally { await context.close(); }
  }
  console.log("Screenshots: " + output);
} finally { await browser?.close(); await server.close(); }
