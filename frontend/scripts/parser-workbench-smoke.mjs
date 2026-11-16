// Actual React/CSS, synthetic offline API only. No model or database calls.
import assert from "node:assert/strict";
import { existsSync, readdirSync, mkdtempSync } from "node:fs";
import { homedir, tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { build } from "esbuild";
import { parserWorkbenchFixture } from "./parser-workbench-fixture.mjs";

const output = mkdtempSync(join(tmpdir(), "waf-parser-workbench-"));
const bundle = await build({ stdin: { contents: `import {createRoot} from 'react-dom/client';import App from './App.jsx';import './styles.css';import './console.css';import './lifecycle.css';import './r3.css';createRoot(document.getElementById('root')).render(<App/>);`, loader: "jsx", resolveDir: fileURLToPath(new URL("../src", import.meta.url)) },
  plugins: [{ name: "offline-api", setup(builder) { builder.onLoad({ filter: /\/api\.js$/ }, () => ({ contents: parserWorkbenchFixture(), loader: "js" })); } }], bundle: true, write: false, outfile: join(output, "bundle.js"), platform: "browser", format: "iife", jsx: "automatic", define: { "process.env.NODE_ENV": '"production"' }, loader: { ".md": "text", ".woff2": "dataurl" }, logLevel: "silent" });
const archive = join(homedir(), ".cache/uv/archive-v0");
const driver = process.env.PLAYWRIGHT_MODULE || readdirSync(archive).map(name => join(archive, name, "playwright/driver/package/index.mjs")).find(existsSync);
const { chromium } = await import(pathToFileURL(driver).href);
const browser = await chromium.launch({ executablePath: process.env.CHROMIUM_PATH || join(homedir(), ".cache/ms-playwright/chromium-1208/chrome-linux64/chrome"), headless: true, args: ["--no-sandbox"] });
let page;
try {
  page = await browser.newPage({ viewport: { width: 1440, height: 1000 }, reducedMotion: "reduce" });
  page.setDefaultTimeout(10000); const errors = [], network = []; let screenshots = 0;
  page.on("pageerror", error => errors.push(error.message));
  await page.route("**/*", route => route.request().url().split("#")[0] === "https://fixture.invalid/" ? route.fulfill({ contentType: "text/html", body: '<html lang="ko"><head><meta name="viewport" content="width=device-width,initial-scale=1"></head><body><div id="root"></div></body></html>' }) : (network.push(route.request().url()), route.abort()));
  await page.goto("https://fixture.invalid/");
  await page.addStyleTag({ content: bundle.outputFiles.find(file => file.path.endsWith(".css")).text });
  await page.addScriptTag({ content: bundle.outputFiles.find(file => file.path.endsWith(".js")).text });
  await page.locator(".r3-overview").waitFor();
  const go = hash => page.evaluate(value => { location.hash = value; }, hash);
  const snap = async name => {
    await page.evaluate(() => window.scrollTo(0, 0));
    await page.screenshot({ path: join(output, name + ".png"), fullPage: true }); screenshots++;
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true, name + " body overflow");
    const overflows = await page.evaluate(() => [...document.querySelectorAll(".parser-workbench *")].filter(node => { const box = node.getBoundingClientRect(); return box.width && box.right > innerWidth + 2 && !node.closest(".table-wrap,.ux-dialog"); }).map(node => ({ tag: node.tagName, className: String(node.className), right: node.getBoundingClientRect().right })).slice(0, 8));
    assert.deepEqual(overflows, [], name + " layout overflow");
  };
  await go("#configure/parsers"); await page.getByRole("button", { name: "규칙 만들기", exact: true }).waitFor();
  for (const theme of ["SK", "Light", "Dark"]) {
    await page.getByRole("button", { name: /^(테마|Theme)$/ }).click(); await page.getByRole("button", { name: theme, exact: true }).click();
    for (const locale of ["KR", "EN"]) { await page.getByRole("button", { name: locale, exact: true }).click(); await snap(`catalog-${locale}-${theme}`); }
  }
  await page.getByRole("button", { name: "KR", exact: true }).click();
  const create = page.getByRole("button", { name: "규칙 만들기", exact: true }); await create.focus(); await page.keyboard.press("Enter");
  await page.locator(".pw-input-panel").waitFor(); assert.equal(new URL(page.url()).hash, "#configure/parsers/new/data");
  await page.getByRole("textbox", { name: "작업 이름", exact: true }).fill("Vendor A 로그 형식 개선");
  await page.getByRole("combobox", { name: "평가 데이터셋", exact: true }).selectOption("00000620-1111-4111-8111-111111111111");
  await page.getByRole("combobox", { name: "고정 버전", exact: true }).selectOption("00000621-1111-4111-8111-111111111111");
  const sample = page.getByRole("checkbox", { name: "표식 요청 대표 선택", exact: true });
  await sample.uncheck(); assert.equal(await sample.isChecked(), false);
  await sample.check(); assert.equal(await sample.isChecked(), true);
  await snap("choose-inputs-KR");
  await page.getByRole("button", { name: "입력 고정 후 계속", exact: true }).click();
  await page.getByRole("button", { name: "LLM으로 형식 찾기", exact: true }).waitFor();
  const requestModel = async button => {
    await button.click(); const dialog = page.getByRole("dialog").filter({ has: page.getByRole("combobox", { name: "검증된 내부 vLLM", exact: true }) });
    await dialog.getByRole("combobox").selectOption("00000001-1111-4111-8111-111111111111");
    const request = dialog.getByRole("button", { name: "확인 후 요청", exact: true }); assert.equal(await request.isDisabled(), true);
    await dialog.getByRole("checkbox").check(); await request.click(); await dialog.waitFor({ state: "hidden" });
  };
  await requestModel(page.getByRole("button", { name: "LLM으로 형식 찾기", exact: true }));
  await page.locator(".pw-group-card").nth(1).waitFor(); await snap("format-groups-KR");
  assert.equal(await page.getByRole("button", { name: "형식별 규칙 생성", exact: true }).isDisabled(), true);
  await page.getByRole("button", { name: "이 형식 구성을 확인함", exact: true }).click();
  await requestModel(page.getByRole("button", { name: "형식별 규칙 생성", exact: true }));
  await page.getByRole("button", { name: /^원문 대조 \/ 검증으로 계속/ }).waitFor();
  await page.locator(".pw-group-card").first().getByRole("button", { name: "형식과 규칙 상세", exact: true }).click();
  let dialog = page.getByRole("dialog", { name: "형식 상세", exact: true }); await dialog.getByRole("button", { name: "고급 편집", exact: true }).click();
  dialog = page.getByRole("dialog", { name: "규칙 고급 편집", exact: true }); await dialog.getByRole("textbox", { name: "규칙 이름", exact: true }).fill("표식 요청 추출 초안 수정");
  await snap("advanced-rule-editor-KR"); await dialog.getByRole("button", { name: "수정 초안 반영", exact: true }).click();
  await page.getByRole("button", { name: "이 형식 구성을 확인함", exact: true }).click();
  await page.getByRole("button", { name: /^원문 대조 \/ 검증으로 계속/ }).click();
  await page.locator(".pw-case-review").waitFor();
  await page.getByRole("combobox", { name: "문항 구분", exact: true }).selectOption("holdout");
  assert.match(await page.locator(".pw-raw-source").innerText(), /규칙을 고정한 뒤/);
  assert.equal(await page.getByRole("button", { name: "기대값 확인 / 수정", exact: true }).isDisabled(), true);
  await snap("validation-before-lock-KR");
  await page.getByRole("button", { name: "규칙 고정 후 검증용 원문 열기", exact: true }).click();
  await page.getByRole("button", { name: "현재 규칙 검증", exact: true }).waitFor();
  await page.getByRole("button", { name: "메서드", exact: true }).click();
  assert.equal(await page.locator(".pw-raw-source mark").innerText(), "GET");
  assert.equal(await page.locator(".pw-raw-source script").count(), 0); await snap("verified-source-span-KR");
  for (let caseNumber = 3; caseNumber <= 4; caseNumber++) {
    await page.getByRole("combobox", { name: "문항", exact: true }).selectOption(`0000065${caseNumber}-1111-4111-8111-111111111111`);
    await page.getByRole("button", { name: "기대값 확인 / 수정", exact: true }).click();
    dialog = page.getByRole("dialog", { name: "기대 추출값 확인", exact: true });
    for (const index of [0, 1]) {
      const field = dialog.locator(".pw-expectation-field").nth(index); await field.getByRole("button", { name: "추출값으로 초안 채우기", exact: true }).click();
      assert.equal(await field.getByRole("combobox").inputValue(), "unknown"); await field.getByRole("combobox").selectOption("present");
    }
    if (caseNumber === 4) {
      const headers = dialog.locator(".pw-expectation-field").nth(3); await headers.getByRole("button", { name: "추출값으로 초안 채우기", exact: true }).click();
      assert.equal(await headers.locator(".pw-pair-row").count(), 2); await headers.getByRole("combobox").selectOption("present");
      await headers.getByRole("button", { name: "항목 추가", exact: true }).click(); assert.equal(await headers.locator(".pw-pair-row").count(), 3);
      await headers.getByRole("button", { name: "항목 3 삭제", exact: true }).click(); await snap("duplicate-header-expectations-KR");
    }
    await dialog.getByRole("button", { name: "확인한 기대값 저장", exact: true }).click(); await dialog.waitFor({ state: "hidden" });
  }
  await page.getByRole("button", { name: "현재 규칙 검증", exact: true }).click();
  await page.getByRole("textbox", { name: "버전 이름", exact: true }).fill("Vendor A 검토한 규칙");
  const save = page.getByRole("button", { name: "후보 버전 저장", exact: true }); await save.waitFor(); assert.equal(await save.isDisabled(), false); await snap("validated-candidate-KR");
  await save.click(); await page.getByRole("link", { name: "공식 테스트 만들기", exact: true }).waitFor();
  await page.goBack(); await page.locator(".pw-groups-grid").waitFor(); assert.equal(await page.getByRole("button", { name: "형식별 규칙 생성", exact: true }).isDisabled(), true);
  await page.goForward(); await page.locator(".pw-case-review").waitFor();
  await page.setViewportSize({ width: 390, height: 844 }); await snap("review-mobile-KR");
  await page.getByRole("button", { name: "EN", exact: true }).click(); await snap("review-mobile-EN");
  assert.deepEqual(errors, []); assert.deepEqual(network, []);
  assert.equal(await page.evaluate(() => fixture.workbench.writes.some(call => /activate|promote/.test(call.path))), false);
  assert.equal(await page.evaluate(() => fixture.workbench.writes.filter(call => call.path.endsWith("/save-candidate")).length), 1);
  assert.deepEqual(await page.evaluate(() => fixture.workbench.writes.find(call => call.path.endsWith("/sessions")).payload.source.case_ids.slice().sort()), ["00000651-1111-4111-8111-111111111111", "00000652-1111-4111-8111-111111111111", "00000653-1111-4111-8111-111111111111", "00000654-1111-4111-8111-111111111111"]);
  assert.ok(await page.evaluate(() => fixture.workbench.writes.filter(call => /discover|generate|groups|validate|expectations|save-candidate/.test(call.path)).every(call => Number.isInteger(call.payload.expected_revision))));
  console.log(JSON.stringify({ output, screenshots, errors, externalTraffic: network.length, checks: "SK/Light/Dark, KR/EN, desktop/mobile, actual hash Back/Forward, keyboard, explicit LLM consent, group confirmation, advanced draft, locked holdout, codepoint source links, repeated header rows, unconfirmed copy, expectations and candidate-only save" }));
} catch (error) {
  await page?.screenshot({ path: join(output, "failure.png"), fullPage: true });
  console.error(JSON.stringify({ output, fixture: await page?.evaluate(() => fixture.workbench), text: await page?.locator("body").innerText() }));
  throw error;
} finally { await browser.close(); }
