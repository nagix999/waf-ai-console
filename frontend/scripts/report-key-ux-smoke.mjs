// Mock-only browser checks. Never permit access to a live API or LLM.
import assert from "node:assert/strict";
import { existsSync, readdirSync, mkdtempSync } from "node:fs";
import { homedir, tmpdir } from "node:os";
import { join } from "node:path";
import { pathToFileURL } from "node:url";
const origin = "http://127.0.0.1:15173";
const archive = join(homedir(), ".cache/uv/archive-v0");
const modulePath = readdirSync(archive).map(name => join(archive, name, "playwright/driver/package/index.mjs")).find(existsSync);
const { chromium } = await import(pathToFileURL(modulePath).href);
const browser = await chromium.launch({ executablePath: join(homedir(), ".cache/ms-playwright/chromium-1208/chrome-linux64/chrome"), headless: true, args: ["--no-sandbox"] });
const output = mkdtempSync(join(tmpdir(), "waf-report-key-"));
const detail = { id: "11111111-2222-4333-8444-555555555555", event_id: "fixture-event", status: "completed", analysis_purpose: "production", company_name: "검증 회사", src_ip: "192.0.2.1", dest_ip: "198.51.100.1", created_at: "2026-09-15T00:00:00Z", result: { verdict: "true_positive", summary_ko: "요청 경로에서 상위 디렉터리 접근 시도를 확인했습니다.", threat_analysis: { severity: "CRITICAL", technique_ko: "상위 경로 접근", potential_impact_ko: "요청 값이 파일 경로로 사용되면 파일 내용이 노출될 수 있습니다." }, evidence: [{ field: "payload.path", excerpt: "../example\r\n<img src=https://blocked.invalid/excerpt>", interpretation_ko: "상위 경로 접근 구문은 파일 노출 위험과 연결됩니다." }], agent: { framework: "moduagent" } } };
const html = `<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head><body><main id="fixture-root" style="max-width:1100px;margin:20px auto;padding:14px"></main><script type="module">
import RefreshRuntime from '/@react-refresh'; RefreshRuntime.injectIntoGlobalHook(window); window.$RefreshReg$=()=>{}; window.$RefreshSig$=()=>type=>type; window.__vite_plugin_react_preamble_installed__=true;
await import('/src/styles.css');
const {default:React}=await import('/node_modules/.vite/deps/react.js'); const {default:ReactDOM}=await import('/node_modules/.vite/deps/react-dom_client.js');
const {default:Keys}=await import('/src/ServiceApiKeys.jsx'); const {default:Report}=await import('/src/AnalysisReport.jsx'); const {default:Downloads}=await import('/src/AnalysisDownloads.jsx'); const h=React.createElement;
function App(){const [mode,setMode]=React.useState('preview'),[appendix,setAppendix]=React.useState(false); return h('div',{},h(Keys),h('div',{className:'decision-card'},h('div',{},h('span',{className:'severity severity-critical'},'CRITICAL'))),h(Downloads,{id:'${detail.id}',status:'completed',includeAppendix:appendix}),h(Report,{detail:${JSON.stringify(detail).replaceAll("<", "\\u003c")},mode,onModeChange:setMode,includeAppendix:appendix,onAppendixChange:setAppendix}));}
ReactDOM.createRoot(document.getElementById('fixture-root')).render(h(App));</script></body></html>`;
const item = { id: "fixture-key", name: "운영 수집기", purpose: "production", source_system: "fixture", scopes: ["ingest"] };
const passed = [];
try {
  for (const scenario of ["retain-light", "purge-dark-mobile", "active-block", "stale-preview"]) {
    const mobile = scenario.includes("mobile");
    const context = await browser.newContext({ viewport: mobile ? { width: 390, height: 844 } : { width: 1440, height: 1000 }, serviceWorkers: "block" });
    const requests = [], violations = [], errors = [];
    let deleted = false, previews = 0;
    await context.route("**/*", route => {
      const request = route.request(), url = new URL(request.url());
      const reply = (body, status = 200) => route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });
      if (url.origin !== origin) { violations.push(url.origin); return route.abort(); }
      if (url.pathname === "/report-key-fixture") return route.fulfill({ contentType: "text/html", body: html });
      if (!url.pathname.startsWith("/api/")) return route.continue();
      requests.push({ path: url.pathname + url.search, method: request.method(), body: request.postDataJSON() });
      if (url.pathname.endsWith("/deletion-preview")) { previews++; return reply({ name: item.name, purpose: "production", analyses: 3, active_analyses: scenario === "active-block" ? 1 : 0, dataset_copies: 2, blocked_references: false, scope: String(previews).repeat(64) }); }
      if (request.method() === "DELETE") {
        if (scenario === "stale-preview" && previews === 1) return reply({ detail: "service_api_key_deletion_changed" }, 409);
        deleted = true; return route.fulfill({ status: 204 });
      }
      if (url.pathname === "/api/v1/admin/service-api-keys") return reply({ items: deleted ? [] : [item] });
      if (url.pathname.endsWith("/report.pdf")) return route.fulfill({ contentType: "application/pdf", body: "%PDF-fixture" });
      violations.push(url.pathname); return route.abort();
    });
    const page = await context.newPage(); page.on("pageerror", error => errors.push(error.message));
    try {
      await page.goto(origin + "/report-key-fixture");
      if (mobile) await page.evaluate(() => document.documentElement.dataset.theme = "dark");
      await page.getByRole("button", { name: "운영 수집기 삭제", exact: true }).click();
      const dialog = page.getByRole("dialog", { name: "API 키 삭제", exact: true });
      await dialog.getByRole("group", { name: "연결된 분석 3건" }).waitFor();
      const name = dialog.getByLabel("확인을 위해 키 이름을 입력하세요", { exact: true });
      assert.equal(await dialog.getByRole("radio", { name: "분석 보존", exact: true }).isChecked(), true);
      assert.equal(await dialog.getByRole("button", { name: "API 키 삭제", exact: true }).isDisabled(), true);
      await name.fill(item.name + " ");
      assert.equal(await dialog.getByRole("button", { name: "API 키 삭제", exact: true }).isDisabled(), true);
      await name.fill(item.name);
      if (scenario === "active-block") {
        assert.equal(await dialog.getByRole("radio", { name: "분석도 함께 삭제", exact: true }).isDisabled(), true);
        await dialog.getByRole("button", { name: "취소", exact: true }).click();
        assert.equal(requests.filter(r => r.method === "DELETE").length, 0);
      } else {
        if (scenario !== "retain-light") await dialog.getByRole("radio", { name: "분석도 함께 삭제", exact: true }).check();
        await page.screenshot({ path: join(output, scenario + "-modal.png"), fullPage: true });
        await dialog.getByRole("button", { name: scenario === "retain-light" ? "API 키 삭제" : "키와 분석 삭제", exact: true }).click();
        if (scenario === "stale-preview") {
          await dialog.getByRole("alert").waitFor();
          assert.match(await dialog.innerText(), /새로|변경/);
          await dialog.getByRole("button", { name: "대상 새로고침", exact: true }).click();
          await page.waitForFunction(() => document.querySelector('.service-key-delete-options input')?.checked && document.querySelector('input[placeholder="운영 수집기"]')?.value === '');
          await name.fill(item.name);
          await dialog.getByRole("button", { name: "API 키 삭제", exact: true }).click();
        }
        await dialog.waitFor({ state: "hidden" });
        const last = requests.filter(r => r.method === "DELETE").at(-1);
        assert.equal(last.body.confirm_name, item.name);
        assert.equal(last.body.delete_analyses, scenario === "purge-dark-mobile");
      }
      const contrast = await page.locator('.severity-critical').evaluate(node => ({ fg: getComputedStyle(node).color, bg: getComputedStyle(node).backgroundColor }));
      assert.deepEqual(contrast, { fg: "rgb(255, 255, 255)", bg: "rgb(180, 35, 59)" });
      await page.getByRole("checkbox", { name: "평가·실행 부록 포함", exact: true }).check();
      const download = page.waitForEvent("download");
      await page.getByRole("button", { name: "PDF 다운로드", exact: true }).click();
      await download;
      const exportRequest = requests.find(r => r.path.includes("report.pdf"));
      assert.match(exportRequest.path, /include_appendix=true&include_decoding=false/);
      assert.match(exportRequest.path, mobile ? /theme=dark/ : /theme=light/);
      assert.equal(await page.locator('.report-paper img').count(), 0);
      await page.evaluate(() => document.fonts.ready);
      assert.equal(await page.evaluate(() => document.fonts.check('13px WafReport')), true);
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
      assert.deepEqual(errors, []); assert.deepEqual(violations, []);
      await page.screenshot({ path: join(output, scenario + "-report.png"), fullPage: true });
      passed.push(scenario);
    } finally { await context.close(); }
  }
  console.log(JSON.stringify({ passed, output, actualApiCalls: 0, llmCalls: 0 }));
} finally { await browser.close(); }
