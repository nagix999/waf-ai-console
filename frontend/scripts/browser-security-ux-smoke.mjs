// Starts its own loopback Vite server without an API proxy. Every API response
// is fabricated and intercepted; this never contacts a deployed service/model.
import assert from "node:assert/strict";
import { existsSync, mkdtempSync, readdirSync } from "node:fs";
import { homedir, tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { createServer } from "vite";
import react from "@vitejs/plugin-react";

const frontendRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const projectRoot = resolve(frontendRoot, "..");
const output = mkdtempSync(join(tmpdir(), "waf-browser-security-ux-"));
const archive = join(homedir(), ".cache/uv/archive-v0");
const modulePath = readdirSync(archive).map(name => join(archive, name, "playwright/driver/package/index.mjs")).find(existsSync);
if (!modulePath) throw new Error("Existing Playwright required; installation is not permitted");
const { chromium } = await import(pathToFileURL(modulePath).href);
const server = await createServer({
  root: frontendRoot, configFile: false, plugins: [react()], logLevel: "error",
  cacheDir: join(output, "vite-cache"),
  server: { host: "127.0.0.1", port: 0, strictPort: true, hmr: false, fs: { allow: [projectRoot, output] } },
});
let browser;
const passed = [];
const assetPaths = new Set([
  "/", "/@react-refresh", "/@vite/client", "/node_modules/vite/dist/client/env.mjs",
  ...readdirSync(join(frontendRoot, "src")).filter(name => /\.(?:jsx?|css)$/.test(name)).map(name => "/src/" + name),
  "/@fs" + join(projectRoot, "docs/Production_API_v0.1.md"),
]);
const profile = {
  id: "11111111-1111-4111-8111-111111111111", name: "Fixture only", provider: "vllm",
  model_name: "fixture-model", status: "production", is_test: true, can_assign: true,
  profile_fingerprint: "a".repeat(64),
};
const summary = { runtime: { agent_mode: "moduagent", production_profile: profile } };
const deferred = () => { let release; const promise = new Promise(resolve => { release = resolve; }); return { promise, release }; };

try {
  await server.listen();
  const origin = `http://127.0.0.1:${server.httpServer.address().port}`;
  browser = await chromium.launch({
    executablePath: join(homedir(), ".cache/ms-playwright/chromium-1208/chrome-linux64/chrome"),
    headless: true, args: ["--no-sandbox"],
  });

  async function scenario(name, check, { signedInInitially = true, holdLogout = false } = {}) {
    const context = await browser.newContext({ viewport: { width: 1360, height: 960 }, serviceWorkers: "block" });
    const gate = deferred();
    let signedIn = signedInInitially;
    let logoutCount = 0;
    const calls = [], violations = [], pageErrors = [];
    await context.addInitScript(() => {
      window.__unhandledSecurityErrors = [];
      addEventListener("unhandledrejection", () => window.__unhandledSecurityErrors.push("unhandled rejection"));
    });
    await context.routeWebSocket("**/*", socket => socket.close());
    await context.route("**/*", async route => {
      const request = route.request(), url = new URL(request.url()), path = url.pathname;
      const json = (value, status = 200) => route.fulfill({ status, contentType: "application/json", body: JSON.stringify(value) });
      if (url.origin !== origin) { violations.push("external request blocked"); return route.abort(); }
      if (path.startsWith("/api/")) {
        calls.push({ method: request.method(), path });
        if (path === "/api/v1/auth/me" && request.method() === "GET") return signedIn
          ? json({ kind: "admin_session", username: "fixture-admin", scopes: ["admin", "ingest", "review"] })
          : json({ detail: "authentication_required" }, 401);
        if (path === "/api/v1/auth/login" && request.method() === "POST") return json({ detail: "csrf_origin_invalid" }, 403);
        if (path === "/api/v1/auth/logout" && request.method() === "POST") {
          logoutCount += 1;
          if (holdLogout) await gate.promise;
          else if (logoutCount === 1) return json({ detail: "csrf_origin_required" }, 403);
          signedIn = false;
          return route.fulfill({ status: 204, body: "" });
        }
        if (path === "/api/v1/dashboard/summary" && request.method() === "GET") return json(summary);
        if (path === "/api/v1/model-profiles" && request.method() === "GET") return json([profile]);
        violations.push(`unmocked API blocked: ${request.method()} ${path}`);
        return route.abort();
      }
      const cachedDependency = path.startsWith("/@fs" + join(output, "vite-cache") + "/deps/") && /\/[A-Za-z0-9_.-]+\.js$/.test(path);
      if (request.method() === "GET" && (assetPaths.has(path) || cachedDependency)) return route.continue();
      if (request.method() === "GET" && path === "/favicon.ico") return route.fulfill({ status: 204, body: "" });
      violations.push("unexpected asset blocked: " + path);
      return route.abort();
    });
    const page = await context.newPage();
    page.setDefaultTimeout(10000);
    page.on("pageerror", error => pageErrors.push(error.message));
    try {
      await page.goto(origin + "/#test");
      await check({ page, calls, gate, authenticated: () => signedIn });
      assert.deepEqual(violations, []);
      assert.deepEqual(pageErrors, []);
      assert.deepEqual(await page.evaluate(() => window.__unhandledSecurityErrors), []);
      await page.screenshot({ path: join(output, name + ".png"), fullPage: true });
      passed.push(name);
      console.log("PASS " + name);
    } catch (error) {
      console.log(JSON.stringify({ scenario: name, output, violations, pageErrors, calls }));
      await page.screenshot({ path: join(output, name + "-failure.png"), fullPage: true });
      throw error;
    } finally { gate.release(); await context.close(); }
  }

  await scenario("login-csrf-denial-is-address-guidance", async ({ page, calls, authenticated }) => {
    await page.getByLabel("아이디", { exact: true }).fill("fixture-admin");
    await page.getByLabel("비밀번호", { exact: true }).fill("fixture-password");
    await page.getByRole("button", { name: "로그인", exact: true }).click();
    const alert = page.getByRole("alert");
    await alert.waitFor();
    assert.match(await alert.innerText(), /서비스 주소를 확인/);
    assert.doesNotMatch(await alert.innerText(), /서버에 연결|csrf_|fixture-password/);
    assert.equal(await page.locator(".app-header").count(), 0);
    assert.equal(await page.getByLabel("아이디", { exact: true }).inputValue(), "fixture-admin");
    assert.equal(await page.getByRole("button", { name: "로그인", exact: true }).isEnabled(), true);
    assert.equal(authenticated(), false);
    assert.equal(calls.filter(call => call.path === "/api/v1/auth/login").length, 1);
  }, { signedInInitially: false });

  await scenario("logout-denial-preserves-draft-and-retry-succeeds", async ({ page, calls, authenticated }) => {
    await page.getByRole("heading", { name: "단건 분석", exact: true }).waitFor();
    const direct = page.locator("#test-panel-direct");
    await direct.getByLabel("테스트명", { exact: true }).fill("보존할 테스트 초안");
    await direct.getByLabel("회사명", { exact: true }).fill("가상 회사");
    const previousUrl = page.url();
    await page.getByRole("button", { name: "로그아웃", exact: true }).click();
    const alert = page.getByRole("alert");
    await alert.waitFor();
    assert.match(await alert.innerText(), /로그아웃하지 못했습니다.*서비스 주소를 확인/);
    assert.equal(page.url(), previousUrl);
    assert.equal(await direct.getByLabel("테스트명", { exact: true }).inputValue(), "보존할 테스트 초안");
    assert.equal(await direct.getByLabel("회사명", { exact: true }).inputValue(), "가상 회사");
    assert.equal(await page.locator(".login-card").count(), 0);
    assert.equal(authenticated(), true);
    await page.screenshot({ path: join(output, "logout-denial-preserved-screen.png"), fullPage: true });
    await page.getByRole("button", { name: "로그아웃", exact: true }).click();
    await page.getByRole("button", { name: "로그인", exact: true }).waitFor();
    assert.equal(authenticated(), false);
    assert.equal(await page.locator(".app-header").count(), 0);
    assert.equal(calls.filter(call => call.path === "/api/v1/auth/logout").length, 2);
  });

  await scenario("pending-logout-disables-repeated-clicks", async ({ page, calls, gate, authenticated }) => {
    await page.getByRole("heading", { name: "단건 분석", exact: true }).waitFor();
    await page.getByRole("button", { name: "로그아웃", exact: true }).click();
    const pending = page.getByRole("button", { name: "로그아웃 중…", exact: true });
    await pending.waitFor();
    assert.equal(await pending.isDisabled(), true);
    const position = await pending.boundingBox();
    await page.mouse.click(position.x + position.width / 2, position.y + position.height / 2, { clickCount: 3 });
    await pending.evaluate(node => { node.click(); node.click(); });
    await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
    assert.equal(calls.filter(call => call.path === "/api/v1/auth/logout").length, 1);
    assert.equal(authenticated(), true);
    assert.equal(await page.locator(".login-card").count(), 0);
    await page.screenshot({ path: join(output, "logout-pending.png"), fullPage: true });
    gate.release();
    await page.getByRole("button", { name: "로그인", exact: true }).waitFor();
    assert.equal(authenticated(), false);
    assert.equal(calls.filter(call => call.path === "/api/v1/auth/logout").length, 1);
  }, { holdLogout: true });

  console.log(JSON.stringify({ passed: passed.length, output, realApiRequests: 0, realLlmCalls: 0, server: "isolated loopback; no API proxy" }));
} finally {
  await browser?.close();
  await server.close();
}
