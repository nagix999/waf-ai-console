// All application APIs are replaced in the bundle. No API, LLM or paid calls.
// Uses an existing development browser only; it never downloads a browser.
import assert from "node:assert/strict";
import { existsSync, readdirSync, mkdtempSync } from "node:fs";
import { homedir, tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { build } from "esbuild";

const source = fileURLToPath(new URL("../src", import.meta.url));
const archive = join(homedir(), ".cache/uv/archive-v0");
const driver = process.env.PLAYWRIGHT_MODULE || readdirSync(archive).map(name => join(archive, name, "playwright/driver/package/index.mjs")).find(existsSync);
if (!driver) throw Error("Existing development browser required");
const { chromium } = await import(pathToFileURL(driver).href);
const output = mkdtempSync(join(tmpdir(), "waf-console-ui-"));
const mock = `
window.fixture={reads:[],writes:[],unknown:[],dashboardError:false,recentError:false,deferRecent:false};const state=window.fixture;
const profile={id:'p1',name:'내부 검증 모델',provider:'vllm',model_name:'fixture-model',can_assign:true,status:'production'};
const role={primary_profile_id:'p1',verifier_profile_id:null,evidence_editor_enabled:false,evidence_editor_profile_id:null};
const agents={state_token:'a'.repeat(64),profiles:[profile,{...profile,id:'p2',name:'후보 모델',status:'verified'}],assignments:{production:{...role},test:{...role}}};
const versions=prefix=>({active_version_id:prefix+'1',items:[{id:prefix+'1',name:'기본 설정',version_number:1,created_at:'2026-09-17T00:00:00Z'}]});
const evaluation={total:0,labeled:0,evaluable:0,matches:0,binary_evaluable:0,binary_decided:0,unlabeled:0,metrics:{},confusion_matrix:{},outcomes:{},source_groups:[]};
const list={items:[],total:0,evaluation_summary:evaluation};
const methods={
 me:async()=>({username:'fixture-admin',kind:'admin_session',scopes:['admin']}), logout:async()=>({}),
 dashboard:async(days,options,key)=>{if(state.dashboardError)throw Error('private-fixture-error');return {window:{days,created_from:days===7?'2026-09-10T00:00:00Z':'2026-08-18T00:00:00Z',created_to:'2026-09-17T00:00:00Z'},runtime:{agent_mode:'moduagent',production_profile:profile,worker_health_verified:false},counts:{total:128,completed:120,processing:0,pending:0,failed:8},evaluation_summary:evaluation,trend:Array.from({length:7},(_,i)=>({date:'2026-09-'+(10+i),total:[13,21,7,30,24,12,21][i],evaluation_summary:evaluation}))};},
 serviceApiKeys:async()=>({items:[{id:'fixture-key',name:'사내 WAF',purpose:'production'}]}), agentSettings:async()=>agents,
 promptPolicies:async()=>versions('v'), inputSchemas:async()=>versions('s'),
 concurrencySettings:async()=>({state_token:'a'.repeat(64),production:3,test:2,servers:[]}),
 analyses:async query=>{
   if(query.limit!==10||query.analysis_purpose!=='production'||!query.created_from)return {...list};
   if(state.deferRecent)await new Promise(resolve=>{state.resolveRecent=resolve});
   if(state.recentError)throw Error('private-recent-error');
   return {...list,total:5,items:['SQL 구문 탐지','정상 이미지 요청','경로 탐색 시도','로그인 요청','입력 확인 실패'].map((signature,i)=>({id:'fixture-analysis-'+i,created_at:'2026-09-17T00:00:00Z',signature:(query.service_api_key_id?'키별 ':'')+signature,status:i===4?'failed':'completed',verdict:i===3?'inconclusive':i===1?'false_positive':'true_positive',total_elapsed_ms:i===4?null:1230}))};
 }, testRuns:async()=>list, validationDatasets:async()=>list, searchValidationDatasets:async()=>list,
 modelProfiles:async()=>[], internalEgress:async()=>[],
 promptPolicy:async id=>({id,name:'기본 설정',version_number:1,policy_text:'Read only fixture',change_note:'Fixture',parent_version_id:null}),
 inputSchema:async()=>{throw Error('fixture_schema_unavailable');}, inputSchemaHistory:async()=>({items:[]}), productionApi:async()=>{throw Error('fixture_docs_unavailable');},
 agentDiagnostics:async()=>({counts:{total:0,completed:0,measured:0,inconclusive:0,failed:0},inconclusive_rate:null,unmeasured_completed:0,llm_failure_counts:{}}),
};
export const api=new Proxy(methods,{get(target,name){if(!target[name])return async(...args)=>{state.unknown.push(name);throw Error('Unexpected mock API '+name);};return async(...args)=>{state.reads.push({name,args});return target[name](...args);};}});
`;
const bundle = await build({ stdin: { contents: `import {createRoot} from 'react-dom/client';import App from './App.jsx';import './styles.css';import './console.css';createRoot(document.getElementById('root')).render(<App/>);`, loader: "jsx", resolveDir: source },
  plugins: [{ name: "offline-api", setup(b) { b.onLoad({ filter: /\/api\.js$/ }, () => ({ contents: mock, loader: "js" })); } }],
  bundle: true, write: false, outfile: join(output, "bundle.js"), platform: "browser", format: "iife", jsx: "automatic", define: { "process.env.NODE_ENV": '"production"' }, loader: { ".md": "text", ".woff2": "dataurl" }, logLevel: "silent" });
const browser = await chromium.launch({ executablePath: process.env.CHROMIUM_PATH || join(homedir(), ".cache/ms-playwright/chromium-1208/chrome-linux64/chrome"), headless: true, args: ["--no-sandbox"] });
let page;
try {
  page = await browser.newPage({ viewport: { width: 1440, height: 900 } }); page.setDefaultTimeout(10000); const errors = []; const blocked = [];
  page.on("pageerror", error => { errors.push(error.message); console.error("Browser error:", error.message); });
  await page.route("**/*", route => route.request().url() === "https://fixture.invalid/" ? route.fulfill({ contentType: "text/html", body: '<html lang="ko"><body><div id="root"></div></body></html>' }) : (blocked.push(route.request().url()), route.abort()));
  await page.goto("https://fixture.invalid/");
  await page.addStyleTag({ content: bundle.outputFiles.find(file => file.path.endsWith(".css")).text });
  await page.addScriptTag({ content: bundle.outputFiles.find(file => file.path.endsWith(".js")).text });
  await page.getByRole("heading", { name: "현재 운영 설정", exact: true }).waitFor();
  await page.getByText("내부 검증 모델", { exact: true }).first().waitFor();
  const nav = page.locator(".console-sidebar nav");
  async function go(group, item) {
    const heading = nav.getByRole("button", { name: group, exact: true });
    if (await heading.getAttribute("aria-expanded") !== "true") await heading.click();
    await nav.getByRole("button", { name: item, exact: true }).click();
    await page.getByRole("heading", { name: item, exact: true, level: 1 }).waitFor();
  }
  async function chooseTheme(value, label = "테마") { await page.getByRole("button", { name: label, exact: true }).click(); await page.getByRole("button", { name: value, exact: true }).click(); }
  async function chooseLocale(value, label) { await page.getByRole("button", { name: label, exact: true }).click(); await page.getByRole("button", { name: value, exact: true }).click(); }
  async function noHorizontalOverflow() { assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true); }
  await noHorizontalOverflow();
  assert.equal(await page.locator(".console-header .admin-avatar").innerText(), "F");
  assert.equal(await page.getByText("운영 상태 미측정", { exact: true }).count(), 1);
  const recent = page.locator(".runtime-recent");
  await recent.getByText("SQL 구문 탐지", { exact: true }).waitFor();
  assert.equal(await recent.getByRole("button", { name: "상세 보기", exact: true }).count(), 5);
  assert.doesNotMatch(await recent.innerText(), /fixture-analysis-|true_positive|false_positive/);
  assert.equal(await page.locator(".runtime-health-grid > article").count(), 4);
  assert.equal(await page.locator(".console-header").evaluate(el => el.getBoundingClientRect().height), 64);
  assert.equal(await page.locator('.console-sidebar-footer').evaluate(el=>el.getBoundingClientRect().bottom<=innerHeight),true);
  for (const [label, value] of [["Light", "light"], ["SK", "sk"], ["Dark", "dark"]]) {
    await chooseTheme(label); assert.equal(await page.locator("html").getAttribute("data-theme"), value);
    assert.equal(await page.evaluate(() => getComputedStyle(document.documentElement).getPropertyValue('--success').trim()), "#1f9d63");
    await page.screenshot({ path: join(output, `runtime-ko-${value}.png`), fullPage: true });
  }
  await chooseTheme("Light"); await chooseLocale("EN", "화면 언어");
  assert.equal(await page.locator("html").getAttribute("lang"), "en"); await noHorizontalOverflow();
  await page.screenshot({ path: join(output, "runtime-en-light.png"), fullPage: true });
  await chooseLocale("KR", "Interface language");
  for (const [width, height] of [[1280,900],[1536,1024]]) {
    await page.setViewportSize({width,height}); await noHorizontalOverflow();
    const layout = await page.evaluate(() => {
      const box = selector => document.querySelector(selector).getBoundingClientRect();
      const config=box('.runtime-config'), trend=box('.runtime-trend'), filters=box('.runtime-filter-row'), title=box('.console-page-heading h1');
      return {sameRow:Math.abs(config.y-trend.y)<1, ratio:config.width/(config.width+trend.width), controlsClear:title.right<filters.left, sidebar:box('.console-sidebar').width};
    });
    assert.equal(layout.sameRow,true); assert.ok(layout.ratio>.57&&layout.ratio<.59); assert.equal(layout.controlsClear,true); assert.equal(layout.sidebar,242);
    await page.screenshot({path:join(output,`runtime-ko-${width}.png`),fullPage:true});
  }
  await page.setViewportSize({width:1440,height:900});
  await page.evaluate(()=>{window.fixture.deferRecent=true});
  await page.locator('.runtime-filter-row').getByLabel('API 키',{exact:true}).selectOption('fixture-key');
  await page.waitForFunction(()=>Boolean(window.fixture.resolveRecent));
  assert.equal(await recent.getByText('SQL 구문 탐지',{exact:true}).count(),0);
  await page.evaluate(()=>{window.fixture.deferRecent=false;window.fixture.resolveRecent()});
  await recent.getByText('키별 SQL 구문 탐지',{exact:true}).waitFor();
  const recentQuery=await page.evaluate(()=>window.fixture.reads.filter(call=>call.name==='analyses').at(-1).args[0]);
  assert.equal(recentQuery.service_api_key_id,'fixture-key');assert.equal(recentQuery.analysis_purpose,'production');assert.equal(recentQuery.limit,10);assert.equal(recentQuery.created_from,'2026-09-10T00:00:00Z');
  await page.evaluate(()=>{window.fixture.recentError=true});
  await page.locator('.runtime-filter-row').getByLabel('기간',{exact:true}).selectOption('30');
  await recent.getByRole('alert').waitFor(); assert.doesNotMatch(await recent.innerText(),/private-recent-error|키별 SQL/);
  await page.evaluate(()=>{window.fixture.recentError=false});
  await page.locator('.runtime-filter-row').getByLabel('API 키',{exact:true}).selectOption('');
  await recent.getByText('SQL 구문 탐지',{exact:true}).waitFor();
  await recent.getByRole('button',{name:/분석 이력 보기/}).click();
  await page.getByRole('heading',{name:'분석 이력',level:1,exact:true}).waitFor();
  await page.waitForFunction(()=>window.fixture.reads.some(call=>call.name==='analyses'&&call.args[0].analysis_purpose==='production'&&Date.parse(call.args[0].created_from)===Date.parse('2026-08-18T00:00:00Z')&&call.args[0].limit===25));
  await go("평가", "테스트 실행");
  await page.getByLabel("회사명", { exact: true }).fill("private-ui-draft");
  await chooseTheme("Dark"); await chooseLocale("EN", "화면 언어");
  assert.equal(await page.getByLabel("회사명", { exact: true }).inputValue(), "private-ui-draft");
  assert.equal(await page.evaluate(() => location.hash), "#test");
  await chooseLocale("KR", "Interface language"); await chooseTheme("Light");
  await go("모델·Agent", "Agent 구성");
  const production = page.locator("fieldset").filter({ has: page.locator("legend", { hasText: "프로덕션" }) });
  await production.getByLabel("Primary · 1차 판정").selectOption("p2");
  await page.getByRole("button", { name: "배정 저장", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "모델 배정 확인", exact: true }); await dialog.waitFor();
  await page.keyboard.press("Escape"); assert.equal(await dialog.isVisible(), false);
  assert.equal(await production.getByLabel("Primary · 1차 판정").inputValue(), "p2");
  assert.equal(await page.getByRole("tablist", { name: "설정 항목", exact: true }).count(), 0);
  assert.equal(await page.getByRole("tablist", { name: "Agent 설정 구분", exact: true }).count(), 0);
  for (const [group, item] of [["모델·Agent","공통 지침"],["모델·Agent","동시 처리"],["모델·Agent","LLM 프로필"],["API·연동","API 키"],["API·연동","입력 스키마"],["API·연동","vLLM 연결 대상"],["API·연동","Production API"],["평가","Ground Truth"],["런타임","진단"],["런타임","배포 정보"],["런타임","변경 이력"],["런타임","운영 품질"]]) await go(group,item);
  await go("추론", "분석 이력"); await page.getByRole("tab", { name: "테스트", exact: true }).click();
  assert.equal(await page.locator("h1").innerText(), "분석 이력");
  assert.equal(await page.getByLabel("테스트명 검색", { exact: true }).count(), 0);
  await go("평가", "테스트 결과"); await page.getByRole("textbox", { name: "테스트명 검색", exact: true }).waitFor();
  await page.goBack(); await page.getByRole("heading", { name: "분석 이력", level: 1 }).waitFor();
  assert.equal(await page.getByRole("tab", { name: "테스트", exact: true }).getAttribute("aria-selected"), "true");
  await page.getByRole("search").getByRole("combobox").selectOption("signature");
  await page.getByLabel("분석 이력 검색", { exact: true }).fill("private-fixture-query");
  await page.getByRole("search").getByRole("button", { name: "검색", exact: true }).click();
  await page.waitForFunction(() => window.fixture.reads.some(call => call.name === 'analyses' && call.args[0].q === 'private-fixture-query'));
  const stored = await page.evaluate(() => JSON.stringify({ url: location.href, history: history.state, local: { ...localStorage }, session: { ...sessionStorage } }));
  assert.doesNotMatch(stored, /private-fixture-query|private-ui-draft/);
  await go("런타임", "상태"); await page.setViewportSize({ width: 390, height: 844 }); await noHorizontalOverflow();
  await page.screenshot({ path: join(output, "runtime-ko-mobile.png"), fullPage: true });
  await page.getByRole("button", { name: "메뉴 열기", exact: true }).click();
  const mobile = page.getByRole("dialog", { name: "주 메뉴", exact: true });
  assert.equal(await mobile.locator("nav").evaluate(element => getComputedStyle(element).gridTemplateColumns.split(' ').length), 1);
  assert.equal(await mobile.getByRole("button", { name: "상태", exact: true }).evaluate(element => getComputedStyle(element).textAlign), "left");
  await page.screenshot({ path: join(output, "navigation-ko-mobile.png"), fullPage: true });
  const evaluationMenu = mobile.getByRole("button", { name: "평가", exact: true });
  if (await evaluationMenu.getAttribute("aria-expanded") !== "true") await evaluationMenu.click();
  await mobile.getByRole("button", { name: "테스트 실행", exact: true }).click();
  assert.equal(await mobile.isVisible(), false); await noHorizontalOverflow();
  await page.screenshot({ path: join(output, "test-ko-mobile.png"), fullPage: true });
  assert.deepEqual(errors, []); assert.deepEqual(blocked, []);
  assert.deepEqual(await page.evaluate(() => window.fixture.unknown), []);
  assert.equal(await page.evaluate(() => window.fixture.reads.some(call => /^(update|create|run|delete|activate|upload|save)/.test(call.name))), false);
  console.log(JSON.stringify({ result: "passed", screenshots: output, checked: ["all navigation leaves", "KR light/SK/dark", "EN light", "390px layout", "theme/locale draft retention", "Production assignment dialog without mutation", "Test items vs runs", "browser Back", "URL/storage privacy", "Escape and mobile dialog", "no external traffic or model calls"] }));
} catch (error) {
  if (page) await page.screenshot({ path: join(output, "failure.png"), fullPage: true });
  console.error("Screenshot directory:", output); throw error;
} finally { await browser.close(); }
