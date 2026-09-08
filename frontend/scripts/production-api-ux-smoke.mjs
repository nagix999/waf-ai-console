// Browser-only, fabricated responses. No live API, model, Swagger example,
// document export or schema metadata is read or written by this check.
import assert from "node:assert/strict";
import { existsSync, readdirSync, readFileSync, mkdtempSync } from "node:fs";
import { homedir, tmpdir } from "node:os";
import { join } from "node:path";
import { pathToFileURL } from "node:url";
import { filterApiSections, parseApiDocument } from "../src/apiDocument.js";

const origin = "http://127.0.0.1:15173";
const template = readFileSync(new URL("../../docs/Production_API_v0.1.md", import.meta.url), "utf8");
assert.equal(parseApiDocument(template).sections.length, 12, "real template has 12 sections");
const schema = version => ({ version_number: version, version_id: version === 1 ? "11111111-1111-4111-8111-111111111111" : "22222222-2222-4222-8222-222222222222", content_hash: String(version).repeat(64) });
const reference = (markdown, version) => {
  const inputSchema = schema(version);
  const metadata = `적용 입력 스키마: **v${version}** · \`${inputSchema.version_id}\`\n정의 SHA-256: \`${inputSchema.content_hash}\``;
  return { markdown: markdown.replace("\n\n", `\n\n${metadata}\n\n`), input_schema: inputSchema };
};
const pdfBody = "%PDF-1.4\n% Isolated fake PDF for a browser download event.\n%%EOF\n";
const longTitle = "참고 답안 연결과 평가 · " + "버전이 다른 참고 답안을 안전하게 연결하고 평가 범위를 확인하는 절차 ".repeat(4).trim();
const mobileTemplate = template.replace("## 참고 Label 연결과 평가", `## ${longTitle}`);
const archive = join(homedir(), ".cache/uv/archive-v0");
const modulePath = readdirSync(archive).map(name => join(archive, name, "playwright/driver/package/index.mjs")).find(existsSync);
if (!modulePath) throw new Error("Existing Playwright required");
const { chromium } = await import(pathToFileURL(modulePath).href);
const browser = await chromium.launch({ executablePath: join(homedir(), ".cache/ms-playwright/chromium-1208/chrome-linux64/chrome"), headless: true, args: ["--no-sandbox"] });
const output = mkdtempSync(join(tmpdir(), "waf-production-api-ux-"));
const html = `<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><link rel="icon" href="data:,"></head><body><main id="fixture-root" style="max-width:1260px;margin:20px auto;padding:14px"></main><script type="module">
import RefreshRuntime from '/@react-refresh'; RefreshRuntime.injectIntoGlobalHook(window); window.$RefreshReg$ = () => {}; window.$RefreshSig$ = () => type => type; window.__vite_plugin_react_preamble_installed__ = true;
await import('/src/styles.css');
const {default:React} = await import('/node_modules/.vite/deps/react.js'); const {default:ReactDOM} = await import('/node_modules/.vite/deps/react-dom_client.js');
const {default:ProductionApi} = await import('/src/ProductionApi.jsx');
ReactDOM.createRoot(document.getElementById('fixture-root')).render(React.createElement(ProductionApi));
</script></body></html>`;
const assets = new Set([
  "/@react-refresh", "/@vite/client", "/node_modules/vite/dist/client/env.mjs",
  ...["ProductionApi.jsx", "productionApiDownloads.js", "apiDocument.js", "api.js", "Icon.jsx", "HelpTooltip.jsx", "Dialog.jsx", "styles.css", "productionApi.css", "ux.css", "modelValidation.js", "modelAssignments.js", "analysisView.js", "llmProfiles.js", "internalEgress.js", "testRuns.js", "evaluationMetrics.js"].map(name => "/src/" + name),
]);
const passed = [];
const deferred = () => { let resolve; const promise = new Promise(done => { resolve = done; }); return { promise, resolve }; };
const nav = page => page.getByRole("navigation", { name: "정의서 이전·다음 항목", exact: true });
const toc = page => page.getByRole("navigation", { name: "API 정의서 목차", exact: true });
const section = page => page.locator(".api-doc-content > .api-doc-section");
const plain = text => text.replace(/`([^`]+)`|\*\*([^*]+)\*\*/g, (_, code, strong) => code ?? strong);

async function scenario(name, check, { mobile = false, markdown = template, conflict = false } = {}) {
  const context = await browser.newContext({ viewport: mobile ? { width: 390, height: 844 } : { width: 1440, height: 1000 }, serviceWorkers: "block", acceptDownloads: true });
  const state = { version: 1, pdfRequests: 0, referenceRequests: 0, pdfGate: null };
  const calls = [], violations = [], errors = [];
  await context.addInitScript(() => {
    window.__sectionScrolls = [];
    const scroll = Element.prototype.scrollIntoView;
    Element.prototype.scrollIntoView = function(options) {
      if (this.matches(".api-doc-section h2")) window.__sectionScrolls.push({ title: this.textContent, block: options?.block });
      return scroll.call(this, options);
    };
  });
  // Block Vite HMR sockets as well: this fixture only needs static modules.
  await context.routeWebSocket("**/*", socket => socket.close());
  await context.route("**/*", async route => {
    const request = route.request(), url = new URL(request.url()), path = url.pathname;
    if (url.origin !== origin) { violations.push("external request blocked"); return route.abort(); }
    if (request.method() === "GET" && path === "/production-api-ux-smoke") return route.fulfill({ contentType: "text/html", body: html });
    if (path.startsWith("/api/")) {
      calls.push({ path, method: request.method(), query: Object.fromEntries(url.searchParams), body: request.postData() });
      if (request.method() === "GET" && path === "/api/v1/production-api" && !url.search) {
        state.referenceRequests++;
        return route.fulfill({ contentType: "application/json", body: JSON.stringify(reference(markdown, state.version)) });
      }
      if (request.method() === "GET" && path === "/api/v1/production-api.pdf") {
        state.pdfRequests++;
        assert.deepEqual([...url.searchParams.keys()].sort(), ["expected_schema_hash", "expected_schema_version_id"], "PDF always requests the whole document, never a selected section or search");
        assert.equal(url.searchParams.get("expected_schema_version_id"), schema(state.version).version_id);
        assert.equal(url.searchParams.get("expected_schema_hash"), schema(state.version).content_hash);
        assert.equal(request.postData(), null);
        assert.equal(request.headers().accept, "application/pdf");
        if (state.pdfGate) await state.pdfGate.promise;
        if (conflict && state.pdfRequests === 1) return route.fulfill({ status: 409, contentType: "application/json", body: JSON.stringify({ detail: "input_schema_document_changed" }) });
        return route.fulfill({ contentType: "application/pdf", headers: { "Content-Length": String(Buffer.byteLength(pdfBody)) }, body: pdfBody });
      }
      violations.push("unexpected API request blocked"); return route.abort();
    }
    if (request.method() === "GET" && (assets.has(path) || /^\/node_modules\/\.vite\/deps\/(?:react(?:-dom)?(?:_client|_jsx-dev-runtime)?|chunk-[A-Z0-9]+)\.js$/.test(path))) return route.continue();
    violations.push("unexpected asset blocked: " + path); return route.abort();
  });
  const page = await context.newPage(); page.setDefaultTimeout(10000); page.on("pageerror", error => errors.push(error.message));
  try {
    await page.goto(origin + "/production-api-ux-smoke");
    await page.getByRole("heading", { name: "운영 API 정의서", exact: true }).waitFor();
    await toc(page).getByRole("button", { name: "정의서 안내", exact: true }).waitFor();
    if (mobile) await page.evaluate(() => document.documentElement.dataset.theme = "dark");
    assert.equal(state.referenceRequests, 1); assert.equal(state.pdfRequests, 0, "opening a document must not generate PDF");
    assert.equal(await page.locator(".api-doc-heading .metric-help-trigger").count(), 0, "no header question mark");
    assert.equal(await page.locator(".api-doc-actions .metric-help-trigger").count(), 0, "no obvious download help");
    assert.doesNotMatch(await section(page).innerText(), /11111111-1111-4111-8111-111111111111|1111111111111111111111111111111111111111111111111111111111111111/, "schema IDs and hash remain in technical details");
    await check({ page, state, calls, document: parseApiDocument(markdown) });
    assert.deepEqual(errors, []); assert.deepEqual(violations, []);
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true, "page has no horizontal overflow");
    await page.screenshot({ path: join(output, name + ".png"), fullPage: true });
    passed.push(name); console.log("PASS " + name);
  } catch (error) {
    await page.screenshot({ path: join(output, name + "-failure.png"), fullPage: true });
    console.log(JSON.stringify({ name, output, errors, violations, referenceRequests: state.referenceRequests, pdfRequests: state.pdfRequests }));
    throw error;
  } finally { state.pdfGate?.resolve(); await context.close(); }
}

async function verifySection(page, item, { focus = true } = {}) {
  const body = section(page);
  assert.equal(await body.count(), 1, "exactly one section is displayed");
  assert.equal(await body.locator("h2").textContent(), item.title);
  assert.equal(await toc(page).locator('[aria-current="page"]').count(), 1);
  assert.equal(await toc(page).locator('[aria-current="page"]').textContent(), item.title);
  assert.deepEqual(await body.locator(":scope > p").allTextContents(), item.blocks.filter(block => block.type === "paragraph").map(block => plain(block.text)));
  assert.deepEqual(await body.locator(".api-code pre code").allTextContents(), item.blocks.filter(block => block.type === "code").map(block => block.text), "code examples preserve their original bytes");
  assert.deepEqual(await body.locator(":scope > h3").allTextContents(), item.blocks.filter(block => block.type === "heading").map(block => plain(block.text)));
  assert.deepEqual(await body.locator(":scope > ul > li, :scope > ol > li").allTextContents(), item.blocks.filter(block => block.type === "list").flatMap(block => block.items.map(plain)));
  const actualTables = await body.locator("table").evaluateAll(tables => tables.map(table => ({ headers: [...table.querySelectorAll("thead th")].map(cell => cell.textContent), rows: [...table.querySelectorAll("tbody tr")].map(row => [...row.querySelectorAll("td")].map(cell => cell.textContent)) })));
  assert.deepEqual(actualTables, item.blocks.filter(block => block.type === "table").map(block => ({ headers: block.headers.map(plain), rows: block.rows.map(row => row.map(plain)) })));
  if (focus) {
    await page.waitForFunction(title => document.activeElement?.matches(".api-doc-section h2") && document.activeElement.textContent === title, item.title);
    assert.deepEqual(await page.evaluate(() => window.__sectionScrolls.at(-1)), { title: item.title, block: "start" });
    const heading = await body.locator("h2").boundingBox(); assert.ok(heading.y >= -1 && heading.y < (await page.viewportSize()).height - 40, "heading is brought into view");
  }
}

try {
  await scenario("all-twelve-sections-and-boundaries", async ({ page, state, document }) => {
    assert.equal(await toc(page).getByRole("button").count(), 13);
    assert.equal(await nav(page).getByRole("button", { name: /^이전 항목:/ }).count(), 0);
    for (const [index, item] of document.sections.entries()) {
      await nav(page).getByRole("button", { name: "다음 항목: " + item.title, exact: true }).click();
      await verifySection(page, item);
      assert.equal(await nav(page).getByRole("button", { name: "이전 항목: " + (index ? document.sections[index - 1].title : "정의서 안내"), exact: true }).count(), 1);
      assert.equal(await nav(page).getByRole("button", { name: /^다음 항목:/ }).count(), index === 11 ? 0 : 1);
    }
    for (let index = 10; index >= 0; index--) {
      const item = document.sections[index]; await nav(page).getByRole("button", { name: "이전 항목: " + item.title, exact: true }).click(); await verifySection(page, item);
    }
    await nav(page).getByRole("button", { name: "이전 항목: 정의서 안내", exact: true }).click();
    await page.waitForFunction(() => document.activeElement?.textContent === "정의서 안내" && document.activeElement.tagName === "H2");
    assert.equal(await section(page).count(), 1); assert.equal(await nav(page).getByRole("button", { name: /^이전 항목:/ }).count(), 0);
    assert.equal(state.referenceRequests, 1); assert.equal(state.pdfRequests, 0);
  });

  await scenario("filtered-navigation-and-no-results", async ({ page, state, document }) => {
    const matches = filterApiSections(document.sections, "source_system"); assert.ok(matches.length > 1 && matches.length < 12);
    await page.getByRole("searchbox", { name: "정의서 검색", exact: true }).fill("source_system");
    assert.equal(await toc(page).getByRole("button", { name: "정의서 안내", exact: true }).count(), 0);
    assert.equal(await toc(page).getByRole("button").count(), matches.length);
    await verifySection(page, matches[0], { focus: false });
    assert.equal(await nav(page).getByRole("button", { name: /^이전 항목:/ }).count(), 0);
    for (const item of matches.slice(1)) { await nav(page).getByRole("button", { name: "다음 항목: " + item.title, exact: true }).click(); await verifySection(page, item); }
    assert.equal(await nav(page).getByRole("button", { name: /^다음 항목:/ }).count(), 0);
    await page.getByRole("searchbox", { name: "정의서 검색", exact: true }).fill("no-fixture-section-can-match-this");
    assert.equal(await nav(page).count(), 0); assert.equal(await section(page).count(), 0);
    await page.getByText("검색어에 해당하는 항목이 없습니다.", { exact: true }).waitFor();
    await page.getByRole("button", { name: "검색 초기화", exact: true }).click();
    assert.equal(await toc(page).getByRole("button").count(), 13); assert.equal(state.pdfRequests, 0);
  });

  await scenario("pdf-whole-document-explicit-single-request", async ({ page, state, calls }) => {
    await page.getByRole("searchbox", { name: "정의서 검색", exact: true }).fill("wait_seconds");
    assert.equal(state.pdfRequests, 0, "search does not request PDF");
    state.pdfGate = deferred();
    const downloaded = page.waitForEvent("download");
    const requested = page.waitForRequest(request => new URL(request.url()).pathname === "/api/v1/production-api.pdf");
    await page.getByRole("button", { name: "PDF 다운로드", exact: true }).evaluate(button => { button.click(); button.click(); });
    await page.getByRole("button", { name: "PDF 작성 중…", exact: true }).waitFor();
    await requested;
    assert.equal(state.pdfRequests, 1, "double click is single flight");
    assert.equal(await page.getByRole("button", { name: "PDF 작성 중…", exact: true }).isDisabled(), true);
    assert.equal(await page.getByRole("button", { name: "최신 정의서 새로고침", exact: true }).isDisabled(), true);
    state.pdfGate.resolve();
    const download = await downloaded;
    assert.equal(download.suggestedFilename(), "Production_API_v0.2.0_schema-v1.pdf");
    await page.getByRole("button", { name: "PDF 다운로드", exact: true }).waitFor();
    assert.equal(state.pdfRequests, 1); assert.equal(calls.filter(call => call.path.endsWith(".pdf")).length, 1);
    assert.ok(calls.every(call => call.method === "GET" && call.body === null));
  });

  await scenario("pdf-schema-conflict-refresh-and-retry", async ({ page, state, calls }) => {
    await page.getByRole("button", { name: "PDF 다운로드", exact: true }).click();
    await page.getByRole("alert").waitFor(); assert.match(await page.getByRole("alert").innerText(), /입력 스키마가 변경됐습니다.*새로고침/);
    assert.equal(state.pdfRequests, 1, "conflict must not retry automatically"); assert.equal(state.referenceRequests, 1);
    state.version = 2; await page.getByRole("button", { name: "최신 정의서 새로고침", exact: true }).click();
    await page.locator(".api-source-note").filter({ hasText: "입력 스키마 v2" }).waitFor();
    assert.equal(state.referenceRequests, 2); assert.equal(state.pdfRequests, 1); assert.equal(await page.getByRole("alert").count(), 0);
    const downloaded = page.waitForEvent("download"); await page.getByRole("button", { name: "PDF 다운로드", exact: true }).click();
    assert.equal((await downloaded).suggestedFilename(), "Production_API_v0.2.0_schema-v2.pdf");
    const exports = calls.filter(call => call.path.endsWith(".pdf")); assert.equal(exports.length, 2);
    assert.notEqual(exports[0].query.expected_schema_version_id, exports[1].query.expected_schema_version_id);
    assert.notEqual(exports[0].query.expected_schema_hash, exports[1].query.expected_schema_hash);
  }, { conflict: true });

  await scenario("mobile-dark-keyboard-long-title-and-plain-warning", async ({ page, state, document }) => {
    assert.equal(await page.evaluate(() => document.documentElement.dataset.theme), "dark");
    assert.equal(await page.locator(".metric-help-trigger").count(), 0, "short explanations are plain text rather than question marks");
    assert.match(await page.locator(".api-doc-notice").innerText(), /로그인 중에는 API Key보다 관리자 권한이 우선/);
    assert.match(await page.locator(".api-doc-notice").innerText(), /Try it out은 실제 API를 호출/);
    assert.equal(await page.getByRole("tooltip").count(), 0);
    const beforeLong = document.sections[9]; await toc(page).getByRole("button", { name: beforeLong.title, exact: true }).focus(); await page.keyboard.press("Enter"); await verifySection(page, beforeLong);
    const longNext = nav(page).getByRole("button", { name: "다음 항목: " + longTitle, exact: true }); await longNext.focus();
    const longBounds = await longNext.boundingBox(); assert.ok(longBounds.x >= 0 && longBounds.x + longBounds.width <= 390, "long title navigation stays inside mobile viewport");
    assert.equal(await longNext.evaluate(button => button.scrollWidth <= button.clientWidth), true, "long navigation title wraps without clipping");
    await nav(page).screenshot({ path: join(output, "mobile-long-navigation.png") });
    await page.keyboard.press("Enter"); await verifySection(page, document.sections[10]);
    for (const button of await nav(page).getByRole("button").all()) { const bounds = await button.boundingBox(); assert.ok(bounds.x >= 0 && bounds.x + bounds.width <= 390, "long navigation labels fit mobile width"); }
    assert.equal(await nav(page).locator(".metric-help-trigger").count(), 0);
    await toc(page).getByRole("button", { name: "정의서 안내", exact: true }).focus(); await page.keyboard.press("Enter");
    await page.waitForFunction(() => document.activeElement?.tagName === "H2" && document.activeElement.textContent === "정의서 안내");
    assert.equal(state.referenceRequests, 1); assert.equal(state.pdfRequests, 0);
  }, { mobile: true, markdown: mobileTemplate });

  console.log(JSON.stringify({ passed, output, sectionsChecked: 12, actualApiCalls: 0, llmCalls: 0, savedRealDocumentFiles: 0 }));
} finally { await browser.close(); }
