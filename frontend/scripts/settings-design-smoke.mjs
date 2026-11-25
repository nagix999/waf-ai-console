// Visual review of the migrated settings/integrations family. All API values
// below are synthetic; unknown requests and every network destination fail.
import assert from "node:assert/strict";
import { existsSync, readFileSync, readdirSync, mkdirSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { build } from "esbuild";
import { r5BrowserFixture } from "./r5-browser-fixture.mjs";

const output = process.env.SCREENSHOT_DIR || fileURLToPath(new URL("../../test-results/settings-structure-v2", import.meta.url));
mkdirSync(output, { recursive: true });
const mock = r5BrowserFixture().replace("export const api=", `
const signatureVersion={id:id(901),version_no:1,name:'벤더별 탐지 설명',change_reason:'합성 참고 설명',parent_id:null,fingerprint:hash,entry_count:3,created_by:'fixture-admin',created_at:date,entries:Array.from({length:3},(_,n)=>({waf_vendor:n%2?'Example WAF B':'Example WAF A',signature:'Example detection '+(n+1),signature_explanation:'탐지 대상과 요청 구조를 대조하는 합성 참고 설명입니다. 개별 요청의 정답을 뜻하지 않습니다.',reference_source:'가상 벤더 문서'}))};
Object.assign(methods,{signatureExplanations:async()=>({items:[signatureVersion],active_version_id:signatureVersion.id,revision:1,total:1,limit:25,offset:0}),signatureExplanation:async()=>signatureVersion,analysisEvidenceMeta:()=>null});
export const api=`);
const source = fileURLToPath(new URL("../src", import.meta.url));
const bundle = await build({ stdin: { contents: readFileSync(join(source, "main.jsx"), "utf8"), loader: "jsx", resolveDir: source }, plugins: [{ name: "offline-api", setup(builder) { builder.onLoad({ filter: /\/api\.js$/ }, () => ({ contents: mock, loader: "js" })); } }], bundle: true, write: false, outfile: join(output, "bundle.js"), platform: "browser", format: "iife", jsx: "automatic", define: { "process.env.NODE_ENV": '"production"' }, loader: { ".md": "text", ".woff2": "dataurl" }, logLevel: "silent" });
const archive = join(homedir(), ".cache/uv/archive-v0");
const driver = process.env.PLAYWRIGHT_MODULE || readdirSync(archive).map(name => join(archive, name, "playwright/driver/package/index.mjs")).find(existsSync);
const { chromium } = await import(pathToFileURL(driver).href);
const browser = await chromium.launch({ executablePath: process.env.CHROMIUM_PATH || join(homedir(), ".cache/ms-playwright/chromium-1208/chrome-linux64/chrome"), headless: true, args: ["--no-sandbox"] });
const routes = [
  ["profiles", "#configure/llm-profiles", ".profile-section"], ["roles", "#configure/agent-roles", ".settings-role-cards"],
  ["instructions", "#configure/instructions", ".prompt-settings"], ["schema", "#configure/input-schema", ".input-schema-settings"],
  ["signatures", "#configure/signature-explanations", ".signature-catalog"], ["keys", "#connect/api-keys", ".service-api-keys"],
  ["targets", "#connect/vllm-targets", ".internal-egress-settings"], ["api", "#connect/production-api", ".api-document-page"],
  ["runtime", "#operate/runtime", ".runtime-settings-workspace"], ["activity", "#operate/activity", ".activity-workspace"],
  ["deployment", "#operate/activity/deployment", ".deployment-workspace"], ["review", "#promotion/00000008-1111-4111-8111-111111111111", ".v5-promotion"],
];
let page;
try {
  page = await browser.newPage({ viewport: { width: 1440, height: 1000 }, reducedMotion: "reduce" });
  page.setDefaultTimeout(12000); const errors = [], network = []; let screenshots = 0;
  page.on("pageerror", error => errors.push(error.message));
  await page.route("**/*", route => route.request().url().split("#")[0] === "https://fixture.invalid/" ? route.fulfill({ contentType: "text/html", body: '<html lang="ko"><head><meta name="viewport" content="width=device-width,initial-scale=1"></head><body><div id="root"></div></body></html>' }) : (network.push(route.request().url()), route.abort()));
  await page.goto("https://fixture.invalid/");
  await page.addStyleTag({ content: bundle.outputFiles.find(file => file.path.endsWith(".css")).text });
  await page.addScriptTag({ content: bundle.outputFiles.find(file => file.path.endsWith(".js")).text });
  await page.locator(".r3-overview").waitFor();
  const go = async (hash, selector) => { await page.evaluate(value => { location.hash = value; }, hash); await page.locator(selector).first().waitFor(); await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))); };
  const snap = async name => { await page.evaluate(() => scrollTo(0, 0)); await page.screenshot({ path: join(output, name + ".png"), fullPage: true }); screenshots++; assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true, name + " body overflow"); };
  for (const [name, hash, selector] of routes) {
    await go(hash, selector); await snap(name + "-KR-1440");
    assert.equal(await page.locator(".settings-section-header").count(), 0, name + " has no duplicate page hero");
    if (name === "keys") {
      const widths = await page.locator(".settings-summary-item").evaluateAll(nodes => nodes.map(node => node.getBoundingClientRect().width));
      assert.equal(widths.length, 3); assert.ok(Math.max(...widths) - Math.min(...widths) < 2, "key summary cards have equal widths rather than inheriting legacy definition-list columns");
    }
  }
  await go("#configure/agent-roles", ".settings-role-cards");
  assert.equal(await page.locator(".settings-role-cards .settings-role-card").count(), 3);
  assert.equal(await page.locator(".settings-role-cards select").count(), 0, "Production assignments remain read-only");
  assert.ok(await page.locator(".settings-inline-defaults select").count() >= 5, "Test defaults remain editable beside the promotion workflow");
  await page.getByRole("button", { name: "EN", exact: true }).click();
  await snap("roles-EN-1440");
  await page.getByRole("button", { name: /^(테마|Theme)$/ }).click(); await page.getByRole("button", { name: "Dark", exact: true }).click();
  await snap("roles-EN-Dark-1440");
  await go("#connect/api-keys", ".service-api-keys"); await snap("keys-EN-Dark-1440");
  await page.getByRole("button", { name: "KR", exact: true }).click();
  await page.getByRole("button", { name: /^(테마|Theme)$/ }).click(); await page.getByRole("button", { name: "Light", exact: true }).click();
  await page.getByRole("button", { name: "키 발급", exact: true }).click(); await page.getByRole("dialog", { name: "새 API 키 발급", exact: true }).waitFor(); await snap("key-create-KR-1440"); await page.keyboard.press("Escape");
  await go("#connect/vllm-targets", ".internal-egress-settings"); await page.getByRole("button", { name: "내부 대상 추가", exact: true }).click(); await page.getByRole("dialog", { name: "내부 대상 추가", exact: true }).waitFor(); await snap("target-create-KR-1440"); await page.keyboard.press("Escape");
  await go("#configure/instructions", ".prompt-settings");
  const instruction = page.locator(".prompt-editor textarea"); await instruction.waitFor(); await instruction.fill("합성 검증용 수정 지침");
  assert.equal(await instruction.inputValue(), "합성 검증용 수정 지침"); assert.equal(await instruction.evaluate(node => document.activeElement === node), true, "editing must not unexpectedly move focus to the version name");
  assert.equal(await page.getByRole("dialog").count(), 0, "instruction editor is inline");
  assert.ok(await page.locator(".settings-instruction-editor").evaluate(node => node.getBoundingClientRect().right) <= await page.locator(".prompt-history").evaluate(node => node.getBoundingClientRect().left), "instruction editor sits left of version history");
  await snap("instruction-editor-KR-1440"); await page.getByRole("button", { name: "초안 삭제", exact: true }).click(); await page.getByRole("button", { name: "초안 삭제 확인", exact: true }).click();
  await go("#configure/input-schema", ".input-schema-settings"); await page.getByRole("button", { name: /event_id 필드 편집/ }).click(); await page.getByRole("dialog").first().waitFor();
  assert.equal(await page.getByRole("dialog").getByLabel("필드명", { exact: true }).getAttribute("readonly"), "");
  assert.equal(await page.getByRole("dialog").locator("select").first().isDisabled(), true);
  await snap("schema-editor-KR-1440"); await page.keyboard.press("Escape");
  const sample = page.locator(".settings-schema-validation textarea"); await sample.fill('{"event_id":"synthetic-only"}');
  assert.equal(await page.getByRole("button", { name: "샘플 검증", exact: true }).isDisabled(), true, "unsaved schema cannot claim validation against a saved version");
  await page.getByRole("button", { name: "초안 삭제", exact: true }).click(); await page.getByRole("button", { name: "초안 삭제 확인", exact: true }).click();
  assert.equal(await page.getByRole("button", { name: "샘플 검증", exact: true }).isDisabled(), false);
  await go("#operate/activity/deployment", ".deployment-workspace"); assert.equal(await page.getByRole("dialog").count(), 0); await page.getByRole("button", { name: "기술정보", exact: true }).click(); await page.getByRole("dialog").waitFor(); await snap("deployment-technical-KR-1440"); await page.keyboard.press("Escape");
  await page.setViewportSize({ width: 1440, height: 600 });
  for (const [hash, selector] of [["#connect/production-api", ".api-doc-index"]]) {
    await go(hash, selector); await page.evaluate(() => scrollTo(0, 600));
    assert.equal(await page.locator(selector).evaluate(node => node.getBoundingClientRect().top >= document.querySelector(".console-header").getBoundingClientRect().bottom), true, selector + " remains below the sticky top bar");
  }
  await page.setViewportSize({ width: 390, height: 844 });
  for (const [name, hash, selector] of routes) { await go(hash, selector); await snap(name + "-KR-390"); }
  assert.deepEqual(errors, []); assert.deepEqual(network, []); assert.deepEqual(await page.evaluate(() => fixture.unknown), []);
  assert.deepEqual(await page.evaluate(() => fixture.writes), [], "Visual review cannot mutate settings");
  console.log(JSON.stringify({ status: "passed", screenshotCount: screenshots, output, checks: ["12 settings/integration/operation pages", "390px and 1440px", "KR/EN", "SK/Light/Dark", "inline instructions and schema sample validation", "protected field editor", "read-only Production and deployment", "no document overflow"], apiNetworkCalls: 0, llmCalls: 0, writes: 0 }));
} catch (error) { if (page) await page.screenshot({ path: join(output, "failure.png"), fullPage: true }); throw error; }
finally { await browser.close(); }
