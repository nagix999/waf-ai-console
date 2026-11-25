// Real React and browser interactions against synthetic, offline APIs only.
import assert from "node:assert/strict";
import { existsSync, readdirSync, mkdirSync, unlinkSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { build } from "esbuild";
import { r5BrowserFixture } from "./r5-browser-fixture.mjs";

const output = fileURLToPath(new URL("../../test-results/console-workspaces", import.meta.url));
mkdirSync(output, { recursive: true });
const archive = join(homedir(), ".cache/uv/archive-v0");
const driver = process.env.PLAYWRIGHT_MODULE || readdirSync(archive).map(name => join(archive, name, "playwright/driver/package/index.mjs")).find(existsSync);
const { chromium } = await import(pathToFileURL(driver).href);
const prod = "11111111-1111-4111-8111-111111111111", testId = "22222222-2222-4222-8222-222222222222";
const runId = "33333333-3333-4333-8333-333333333333", firstStep = "44444444-4444-4444-8444-444444444444", secondStep = "55555555-5555-4555-8555-555555555555";
const fixture = r5BrowserFixture().replace("export const api=", `
import { rememberEvidenceMeta, analysisEvidenceMeta } from './analysisEvidenceMeta.js';
const syntheticRaw='POST /🔒?q=%3Cscript%3E HTTP/1.1\\r\\nHost: synthetic.invalid\\r\\nX-Note: repeated-canary\\r\\nCookie: synthetic-session=unmasked\\r\\n\\r\\n'+Array.from({length:38},(_,i)=>'합성 문맥 '+i+' / unchanged input').join('\\r\\n')+'\\r\\nrepeated-canary 한글 🔑 <script>window.injected=true</script>\\r\\n';
const excerpt='repeated-canary', last=syntheticRaw.lastIndexOf(excerpt);
const span={start:[...syntheticRaw.slice(0,last)].length,end:[...syntheticRaw.slice(0,last+excerpt.length)].length};
const result={schema_version:'waf-analysis-v3',verdict:'inconclusive',confidence_score:.82,summary_ko:'요청의 입력 형태와 실제 처리 경로를 함께 확인해야 합니다. 저장된 근거와 참고 정보를 대조해 판정을 검토하세요.',threat_analysis:{severity:'UNKNOWN',category:'command_injection',target:'payload.body',technique_ko:'명령으로 사용되는지 데이터로 보관되는지 처리 경로에 따라 해석이 달라집니다.',potential_impact_ko:'실행 경로에서 처리되는 경우에 한해 영향이 발생할 수 있습니다.',obfuscations:[]},signature_assessment:{relation:'partial',explanation_ko:'장비의 탐지 기록은 실제 요청의 일부 내용과 관련됩니다.'},input_truncated:false,evidence:[
 {evidence_id:'e1',field:'payload',excerpt,source_spans:[span],supports:'attack_request',interpretation_ko:'본문에 저장된 합성 표식을 확인했습니다. 요청의 실제 처리 방식과 함께 검토할 내용입니다.',origins:[{role:'primary',index:0,source_id:'c1'}]},
 {evidence_id:'e2',field:'signature',excerpt:'synthetic signature',source_spans:[],supports:'context',interpretation_ko:'시그니처는 장비가 기록한 참고 정보입니다. 같은 문구가 원문에 있더라도 이 근거는 입력 필드에만 연결합니다.',origins:[{role:'verifier',index:0,source_id:'c2'}]},
 {evidence_id:'e3',field:'decoded_payload_hints.items.0.decoded',excerpt:'<script>',source_spans:[],supports:'context',interpretation_ko:'변환된 표현을 원문의 위치로 잘못 연결하지 않는지 확인하기 위한 합성 사례입니다.',origins:[{role:'primary',index:1,source_id:'c3'}]},
 {evidence_id:'e4',field:'extra_fields.nested.score',excerpt:'0',source_spans:[],supports:'context',interpretation_ko:'처음에 접힌 추가 입력 필드도 정확한 원문 위치에 연결합니다.',origins:[{role:'verifier',index:1,source_id:'c4'}]}
],decision_issues:[{point_ko:'입력값이 실행에 사용되는지 확인이 필요합니다.',missing_condition_ko:'대상 애플리케이션에서 이 값을 처리한 경로를 확인하세요.',evidence_ids:['e1'],origins:['primary']}],primary:{verdict:'inconclusive',evidence_ids:['e1','e3']},verifier:{executed:true,agreement:true,reasons:[],output:{verdict:'inconclusive',evidence_ids:['e2']}},policy:{decision_evidence_ids:['e1']},agent:{framework:'moduagent'}};
const savedDetail={id:'${testId}',event_id:'synthetic-workspace',event_name:'합성 요청 분석',company_name:'Synthetic company',src_ip:'192.0.2.10',dest_ip:'198.51.100.20',src_port:40100,dest_port:443,waf_vendor:'Synthetic WAF',waf_action:'D',signature:'synthetic signature',status:'completed',analysis_purpose:'test',test_run_id:null,created_at:date,started_at:date,completed_at:date,total_elapsed_ms:12500,queue_wait_ms:2000,processing_duration_ms:10500,result,model_profile:'Saved Primary profile',prompt_version:'saved-policy-v1',evaluation:{outcome:'unlabeled'}};
state.workspaceRaw=syntheticRaw;
Object.assign(methods,{
 analysis:async value=>{const detail=structuredClone({...savedDetail,id:value,analysis_purpose:value==='${prod}'?'production':'test'});rememberEvidenceMeta(detail,{view:'core-v2',status:'completed'});return detail},
 rawEvent:async value=>({analysis_id:value,event_id:'synthetic-workspace',payload:syntheticRaw,extra_fields:{note:'synthetic signature',nested:{score:0,enabled:false}},decoding:{version:'synthetic-decoder',items:[]}}),
 agentRuns:async()=>[{id:'${runId}',status:'completed',started_at:date,duration_ms:10500,steps:[
 {id:'${firstStep}',step_type:'llm_primary',name:'llm_primary',status:'completed',started_at:date,duration_ms:6000,input:'SAVED_PRIMARY_INPUT',output:'SAVED_PRIMARY_OUTPUT',metadata:{model_profile:'Saved Primary profile',provider:'vllm',usage:{input_tokens:120,output_tokens:40},output_validation_retry:{attempt_count:1}},tool_calls:[]},
 {id:'${secondStep}',step_type:'llm_verifier',name:'llm_verifier',status:'completed',started_at:date,duration_ms:4500,input:'SAVED_VERIFIER_INPUT',output:'SAVED_VERIFIER_OUTPUT',metadata:{model_profile:'Saved Verifier profile',provider:'vllm',usage:{input_tokens:0,output_tokens:null},output_validation_retry:{attempt_count:2}},tool_calls:[{name:'SAVED_TOOL_RECORD',input:{synthetic:true}}]}
 ]}],evaluationLabels:async()=>({items:[]}),retryEligibility:async()=>({eligible:false}),
});
export const api=`).replace("get(target,name){", "get(target,name){if(name==='analysisEvidenceMeta')return analysisEvidenceMeta;");
const bundle = await build({ stdin: { contents: "import {createRoot} from 'react-dom/client';import App from './App.jsx';import './styles.css';import './console.css';import './lifecycle.css';import './r3.css';import './workspacePolish.css';createRoot(document.getElementById('root')).render(<App/>);", loader: "jsx", resolveDir: fileURLToPath(new URL("../src", import.meta.url)) }, plugins: [{ name: "offline-api", setup(builder) { builder.onLoad({ filter: /\/api\.js$/ }, () => ({ contents: fixture, loader: "js" })); } }], bundle: true, write: false, outfile: join(output, "bundle.js"), platform: "browser", format: "iife", jsx: "automatic", define: { "process.env.NODE_ENV": '"production"' }, loader: { ".md": "text", ".woff2": "dataurl" }, logLevel: "silent" });
const browser = await chromium.launch({ executablePath: process.env.CHROMIUM_PATH || join(homedir(), ".cache/ms-playwright/chromium-1208/chrome-linux64/chrome"), headless: true, args: ["--no-sandbox"] });
const errors = [], traffic = [], screenshots = [];
let page;
try {
  page = await browser.newPage({ viewport: { width: 1440, height: 960 }, reducedMotion: "reduce" }); page.setDefaultTimeout(10000);
  page.on("pageerror", error => errors.push(error.message));
  await page.route("**/*", route => route.request().url().split("#")[0] === "https://fixture.invalid/" ? route.fulfill({ contentType: "text/html", body: '<html lang="ko"><head><meta name="viewport" content="width=device-width, initial-scale=1"></head><body><div id="root"></div></body></html>' }) : (traffic.push(route.request().url()), route.abort()));
  await page.goto(`https://fixture.invalid/#evaluate/tests/items/${testId}/result`);
  await page.addStyleTag({ content: bundle.outputFiles.find(file => file.path.endsWith(".css")).text });
  await page.addScriptTag({ content: bundle.outputFiles.find(file => file.path.endsWith(".js")).text });
  const tabs = () => page.locator(".inference-detail > .detail-tabs"), tab = name => tabs().getByRole("tab", { name, exact: true }).click();
  const reads = name => page.evaluate(name => fixture.reads.filter(call => call.name === name).length, name);
  const go = async hash => { await page.evaluate(hash => { location.hash = hash; }, hash); };
  const ready = () => page.locator(".test-raw-payload").waitFor();
  const snap = async name => {
    await page.evaluate(() => scrollTo(0, 0));
    await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true, name + ": page overflow");
    const escapedStepContent = await page.locator('.execution-navigation:visible > button[data-execution-step]').evaluateAll(buttons => buttons.filter(button => {
      const content = button.querySelector(':scope > span:not(.inspection-step-number)');
      return content && content.getBoundingClientRect().bottom > button.getBoundingClientRect().bottom + 1;
    }).length);
    assert.equal(escapedStepContent, 0, name + ": step text must stay inside its selectable row");
    const fixedResult = await page.locator('.test-analysis-workspace:visible').count() && page.viewportSize().width > 760;
    if (fixedResult) {
      const geometry = await page.evaluate(() => ({ height: document.documentElement.scrollHeight, viewport: innerHeight, scroll: scrollY, boxes: ['body','.console-shell','.workspace','.console-content','.inference-detail','.test-analysis-workspace'].map(selector => {const node=document.querySelector(selector),box=node.getBoundingClientRect(),style=getComputedStyle(node);return {selector,height:box.height,top:box.top,bottom:box.bottom,scroll:node.scrollHeight,overflow:style.overflow,minimum:style.minHeight};}) }));
      assert.ok(geometry.height <= geometry.viewport + 80, name + ": unexpected blank document height " + JSON.stringify(geometry));
    }
    await page.screenshot({ path: join(output, name + ".png"), fullPage: !fixedResult }); screenshots.push(name);
  };
  const theme = async name => { await page.getByRole("button", { name: /^(테마|Theme)$/, exact: true }).click(); await page.getByRole("button", { name, exact: true }).click(); };
  await ready();
  assert.deepEqual(await tabs().getByRole("tab").allTextContents(), ["분석 결과", "실행", "보고서"]);
  assert.equal(await reads("rawEvent"), 1); assert.equal(await reads("agentRuns"), 0);
  await page.locator('.test-input-fields .tabulator-row').first().waitFor();
  assert.equal(await page.locator('.test-input-fields .tabulator-row').count(), 6);
  assert.equal(await page.locator('.test-payload-panel').evaluate(node => node.getBoundingClientRect().top < node.closest('.test-analysis-pane').getBoundingClientRect().bottom - 100), true);
  await snap("test-result-overview-ko-sk-1440");
  assert.equal(await page.locator(".test-raw-payload code").textContent(), await page.evaluate(() => fixture.workspaceRaw));
  const evidence = page.locator(".test-results-pane .decision-core-evidence");
  await evidence.first().focus(); await page.keyboard.press("Enter");
  await page.locator('.test-raw-payload [data-source-highlight="true"]').waitFor();
  assert.equal(await page.locator('.test-raw-payload [data-source-highlight="true"]').textContent(), "repeated-canary");
  assert.equal(await page.locator(".test-raw-payload").evaluate(node => { const mark=node.querySelector('mark');return node.textContent.indexOf('repeated-canary') < node.textContent.indexOf('repeated-canary',node.textContent.indexOf('repeated-canary')+1) && mark.previousSibling.textContent.includes('X-Note: repeated-canary\r\n'); }), true);
  assert.equal(await evidence.first().evaluate(node => node === document.activeElement), true);
  await evidence.nth(1).focus(); await page.keyboard.press("Space");
  await page.locator(".test-input-field-selected mark").waitFor();
  assert.equal(await page.locator(".test-input-field-selected mark").textContent(), "synthetic signature");
  assert.equal(await page.locator(".test-raw-payload mark").count(), 0);
  await evidence.nth(2).focus(); await page.keyboard.press("Enter");
  assert.equal(await page.locator(".test-input-pane mark").count(), 0);
  await page.getByText("정확한 위치를 확인하지 못해 강조하지 않습니다.", { exact: false }).waitFor();
  await evidence.nth(3).focus(); await page.keyboard.press("Enter");
  await page.locator(".test-input-field-selected mark").waitFor();
  assert.equal(await page.locator(".test-input-field-selected mark").textContent(), "0");
  assert.equal(await page.getByRole("button", { name: "전체 입력 필드 접기", exact: true }).getAttribute("aria-expanded"), "true");
  await page.waitForFunction(() => document.querySelectorAll('.test-input-fields .tabulator-row').length > 6);
  await page.getByRole("button", { name: "전체 입력 필드 접기", exact: true }).click();
  await page.waitForFunction(() => document.querySelectorAll('.test-input-fields .tabulator-row').length === 6);
  assert.equal(await page.evaluate(() => window.injected), undefined);
  const independent = await page.evaluate(() => { const left=document.querySelector('.test-results-pane'),right=document.querySelector('.test-input-pane');left.scrollTop=0;right.scrollTop=100;const before=right.scrollTop;left.scrollTop=150;return {left:left.scrollTop,right:right.scrollTop,before,leftOverflow:left.scrollHeight>left.clientHeight,rightOverflow:right.scrollHeight>right.clientHeight}; });
  assert.ok(independent.left > 0 && independent.leftOverflow && independent.rightOverflow); assert.equal(independent.right, independent.before);
  await tab("실행"); await page.getByText("SAVED_PRIMARY_OUTPUT", { exact: true }).waitFor();
  assert.equal(await reads("agentRuns"), 1);
  const stepButtons = page.locator(".execution-navigation > button");
  await stepButtons.last().click();
  await page.locator(".execution-final-detail").waitFor();
  assert.match(page.url(), /step=result/);
  assert.equal(await reads("agentRuns"), 1, "Final JSON is a UI selection, not a model or step API call");
  await page.reload();
  await page.addStyleTag({ content: bundle.outputFiles.find(file => file.path.endsWith(".css")).text });
  await page.addScriptTag({ content: bundle.outputFiles.find(file => file.path.endsWith(".js")).text });
  await page.locator(".execution-final-detail").waitFor();
  assert.match(page.url(), /step=result/);
  await page.locator(".execution-navigation > button").first().click();
  await page.getByText("SAVED_PRIMARY_OUTPUT", { exact: true }).waitFor();
  await page.goBack(); await page.locator(".execution-final-detail").waitFor();
  await page.goBack(); await page.getByText("SAVED_PRIMARY_OUTPUT", { exact: true }).waitFor();
  await stepButtons.first().focus(); await page.keyboard.press("ArrowDown"); await page.keyboard.press("Enter");
  await page.getByText("SAVED_VERIFIER_OUTPUT", { exact: true }).waitFor();
  assert.match(page.url(), new RegExp(`step=${secondStep}`));
  await page.getByRole("tablist", { name: "단계 자료", exact: true }).getByRole("tab", { name: "분석 입력", exact: true }).click();
  await page.getByText("SAVED_VERIFIER_INPUT", { exact: true }).waitFor(); assert.match(page.url(), /view=input/);
  await page.getByRole("tablist", { name: "단계 자료", exact: true }).getByRole("tab", { name: "실행 정보", exact: true }).click();
  await page.getByRole("button", { name: "tool_calls 펼치기", exact: true }).click();
  await page.getByRole("button", { name: "0 펼치기", exact: true }).click();
  await page.getByText('"SAVED_TOOL_RECORD"', { exact: true }).waitFor(); assert.match(page.url(), /view=metadata/);
  await page.goBack(); await page.getByText("SAVED_VERIFIER_INPUT", { exact: true }).waitFor();
  await page.goBack(); await page.getByText("SAVED_VERIFIER_OUTPUT", { exact: true }).waitFor();
  await page.goBack(); await page.getByText("SAVED_PRIMARY_OUTPUT", { exact: true }).waitFor();
  assert.doesNotMatch(page.url(), /[?&]step=/);
  for (const locale of ["ko", "en"]) {
    if (locale === "en") await page.getByRole("button", { name: "EN", exact: true }).click();
    for (const color of ["SK", "Light", "Dark"]) {
      await theme(color);
      for (const width of [1440, 390]) {
        await page.setViewportSize({ width, height: 960 });
        await tab(locale === "en" ? "Analysis result" : "분석 결과"); await ready();
        await evidence.first().focus(); await page.keyboard.press("Enter");
        await page.locator('.test-raw-payload [data-source-highlight="true"]').waitFor();
        await evidence.first().evaluate(node => { const pane=node.closest('.test-analysis-pane');pane.scrollTop += node.getBoundingClientRect().top-pane.getBoundingClientRect().top-70; });
        await snap(`test-result-${locale}-${color.toLowerCase()}-${width}`);
        await tab(locale === "en" ? "Execution" : "실행"); await page.locator(".execution-workspace").waitFor();
        if (width === 390) assert.ok(await page.locator('.inspection-run-toolbar select').evaluate(node => node.getBoundingClientRect().width > 200), "Mobile run selector remains usable");
        await snap(`test-execution-${locale}-${color.toLowerCase()}-${width}`);
      }
    }
  }
  await page.setViewportSize({ width: 1440, height: 960 });
  const rawBefore = await reads("rawEvent"); await go(`#operate/inference/${prod}/result`);
  await page.locator(".decision-layout").waitFor();
  assert.equal(await tabs().getByRole("tab").count(), 4); assert.equal(await reads("rawEvent"), rawBefore);
  await tab("Evidence & input"); await page.locator(".raw-event-view").waitFor(); assert.equal(await reads("rawEvent"), rawBefore + 1);
  await tab("Report"); await page.locator(".analysis-report").waitFor();
  assert.equal(await reads("rawEvent"), rawBefore + 1);
  await page.goBack(); await page.locator(".raw-event-view").waitFor();
  await page.goBack(); await page.locator(".decision-layout").waitFor();
  await page.goBack(); await page.getByText("SAVED_PRIMARY_OUTPUT", { exact: true }).waitFor();
  assert.match(page.url(), new RegExp(testId));
  const storage = await page.evaluate(() => JSON.stringify({ url: location.href, history: history.state, local: { ...localStorage }, session: { ...sessionStorage } }));
  assert.doesNotMatch(storage, /repeated-canary|unmasked|SAVED_PRIMARY|SAVED_VERIFIER/);
  assert.deepEqual(errors, []); assert.deepEqual(traffic, []); assert.deepEqual(await page.evaluate(() => fixture.unknown), []);
  if (existsSync(join(output, "failure.png"))) unlinkSync(join(output, "failure.png"));
  console.log(JSON.stringify({ result: "passed", screenshots: output, screenshotCount: screenshots.length, checks: ["test three tabs", "exact CRLF and Unicode", "verified repeated occurrence", "field-only and decoded safety", "independent scroll", "keyboard source and steps", "execution URL and browser Back", "Production four tabs and audit fetch", "1440/390 SK Light Dark Korean English"], actualApiCalls: 0, llmCalls: 0 }));
} catch (error) { if (page) await page.screenshot({ path: join(output, "failure.png"), fullPage: true }); throw error; }
finally { await browser.close(); }
