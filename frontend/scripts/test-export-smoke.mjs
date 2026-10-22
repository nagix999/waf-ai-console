// Real export modal, synthetic intercepted APIs; no live service/model traffic.
import assert from "node:assert/strict";
import { existsSync, readdirSync, mkdirSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { build } from "esbuild";

const output = fileURLToPath(new URL("../../test-results/test-export", import.meta.url));
mkdirSync(output, { recursive: true });
const id = "11111111-1111-4111-8111-111111111111";
const fields = [["row_number", "문항 번호", "case"], ["case_name", "문항명", "case"], ["event_id", "이벤트 ID", "input"],
  ["payload", "HTTP 원문", "input"], ["signature", "시그니처", "input"], ["extra_fields.vendor_data", "추가 입력: vendor_data", "input"],
  ["verdict", "AI 최종 판정", "result"], ["summary_ko", "분석 요약", "result"], ["evidence_true_positive", "주요 정탐 근거", "result"],
  ["evidence_false_positive", "주요 오탐 근거", "result"], ["evidence_context", "주요 참고 내용", "result"],
  ["expected_verdict", "접수 당시 기대 판정", "evaluation"], ["reference_verdict", "평가 기준 답안", "evaluation"],
  ["initial_verdict", "외부 1차 판정", "evaluation"], ["analysis_id", "분석 ID", "execution"]]
  .map(([name, label_ko, group]) => ({ name, label_ko, group, description: "형식을 설명하는 예시입니다.", example: "sample" }));
const source = `import {createRoot} from 'react-dom/client';import {useState} from 'react';import TestExport from './TestExport.jsx';import {ConsolePreferencesProvider,useConsolePreferences} from './consolePreferences.jsx';import './styles.css';import './console.css';import './lifecycle.css';import './r3.css';import './testStatistics.css';
function Harness(){const prefs=useConsolePreferences();const [filters,setFilters]=useState({difficulty:'hard',cell:'fn',limit:1,offset:50,evaluation_id:'latest'});window.fixture={setTheme:prefs.setTheme,setLocale:prefs.setLocale,change:patch=>setFilters(old=>({...old,...patch}))};return <main style={{padding:32}}><h1>Excel 다운로드 검증</h1><TestExport id='${id}' filters={filters}/></main>;}createRoot(document.getElementById('root')).render(<ConsolePreferencesProvider><Harness/></ConsolePreferencesProvider>);`;
const bundle = await build({ stdin: { contents: source, loader: "jsx", resolveDir: fileURLToPath(new URL("../src", import.meta.url)) },
  bundle: true, write: false, outfile: join(output, "bundle.js"), platform: "browser", format: "iife", jsx: "automatic",
  define: { "process.env.NODE_ENV": '"production"' }, loader: { ".md": "text", ".woff2": "dataurl" }, logLevel: "silent" });
const archive = join(homedir(), ".cache/uv/archive-v0");
const driver = readdirSync(archive).map(name => join(archive, name, "playwright/driver/package/index.mjs")).find(existsSync);
const { chromium } = await import(pathToFileURL(driver).href);
const browser = await chromium.launch({ executablePath: join(homedir(), ".cache/ms-playwright/chromium-1208/chrome-linux64/chrome"), headless: true, args: ["--no-sandbox"] });
let page;
try {
  page = await browser.newPage({ viewport: { width: 1440, height: 1000 }, reducedMotion: "reduce", acceptDownloads: true });
  const errors = [], blocked = [], previews = [], exports = []; let count = 73, version = "a", conflict = false, previewFailure = false, screenshots = 0;
  page.on("pageerror", error => errors.push(error.message));
  await page.route("**/*", route => {
    const url = new URL(route.request().url());
    if (url.href === "https://fixture.invalid/") return route.fulfill({ contentType: "text/html", body: '<html><head><meta name="viewport" content="width=device-width,initial-scale=1"></head><body><div id="root"></div></body></html>' });
    const path = `/api/v1/test-runs/${id}/`;
    if (url.origin === "https://fixture.invalid" && url.pathname.startsWith(path)) {
      assert.equal(url.search, ""); assert.equal(route.request().method(), "POST");
      const body = route.request().postDataJSON();
      assert.equal(body.filters.limit, undefined); assert.equal(body.filters.offset, undefined);
      if (url.pathname === path + "export/preview") {
        previews.push(body);
        if (previewFailure) return route.fulfill({ status: 503, contentType: "application/json", body: '{"detail":"PRIVATE-SYNTHETIC-ERROR"}' });
        return route.fulfill({ contentType: "application/json", body: JSON.stringify({ count, scope: version.repeat(64), columns: fields }) });
      }
      if (url.pathname === path + "export.xlsx") {
        exports.push(body);
        if (conflict) { conflict = false; return route.fulfill({ status: 409, contentType: "application/json", body: '{"detail":"test_export_selection_changed"}' }); }
        assert.equal(body.expected_count, count); assert.equal(body.preview_scope, version.repeat(64));
        return route.fulfill({ contentType: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", body: "offline workbook fixture" });
      }
    }
    blocked.push(url.href); return route.abort();
  });
  await page.goto("https://fixture.invalid/");
  await page.addStyleTag({ content: bundle.outputFiles.find(file => file.path.endsWith(".css")).text });
  await page.addScriptTag({ content: bundle.outputFiles.find(file => file.path.endsWith(".js")).text });
  const dialog = page.getByRole("dialog"), open = page.getByRole("button", { name: /^(Excel 다운로드|Download Excel)$/ });
  const download = () => dialog.getByRole("button", { name: /^(다운로드|Download)$/ });
  const snap = async name => { assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true); await page.screenshot({ path: join(output, name + ".png"), fullPage: true }); screenshots++; };
  await open.focus(); await page.keyboard.press("Enter"); await dialog.getByText("73건 다운로드", { exact: true }).waitFor();
  assert.equal(exports.length, 0); assert.equal(previews[0].filters.evaluation_outcome, "false_negative");
  assert.equal(previews[0].filters.reference_verdict, "true_positive"); assert.equal(previews[0].filters.verdict, "false_positive");
  for (const theme of ["sk", "light", "dark"]) for (const locale of ["ko", "en"]) {
    await page.evaluate(({ theme, locale }) => { fixture.setTheme(theme); fixture.setLocale(locale); }, { theme, locale });
    await snap(`${theme}-${locale}`);
  }
  await page.evaluate(() => fixture.setLocale("ko"));
  await dialog.getByRole("button", { name: "모두 해제", exact: true }).click(); assert.equal(await download().isDisabled(), true);
  await dialog.getByRole("checkbox", { name: "이벤트 ID event_id", exact: true }).check();
  const beforePage = previews.length; await page.evaluate(() => fixture.change({ offset: 100 }));
  assert.equal(await dialog.isVisible(), true); assert.equal(previews.length, beforePage);
  const event = page.waitForEvent("download"); await download().click(); assert.equal((await event).suggestedFilename(), `WAF-Test-${id}.xlsx`);
  assert.deepEqual(exports[0].columns, ["event_id"]); await dialog.waitFor({ state: "hidden" });
  await open.click(); await dialog.getByText("73건 다운로드", { exact: true }).waitFor();
  assert.equal(await dialog.getByRole("checkbox", { name: "HTTP 원문 payload", exact: true }).isChecked(), false);
  conflict = true; await download().click(); await dialog.getByRole("alert").waitFor(); assert.equal(await download().isDisabled(), true);
  await snap("scope-changed"); count = 74; version = "b";
  await dialog.getByRole("button", { name: "대상 다시 확인", exact: true }).click(); await dialog.getByText("74건 다운로드", { exact: true }).waitFor();
  assert.equal(await download().isDisabled(), false);
  await dialog.getByRole("searchbox", { name: "필드 검색", exact: true }).fill("verdict"); assert.equal(await dialog.getByRole("checkbox").count(), 4);
  await dialog.getByRole("searchbox", { name: "필드 검색", exact: true }).fill("");
  await page.setViewportSize({ width: 390, height: 844 }); await snap("mobile-dark");
  await page.keyboard.press("Escape"); await dialog.waitFor({ state: "hidden" }); assert.equal(await open.evaluate(element => element === document.activeElement), true);
  await page.setViewportSize({ width: 1440, height: 1000 });
  count = 0; await open.click(); await dialog.getByText("0건 다운로드", { exact: true }).waitFor(); assert.equal(await download().isDisabled(), true); await snap("zero-cases");
  await page.keyboard.press("Escape"); previewFailure = true; await open.click(); await dialog.getByRole("alert").waitFor();
  assert.doesNotMatch(await dialog.innerText(), /PRIVATE-SYNTHETIC/); assert.equal(await download().isDisabled(), true); await snap("safe-error");
  previewFailure = false; count = 74;
  await dialog.getByRole("button", { name: "대상 다시 확인", exact: true }).click(); await dialog.getByText("74건 다운로드", { exact: true }).waitFor();
  assert.equal(await dialog.getByRole("checkbox", { name: "HTTP 원문 payload", exact: true }).isChecked(), false, "refresh errors never reselect excluded HTTP");
  await page.evaluate(() => fixture.change({ difficulty: "easy" })); await dialog.waitFor({ state: "hidden" });
  assert.deepEqual(errors, []); assert.deepEqual(blocked, []);
  console.log(JSON.stringify({ screenshots, checks: "KR/EN, SK/Light/Dark, mobile, keyboard/focus/Escape, count across pages, field selection/search, confirmed scope, stale/error/zero states, preserved exclusions, filter invalidation", realApiCalls: 0, externalTraffic: 0 }));
} catch (error) { if (page) await page.screenshot({ path: join(output, "failure.png"), fullPage: true }); throw error; }
finally { await browser.close(); }
