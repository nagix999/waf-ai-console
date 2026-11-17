// Repeatable screenshots and keyboard/workflow checks. All records are synthetic,
// every API is in-memory, and every other network destination is blocked.
import assert from "node:assert/strict";
import { existsSync, readdirSync, mkdtempSync } from "node:fs";
import { homedir, tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { build } from "esbuild";
import { r5BrowserFixture } from "./r5-browser-fixture.mjs";

const output = mkdtempSync(join(tmpdir(), "waf-signature-explanations-"));
const mock = r5BrowserFixture().replace("export const api=", `
const signatureVersions=[{id:id(901),version_no:1,name:'벤더별 탐지 설명',change_reason:'초기 참고 설명',parent_id:null,fingerprint:hash,entry_count:31,created_by:'fixture-admin',created_at:date,entries:Array.from({length:31},(_,n)=>({waf_vendor:n%2?'Example WAF B':'Example WAF A',signature:'Example detection '+(n+1),signature_explanation:'요청 구조와 탐지 대상의 관계를 해석하기 위한 가상 참고 설명 '+(n+1)+'. 실제 요청 원문에서 해당 동작이 확인되는지 구분해야 합니다. 설명 자체가 개별 요청의 정답을 뜻하지 않습니다.',reference_source:n===0?'가상 벤더 문서 <script>never execute</script>':''}))}];
const signatureSummary=({entries,...version})=>version;
Object.assign(methods,{
 signatureExplanations:async(query={})=>({items:signatureVersions.slice(Number(query.offset||0),Number(query.offset||0)+Number(query.limit||25)).map(signatureSummary),active_version_id:id(901),revision:1,total:signatureVersions.length,limit:query.limit||25,offset:query.offset||0}),
 signatureExplanation:async which=>structuredClone(signatureVersions.find(version=>version.id===which)),
 createSignatureExplanation:async payload=>{state.writes.push({name:'createSignatureExplanation',payload});const version={...structuredClone(payload),id:id(901+signatureVersions.length),version_no:signatureVersions.length+1,fingerprint:hash,entry_count:payload.entries.length,created_by:'fixture-admin',created_at:date};signatureVersions.unshift(version);return structuredClone(version)},
 matchSignatureExplanation:async payload=>{state.writes.push({name:'matchSignatureExplanation',payload});const version=signatureVersions.find(value=>value.id===payload.version_id);const entry=version.entries.find(value=>value.waf_vendor.trim().toLowerCase()===payload.waf_vendor.trim().toLowerCase()&&value.signature===payload.signature.trim());return {matched:!!entry,entry:entry||null,version:signatureSummary(version)}},
});
state.signatureVersions=signatureVersions;
export const api=`);
const bundle = await build({ stdin: { contents: `import {createRoot} from 'react-dom/client';import App from './App.jsx';import './styles.css';import './console.css';import './lifecycle.css';import './r3.css';createRoot(document.getElementById('root')).render(<App/>);`, loader: "jsx", resolveDir: fileURLToPath(new URL("../src", import.meta.url)) },
  plugins: [{ name: "offline-api", setup(builder) { builder.onLoad({ filter: /\/api\.js$/ }, () => ({ contents: mock, loader: "js" })); } }],
  bundle: true, write: false, outfile: join(output, "bundle.js"), platform: "browser", format: "iife", jsx: "automatic", define: { "process.env.NODE_ENV": '"production"' }, loader: { ".md": "text", ".woff2": "dataurl" }, logLevel: "silent" });
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
  };
  await go("#configure/signature-explanations");
  await page.getByRole("button", { name: "복제해 수정", exact: true }).waitFor();
  for (const theme of ["SK", "Light", "Dark"]) {
    await page.getByRole("button", { name: /^(테마|Theme)$/ }).click(); await page.getByRole("button", { name: theme, exact: true }).click();
    for (const locale of ["KR", "EN"]) {
      await page.getByRole("button", { name: locale, exact: true }).click();
      assert.equal(await page.locator("aside [aria-current=page]").innerText(), locale === "KR" ? "시그니처 설명" : "Signature Explanations");
      await snap(`catalog-${locale}-${theme}`);
    }
  }
  await page.getByRole("button", { name: "KR", exact: true }).click();
  const pagination = page.getByRole("navigation", { name: "설명 목록", exact: true });
  await pagination.getByRole("button", { name: "마지막 페이지", exact: true }).click();
  await page.getByRole("button", { name: "Example detection 31", exact: true }).waitFor();
  await pagination.getByRole("button", { name: "첫 페이지", exact: true }).click();
  const detailTrigger = page.getByRole("button", { name: "Example detection 1", exact: true });
  await detailTrigger.focus(); await page.keyboard.press("Enter");
  let dialog = page.getByRole("dialog", { name: "시그니처 설명", exact: true });
  await dialog.waitFor(); assert.equal(await dialog.locator("script").count(), 0); assert.match(await dialog.innerText(), /<script>never execute<\/script>/);
  await snap("entry-detail-KR"); await page.keyboard.press("Escape"); await dialog.waitFor({ state: "hidden" });
  assert.equal(await detailTrigger.evaluate(node => document.activeElement === node), true);
  await page.getByRole("button", { name: "매칭 확인", exact: true }).click();
  dialog = page.getByRole("dialog", { name: "매칭 확인", exact: true });
  await dialog.getByRole("textbox", { name: "벤더", exact: true }).fill("example waf a");
  await dialog.getByRole("textbox", { name: "시그니처", exact: true }).fill("Example detection 1");
  await dialog.getByRole("button", { name: "매칭 확인", exact: true }).click();
  await dialog.getByText("일치하는 설명이 있습니다.", { exact: true }).waitFor(); await snap("match-found-KR");
  await dialog.getByRole("textbox", { name: "시그니처", exact: true }).fill("Not registered");
  await dialog.getByRole("button", { name: "매칭 확인", exact: true }).click();
  await dialog.getByText("일치하는 설명이 없습니다. 설명 없이 분석합니다.", { exact: true }).waitFor();
  await page.keyboard.press("Escape");
  await page.getByRole("button", { name: "복제해 수정", exact: true }).click();
  await page.getByRole("textbox", { name: "설명집 이름", exact: true }).fill("검토용 설명집");
  await page.getByRole("textbox", { name: "변경 사유", exact: true }).fill("가상 참고 설명 추가와 정리");
  await page.getByRole("button", { name: "설명 추가", exact: true }).click();
  dialog = page.getByRole("dialog", { name: "설명 추가", exact: true });
  assert.equal(await dialog.getByRole("textbox", { name: "벤더", exact: true }).evaluate(node => node === document.activeElement), true);
  await dialog.getByRole("textbox", { name: "벤더", exact: true }).fill("Example WAF A");
  await dialog.getByRole("textbox", { name: "시그니처", exact: true }).fill("Example detection 1");
  await dialog.getByRole("textbox", { name: /^시그니처 설명/ }).fill("중복 검사용 가상 설명");
  await dialog.getByRole("button", { name: "변경안에 반영", exact: true }).click();
  await dialog.getByRole("alert").filter({ hasText: "이미 있습니다" }).waitFor();
  await dialog.getByRole("textbox", { name: "시그니처", exact: true }).fill("New synthetic detection");
  await dialog.getByRole("textbox", { name: /^시그니처 설명/ }).fill("탐지 대상과 요청 구조를 대조하는 가상 설명입니다. 개별 요청의 정답은 원문을 분석해 판단합니다.");
  await snap("entry-editor-KR");
  await dialog.getByRole("button", { name: "변경안에 반영", exact: true }).click(); await dialog.waitFor({ state: "hidden" });
  await page.getByRole("searchbox", { name: "검색", exact: true }).fill("Example detection 2");
  await page.getByRole("button", { name: "Example detection 2", exact: true }).click();
  dialog = page.getByRole("dialog", { name: "설명 수정", exact: true });
  await dialog.getByRole("button", { name: "변경안에서 제외", exact: true }).click(); await dialog.waitFor({ state: "hidden" });
  await page.getByRole("searchbox", { name: "검색", exact: true }).fill("");
  await snap("collection-draft-KR");
  await page.getByRole("button", { name: "새 버전 저장", exact: true }).click();
  await page.getByText("새 버전을 저장했습니다. 운영 설정은 변경되지 않았습니다.", { exact: true }).waitFor();
  const saved = await page.evaluate(() => fixture.writes.find(call => call.name === "createSignatureExplanation").payload);
  assert.equal(saved.parent_id, "00000901-1111-4111-8111-111111111111");
  assert.equal(saved.entries.length, 31); assert.ok(saved.entries.some(entry => entry.signature === "New synthetic detection"));
  assert.ok(!saved.entries.some(entry => entry.signature === "Example detection 2"));
  assert.equal(await page.evaluate(() => fixture.signatureVersions.find(version => version.version_no === 1).entries.length), 31);
  await page.getByText("현재 운영에서 사용하는 버전은 아닙니다.", { exact: true }).waitFor();
  await snap("saved-candidate-KR");
  await go("#evaluate/tests/new");
  const explanations = page.locator(".candidate-configuration").getByRole("combobox", { name: "시그니처 설명", exact: false });
  await explanations.waitFor(); await page.waitForFunction(() => document.querySelector('.candidate-configuration select option[value="00000902-1111-4111-8111-111111111111"]'));
  assert.equal(await explanations.inputValue(), "");
  await explanations.selectOption("00000902-1111-4111-8111-111111111111"); await snap("test-configuration-KR");
  await go("#configure/signature-explanations"); await page.getByRole("button", { name: "복제해 수정", exact: true }).waitFor();
  await page.setViewportSize({ width: 390, height: 844 });
  await snap("catalog-mobile-KR"); await page.getByRole("button", { name: "EN", exact: true }).click(); await snap("catalog-mobile-EN");
  await page.setViewportSize({ width: 1440, height: 1000 }); await go("#promotion/00000008-1111-4111-8111-111111111111");
  await page.getByRole("heading", { name: "Configuration comparison", exact: true }).waitFor();
  assert.equal(await page.getByText("Signature explanations", { exact: true }).count() >= 2, true); await snap("production-review-EN");
  assert.deepEqual(errors, []); assert.deepEqual(network, []);
  assert.equal(await page.evaluate(() => fixture.writes.some(call => /activate|promote|runModel/i.test(call.name))), false);
  assert.equal(await page.evaluate(() => fixture.unknown.length), 0);
  console.log(JSON.stringify({ output, screenshots, errors, externalTraffic: network.length, checks: "SK/Light/Dark, KR/EN, desktop/mobile, keyboard opening/Escape/focus return, pagination, escaped text, exact matching preview, duplicate rejection, immutable clone/edit/remove/save, no Production activation, optional Test selection, Production comparison" }));
} catch (error) {
  await page?.screenshot({ path: join(output, "failure.png"), fullPage: true });
  console.error(JSON.stringify({ output, errors: await page?.evaluate(() => fixture.unknown), text: await page?.locator("body").innerText() }));
  throw error;
} finally { await browser.close(); }
