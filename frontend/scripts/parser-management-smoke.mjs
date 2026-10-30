// Actual React/CSS with synthetic, offline-only APIs. No DB, model or remote calls.
import assert from "node:assert/strict";
import { existsSync, readdirSync, mkdirSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { build } from "esbuild";
import { r5BrowserFixture } from "./r5-browser-fixture.mjs";

const output = fileURLToPath(new URL("../../test-results/parser-management", import.meta.url));
mkdirSync(output, { recursive: true });
const mock = r5BrowserFixture().replace("export const api=", `
const parserOptions={line_endings:'actual',request_line_whitespace:'strict',skip_leading_blank_lines:false};
const parserVersions=[{id:'generic-http-v3',name:'기존 기본 파서',description:'기존 요청 형식을 그대로 사용',engine_version:'generic-http-v3',options:parserOptions,created_at:date,content_hash:hash},{id:id(201),name:'문자 줄바꿈 수집 형식',description:'문자로 저장된 줄바꿈을 명시적으로 인식',engine_version:'http-capture-v1',options:{...parserOptions,line_endings:'escaped_crlf'},parent_id:'generic-http-v3',created_at:date,content_hash:'b'.repeat(64)}];
const parserResult={summary:{sample_count:3,source_total:3,selection_limited:false,total:3,changed_cases:2,status_counts:{baseline:{success:1,partial:2,failed:0},candidate:{success:1,partial:2,failed:0}},valid_source_spans:3,all_valid_source_spans:true,structure_reviewed:false},items:[1,2,3].map(n=>({analysis_id:id(240+n),waf_vendor:n<3?'Vendor A':'Unknown vendor',raw_payload:'GET /example?case='+n+' HTTP/1.1\\\\r\\\\nHost: example.test\\\\r\\\\n\\\\r\\\\n<script>not executed</script>',baseline:{parse_status:n===1?'success':'partial',method:'GET',body:null,warnings:n===1?[]:['headers_terminator_missing']},candidate:{parse_status:n===1?'success':'partial',method:'GET',headers:{Host:'example.test'},body:'<script>not executed</script>',warnings:n===2?['capture_serialization_unverified']:n===3?['headers_terminator_missing']:[],capture_selection:n===3?{status:'generic_fallback',reason_code:'capture_profile_unmatched',line_endings:'escaped_crlf'}:{status:'matched',profile_index:n-1,profile_name:n===1?'Vendor A 일반 형식':'Vendor A 문자 줄바꿈',line_endings:n===1?'actual':'escaped_crlf'},raw_source_spans:{body:[{start:50,end:79}]}},changed_fields:['headers','body','raw_source_spans'],valid_source_spans:true})),result_truncated:false};
const parserJobs=Array.from({length:7},(_,n)=>({id:id(220+n),kind:'comparison',status:'completed',baseline_version_id:'generic-http-v3',candidate_version_id:id(201),created_at:date,completed_at:date,summary:parserResult.summary,result:parserResult}));
Object.assign(methods,{
 parsers:async()=>({items:parserVersions,active_version_id:'generic-http-v3',revision:1}),
 parserJobs:async(query={})=>({items:parserJobs.slice(Number(query.offset||0),Number(query.offset||0)+Number(query.limit||5)).map(({result,...job})=>job),total:parserJobs.length,limit:Number(query.limit||5),offset:Number(query.offset||0)}),
 parserJob:async which=>{state.parserRawReads=(state.parserRawReads||0)+1;return parserJobs.find(job=>job.id===which)},
 createParser:async payload=>{state.writes.push({name:'createParser',payload});const version={...payload,id:id(210+parserVersions.length),created_at:date,content_hash:hash};parserVersions.unshift(version);return version},
 compareParsers:async payload=>{state.writes.push({name:'compareParsers',payload});return parserJobs[0]},
 suggestParser:async payload=>{state.writes.push({name:'suggestParser',payload});const job={id:id(320),kind:'suggestion',status:'completed',baseline_version_id:payload.baseline_version_id,profile_id:payload.profile_id,created_at:date,result:{sample_count:2,suggestion:{engine_version:'http-capture-v1',options:{...parserOptions,line_endings:'escaped_crlf'},reason_ko:'문자로 저장된 줄바꿈이 확인되어 해당 수집 형식으로 비교할 수 있습니다.',limitations_ko:['실제 줄바꿈과 혼합된 입력은 별도 확인이 필요합니다.']}}};parserJobs.unshift(job);return job},
 reviewParserComparison:async which=>{state.writes.push({name:'reviewParserComparison',id:which});const reviewed=parserJobs.find(job=>job.id===which);reviewed.summary={...reviewed.summary,structure_reviewed:true};return reviewed},
});
export const api=`);
const bundle = await build({ stdin: { contents: `import {createRoot} from 'react-dom/client';import App from './App.jsx';import './styles.css';import './console.css';import './lifecycle.css';import './r3.css';createRoot(document.getElementById('root')).render(<App/>);`, loader: "jsx", resolveDir: fileURLToPath(new URL("../src", import.meta.url)) },
  plugins: [{ name: "offline-api", setup(builder) { builder.onLoad({ filter: /\/api\.js$/ }, () => ({ contents: mock, loader: "js" })); } }],
  bundle: true, write: false, outfile: join(output, "bundle.js"), platform: "browser", format: "iife", jsx: "automatic", define: { "process.env.NODE_ENV": '"production"' }, loader: { ".md": "text", ".woff2": "dataurl" }, logLevel: "silent" });
const archive = join(homedir(), ".cache/uv/archive-v0");
const driver = process.env.PLAYWRIGHT_MODULE || readdirSync(archive).map(name => join(archive, name, "playwright/driver/package/index.mjs")).find(existsSync);
const { chromium } = await import(pathToFileURL(driver).href);
const browser = await chromium.launch({ executablePath: process.env.CHROMIUM_PATH || join(homedir(), ".cache/ms-playwright/chromium-1208/chrome-linux64/chrome"), headless: true, args: ["--no-sandbox"] });
try {
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 }, reducedMotion: "reduce" });
  const errors = [], network = []; let screenshots = 0;
  page.on("pageerror", error => errors.push(error.message));
  await page.route("**/*", route => route.request().url().split("#")[0] === "https://fixture.invalid/" ? route.fulfill({ contentType: "text/html", body: '<html lang="ko"><head><meta name="viewport" content="width=device-width,initial-scale=1"></head><body><div id="root"></div></body></html>' }) : (network.push(route.request().url()), route.abort()));
  await page.goto("https://fixture.invalid/");
  await page.addStyleTag({ content: bundle.outputFiles.find(file => file.path.endsWith(".css")).text });
  await page.addScriptTag({ content: bundle.outputFiles.find(file => file.path.endsWith(".js")).text });
  page.setDefaultTimeout(10000); await page.locator(".r3-overview").waitFor();
  const go = hash => page.evaluate(hash => { location.hash = hash; }, hash);
  const snap = async name => {
    await page.screenshot({ path: join(output, name + ".png"), fullPage: true }); screenshots++;
    assert.deepEqual(await page.evaluate(() => [...document.querySelectorAll('body *')].filter(node => { const rect = node.getBoundingClientRect(); return rect.width && rect.right > innerWidth + 2 && !node.closest('.data-table-wrap,.table-wrap'); }).map(node => ({ tag: node.tagName, className: String(node.className), width: node.getBoundingClientRect().width, right: node.getBoundingClientRect().right, text: node.innerText?.slice(0, 70) })).slice(0, 12)), [], name);
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true, name);
  };
  await go("#configure/parsers"); await page.locator(".parser-current strong").filter({ hasText: "기존 기본 파서" }).waitFor();
  assert.equal(await page.locator("aside [aria-current=page]").innerText(), "파서 관리");
  assert.equal(await page.evaluate(() => fixture.parserRawReads || 0), 0);
  for (const theme of ["SK", "Light", "Dark"]) {
    await page.getByRole("button", { name: /^(테마|Theme)$/ }).click(); await page.getByRole("button", { name: theme, exact: true }).click();
    for (const locale of ["KR", "EN"]) {
      await page.getByRole("button", { name: locale, exact: true }).click();
      assert.equal(await page.locator("aside [aria-current=page]").innerText(), locale === "KR" ? "파서 관리" : "Parser Management");
      await snap(`management-${locale}-${theme}`);
    }
  }
  await page.getByRole("button", { name: "KR", exact: true }).click();
  const history = page.getByRole("navigation", { name: "파서 작업 페이지", exact: true });
  await history.getByRole("button", { name: "마지막 페이지", exact: true }).click();
  await page.waitForFunction(() => fixture.reads.filter(call => call.name === "parserJobs").at(-1).args[0].offset === 5);
  await history.getByRole("button", { name: "첫 페이지", exact: true }).click();
  await page.waitForFunction(() => fixture.reads.filter(call => call.name === "parserJobs").at(-1).args[0].offset === 0);
  const create = page.getByRole("button", { name: "새 버전 작성", exact: true });
  await create.focus(); await page.keyboard.press("Enter");
  const draft = page.getByRole("dialog", { name: "새 파서 버전", exact: true }); await draft.waitFor();
  assert.equal(await draft.getByRole("textbox", { name: "버전 이름", exact: true }).evaluate(node => node === document.activeElement), true);
  await draft.getByRole("textbox", { name: "버전 이름", exact: true }).fill("회귀 비교용 형식");
  await draft.getByRole("combobox", { name: "줄바꿈 형식", exact: true }).selectOption("escaped_crlf");
  await snap("create-version");
  await draft.getByRole("button", { name: "프로필 추가", exact: true }).click();
  let profile = draft.locator(".parser-format-profile").nth(0);
  await profile.getByRole("textbox", { name: "프로필 이름", exact: true }).fill("Vendor A 일반 형식");
  await profile.getByRole("textbox", { name: "WAF 벤더", exact: true }).fill("Vendor A");
  await draft.getByRole("button", { name: "프로필 추가", exact: true }).click();
  profile = draft.locator(".parser-format-profile").nth(1);
  await profile.getByRole("textbox", { name: "프로필 이름", exact: true }).fill("Vendor A 문자 줄바꿈");
  await profile.getByRole("textbox", { name: "WAF 벤더", exact: true }).fill("Vendor A");
  await profile.getByRole("combobox", { name: "줄바꿈 형식", exact: true }).selectOption("escaped_crlf");
  assert.equal(await draft.getByRole("button", { name: "프로필 삭제", exact: true }).count(), 2);
  await snap("vendor-format-profiles");
  await draft.getByRole("button", { name: "새 버전 저장", exact: true }).click(); await draft.waitFor({ state: "hidden" });
  assert.equal(await page.evaluate(() => fixture.writes.filter(call => call.name === "createParser").length), 1);
  assert.equal(await page.evaluate(() => fixture.writes.find(call => call.name === "createParser").payload.options.profiles.length), 2);
  assert.equal(await page.locator(".parser-current strong").innerText(), "기존 기본 파서");
  await page.getByRole("combobox", { name: "테스트", exact: true }).selectOption("00000008-1111-4111-8111-111111111111");
  assert.equal(await page.getByRole("link", { name: /^선택 테스트 통계 보기/ }).getAttribute("href"), "#evaluate/tests/00000008-1111-4111-8111-111111111111/statistics");
  await page.getByRole("button", { name: "파싱 비교 실행", exact: true }).click();
  const comparison = page.getByRole("dialog", { name: "파싱 비교 결과", exact: true }); await comparison.getByRole("heading", { name: "문항 1 · 원문과 인식 결과", exact: true }).waitFor();
  assert.ok(await page.evaluate(() => fixture.parserRawReads > 0)); assert.match(await comparison.locator(".parser-source").innerText(), /<script>/);
  assert.equal(await comparison.locator("script").count(), 0); assert.equal(await comparison.locator(".parser-compare-columns .json-tree").count(), 2);
  await snap("comparison-KR");
  await comparison.getByRole("navigation", { name: "비교 문항", exact: true }).getByRole("button", { name: "다음 페이지", exact: true }).click();
  await comparison.getByRole("heading", { name: "문항 2 · 원문과 인식 결과", exact: true }).waitFor();
  await comparison.getByRole("navigation", { name: "비교 문항", exact: true }).getByRole("button", { name: "다음 페이지", exact: true }).click();
  await comparison.getByRole("heading", { name: "문항 3 · 원문과 인식 결과", exact: true }).waitFor();
  const candidate = comparison.locator(".parser-compare-columns section").filter({ has: page.locator("h4").filter({ hasText: "비교 파서" }) }).first();
  await candidate.getByRole("button", { name: "JSON", exact: true }).click();
  assert.match(await candidate.innerText(), /capture_profile_unmatched/); await snap("unmatched-vendor-fallback");
  await comparison.getByRole("button", { name: "파싱 결과 확인 완료로 기록", exact: true }).click();
  await comparison.getByRole("button", { name: "파싱 결과 확인 완료", exact: true }).waitFor();
  assert.equal(await page.evaluate(() => fixture.writes.filter(call => call.name === "reviewParserComparison").length), 1);
  await page.keyboard.press("Escape"); await comparison.waitFor({ state: "hidden" });
  await page.getByRole("combobox", { name: "내부 vLLM 프로필", exact: true }).selectOption("00000001-1111-4111-8111-111111111111");
  const generate = page.getByRole("button", { name: "개선안 생성", exact: true }); assert.equal(await generate.isDisabled(), true);
  await page.getByRole("checkbox", { name: "선택한 원문·Cookie를 이 내부 모델로 보내는 것을 확인했습니다.", exact: true }).check();
  await generate.click(); const confirm = page.getByRole("dialog", { name: "내부 모델로 개선안 생성", exact: true }); await confirm.waitFor();
  assert.match(await confirm.innerText(), /원본 분석을 삭제해도 별도로 보존됩니다/);
  assert.equal(await page.evaluate(() => fixture.writes.filter(call => call.name === "suggestParser").length), 0);
  await confirm.getByRole("button", { name: "확인 후 생성", exact: true }).click();
  const suggestion = page.getByRole("dialog", { name: "LLM 개선안", exact: true }); await suggestion.getByRole("button", { name: "검토 후 새 버전 작성", exact: true }).waitFor();
  await snap("suggestion-KR"); assert.equal(await page.evaluate(() => fixture.writes.filter(call => call.name === "createParser").length), 1);
  await suggestion.getByRole("button", { name: "검토 후 새 버전 작성", exact: true }).click(); await draft.waitFor();
  assert.equal(await draft.getByRole("textbox", { name: "버전 이름", exact: true }).inputValue(), "");
  await page.keyboard.press("Escape"); await draft.waitFor({ state: "hidden" });
  await go("#evaluate/tests/new"); const parser = page.getByRole("combobox", { name: "파서 버전", exact: true }); await parser.waitFor();
  assert.equal(await parser.inputValue(), "generic-http-v3");
  await parser.selectOption("00000201-1111-4111-8111-111111111111"); await snap("candidate-parser");
  await page.goBack(); await page.locator(".parser-management").waitFor(); await page.goForward(); await parser.waitFor();
  await go("#configure/parsers"); await page.locator(".parser-management").waitFor();
  await page.setViewportSize({ width: 390, height: 844 });
  await snap("management-mobile-KR");
  await page.getByRole("button", { name: "EN", exact: true }).click(); await snap("management-mobile-EN");
  assert.deepEqual(errors, []); assert.deepEqual(network, []); assert.deepEqual(await page.evaluate(() => fixture.unknown), []);
  assert.equal(await page.evaluate(() => fixture.writes.some(call => /promote|activate/i.test(call.name))), false);
  console.log(JSON.stringify({ screenshots, checks: "KR/EN, SK/Light/Dark, desktop/mobile, keyboard focus and Escape, safe immutable creation, audited raw comparison, case pagination, manual review, explicit internal suggestion confirmation, proposal not auto-saved, candidate parser and Back/Forward", externalTraffic: network.length }));
} finally { await browser.close(); }
