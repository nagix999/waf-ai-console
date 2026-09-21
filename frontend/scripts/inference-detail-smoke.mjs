// Synthetic in-memory API only. No service, LLM, database or external traffic.
// Reuse an installed development browser; never download a browser.
import assert from "node:assert/strict";
import { existsSync, readdirSync, mkdtempSync, readFileSync } from "node:fs";
import { homedir, tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { build } from "esbuild";

const source = fileURLToPath(new URL("../src", import.meta.url));
const archive = join(homedir(), ".cache/uv/archive-v0");
const driver = process.env.PLAYWRIGHT_MODULE || readdirSync(archive).map(name => join(archive, name, "playwright/driver/package/index.mjs")).find(existsSync);
if (!driver) throw Error("Existing development browser required");
const { chromium } = await import(pathToFileURL(driver).href);
const output = mkdtempSync(join(tmpdir(), "waf-inference-ui-"));
const id = "11111111-1111-4111-8111-111111111111", second = "22222222-2222-4222-8222-222222222222";
const shared = JSON.parse(readFileSync(new URL("../../backend/tests/fixtures/analyst_assessment_cases.json", import.meta.url), "utf8"))[0].result;
const mock = `
window.fixture={reads:[],writes:[],downloads:[],unknown:[],rawError:false,holdRaw:false,holdAgent:false};const state=window.fixture;
const result={...${JSON.stringify(shared)},verdict:'inconclusive',schema_version:'waf-analysis-v2',summary_ko:'인증 파라미터의 허용 형식을 확인해야 합니다.',
 diagnostics:{inconclusive_reasons:['verdict_disagreement']},threat_analysis:{severity:'HIGH',category:'SQL Injection',target:'payload.body',technique_ko:'인증 요청의 값과 처리 규칙을 확인합니다.',potential_impact_ko:'입력 검증 여부에 따라 다릅니다.'},
 analyst_guidance:{summary_ko:'인증 파라미터의 허용 형식을 확인해야 합니다.',checks:[{source_ko:'인증 서비스',check_ko:'파라미터 허용 형식을 확인하세요.',why_ko:'요청의 용도를 구분하기 위해 필요합니다.'}],limitations:[]},
 agent:{framework:'moduagent',role_profiles:{primary:{model_profile:'저장된 1차 모델'},verifier:{model_profile:'저장된 검증 모델'}}},verifier:{executed:true}};
result.analyst_assessment.evidence.push({evidence_id:'missing',field:'payload.body',excerpt:'missing-quote-canary',interpretation_ko:'이전 입력의 표현을 확인해야 합니다.',supports:'context'});
const base={id:'${id}',event_id:'hidden-event-canary',status:'completed',analysis_purpose:'test',ingest_channel:'test_lab',company_name:'가상 테스트 조직',src_ip:'192.0.2.10',dest_ip:'198.51.100.20',dest_port:443,src_port:45678,waf_action:'D',waf_vendor:'fixture',signature:'인증 요청 탐지',source_system:'fixture',model_profile:'저장된 1차 모델',prompt_version:'지침 v12',input_schema_metadata:{version_number:3,field_count:11},total_elapsed_ms:1500,queue_wait_ms:50,processing_duration_ms:1450,created_at:'2026-09-17T00:00:00Z',result,evaluation:{outcome:'unlabeled'}};
state.detail=base;
const raw=key=>({analysis_id:key,event_id:'hidden-event-canary',payload:key==='${id}'?'POST /auth HTTP/1.1\\r\\nHost: fixture.invalid\\r\\nCookie: private-fixture-cookie\\r\\n\\r\\ncode_verifier=fixture&encoded=%3Cscript%3E':'SECOND-INPUT-CANARY',extra_fields:{vendor_note:'<script>window.injected=true</script>'},decoding:{decoder_version:'fixture',items:[],warnings:[],scan_truncated:false}});
const runs=[{id:'hidden-run-canary',status:'completed',started_at:base.created_at,completed_at:base.created_at,duration_ms:1450,steps:[
 {id:'step-1',step_type:'llm_primary',status:'completed',duration_ms:700,output:{verdict:'inconclusive',nested:{number:3,flag:true}},input:{payload:'trace-input-canary'},metadata:{model_profile:'저장된 1차 모델',model_name:'fixture-model',output_validation_retry:{attempt_count:3},evidence_grounding_retry:{attempted:true,attempt_count:2,recovered:true,attempts:[{output_validation_retry:{attempt_count:3}},{output_validation_retry:{attempt_count:1}}]}}},
 {id:'step-2',step_type:'llm_verifier',status:'completed',duration_ms:600,output:{verdict:'inconclusive'},metadata:{model_profile:'저장된 검증 모델'}}]}];
const methods={me:async()=>({username:'fixture-admin',kind:'admin_session'}),dashboard:async()=>({counts:{pending:0,processing:0},runtime:{agent_mode:'moduagent'}}),
 analysis:async key=>key==='${id}'?structuredClone(state.detail):{...base,id:key,status:'failed',result:null,error_code:'primary_agent_failed'},
 evaluationLabels:async()=>({items:[]}),
 rawEvent:async key=>{if(state.rawError)throw Error('private-upstream-error');if(state.holdRaw)return new Promise(resolve=>{state.resolveRaw=()=>resolve(raw(key));});return raw(key);},
 agentRuns:async()=>{if(state.holdAgent)return new Promise(resolve=>{state.resolveAgent=()=>resolve(runs);});return runs;},
 referenceSelection:async ids=>({items:ids.map(analysis_id=>({analysis_id,expected_revision:0,verdict:null}))}),
 saveReferences:async body=>{state.writes.push(body);state.detail.evaluation={outcome:'expected_abstention_match',reference_label:{verdict:body.verdict,source_kind:'reference',ai_visible:true,revision:1}};return {applied_count:1};},
 validationDatasets:async()=>({items:[{id:'dataset-1',name:'검증용 데이터',revision:1,total:0}],total:1}),
 retryEligibility:async key=>({analysis_id:key,allowed:true,model_profile:'저장된 1차 모델',prompt_version:'지침 v12',provider:'vllm'}),
};
export const api=new Proxy(methods,{get(target,name){if(!target[name])return async()=>{state.unknown.push(name);throw Error('Unexpected mock API');};return async(...args)=>{state.reads.push({name,id:args[0]});return target[name](...args);};}});
window.fetch=async(path,options)=>{state.downloads.push({path,options});if(!/^\\/api\\/v1\\/analyses\\/[0-9a-f-]+\\/report\\.(pdf|xlsx)/.test(path))throw Error('Unexpected fetch');return new Response(path.includes('.pdf')?'%PDF-fixture':'PK-fixture',{headers:{'Content-Type':path.includes('.pdf')?'application/pdf':'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'}});};
`;
const bundle = await build({ stdin: { contents: `import {createRoot} from 'react-dom/client';import App from './App.jsx';import './styles.css';import './console.css';import './lifecycle.css';createRoot(document.getElementById('root')).render(<App/>);`, loader: "jsx", resolveDir: source },
  plugins: [{ name: "offline-api", setup(b) { b.onLoad({ filter: /\/api\.js$/ }, () => ({ contents: mock, loader: "js" })); } }],
  bundle: true, write: false, outfile: join(output, "bundle.js"), platform: "browser", format: "iife", jsx: "automatic", define: { "process.env.NODE_ENV": '"production"' }, loader: { ".md": "text", ".woff2": "dataurl" }, logLevel: "silent" });
const browser = await chromium.launch({ executablePath: process.env.CHROMIUM_PATH || join(homedir(), ".cache/ms-playwright/chromium-1208/chrome-linux64/chrome"), headless: true, args: ["--no-sandbox"] });
let page;
try {
  page = await browser.newPage({ viewport: { width: 1440, height: 900 } }); page.setDefaultTimeout(10000);
  const errors = [], blocked = [];
  page.on("pageerror", error => { errors.push(error.message); console.error('Fixture browser error:', error.stack); });
  await page.route("**/*", route => route.request().url().split('#')[0] === "https://fixture.invalid/" ? route.fulfill({ contentType: "text/html", body: '<html lang="ko"><body><div id="root"></div></body></html>' }) : (blocked.push(route.request().url()), route.abort()));
  await page.goto(`https://fixture.invalid/#analyses/${id}`);
  await page.addStyleTag({ content: bundle.outputFiles.find(file => file.path.endsWith(".css")).text });
  await page.addScriptTag({ content: bundle.outputFiles.find(file => file.path.endsWith(".js")).text });
  const tabs = page.getByRole("tablist", { name: "분석 상세", exact: true });
  async function tab(name) { await tabs.getByRole("tab", { name, exact: true }).click(); }
  const reads = name => page.evaluate(name => fixture.reads.filter(call => call.name === name).length, name);
  async function noOverflow() { assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true); }
  await tabs.waitFor(); await noOverflow();
  assert.equal(await reads('rawEvent'), 0); assert.equal(await reads('agentRuns'), 0);
  assert.equal(await page.getByText('hidden-event-canary', { exact: true }).count(), 0);
  assert.equal(await tabs.getByRole('tab').count(), 5);
  // Arrow focus alone must not request audited resources.
  await tabs.getByRole('tab', { name: '판정 결과', exact: true }).focus();
  await page.keyboard.press('ArrowRight'); await page.keyboard.press('ArrowRight');
  assert.equal(await page.evaluate(() => document.activeElement.textContent), '입력');
  assert.equal(await reads('rawEvent'), 0); assert.equal(await reads('agentRuns'), 0);
  await tab('보고서'); await page.getByRole('button', { name: 'Markdown 원본', exact: true }).click();
  await page.getByRole('checkbox', { name: '평가·실행 부록 포함' }).check();
  assert.equal(await reads('rawEvent'), 0);
  await page.getByRole('button', { name: '보고서 다운로드', exact: true }).click();
  assert.equal(await page.getByRole('button', { name: 'Excel 다운로드', exact: true }).count(), 1);
  const downloading = page.waitForEvent('download');
  await page.getByRole('button', { name: 'PDF 다운로드', exact: true }).click(); await downloading;
  const download = await page.evaluate(() => fixture.downloads[0]);
  assert.match(download.path, /include_appendix=true/); assert.doesNotMatch(download.path, /include_decoding=true/);
  assert.equal(download.options.method, 'GET'); assert.equal(download.options.credentials, 'include');
  await tab('결과 JSON'); await page.getByRole('region', { name: '결과 JSON 열람' }).waitFor();
  assert.equal(await reads('rawEvent'), 0); assert.equal(await reads('agentRuns'), 0);
  await page.goBack(); assert.equal(await tabs.getByRole('tab', { name: '보고서', exact: true }).getAttribute('aria-selected'), 'true');
  assert.equal(await page.getByRole('button', { name: 'Markdown 원본', exact: true }).getAttribute('aria-pressed'), 'true');
  await tab('판정 결과'); await page.getByRole('button', { name: '입력에서 보기', exact: true }).first().click();
  await page.locator('mark:visible').waitFor(); assert.equal(await page.locator('mark:visible').innerText(), 'code_verifier=fixture');
  assert.equal(await page.evaluate(() => document.activeElement.textContent), '입력');
  assert.equal(await reads('rawEvent'), 1);
  assert.equal(await page.getByLabel('HTTP 원문에서 찾기', { exact: true }).inputValue(), 'code_verifier=fixture');
  const stored = await page.evaluate(() => JSON.stringify({url:location.href,history:history.state,local:{...localStorage},session:{...sessionStorage}}));
  assert.doesNotMatch(stored, /code_verifier|private-fixture-cookie|missing-quote/);
  await tab('판정 결과'); await page.locator('.evidence-context article').filter({hasText:'WAF가 요청을 차단'}).getByRole('button',{name:'입력에서 보기'}).click();
  await page.getByRole('tab',{name:'입력 필드',exact:true}).waitFor();
  await page.waitForFunction(() => document.querySelector('.input-fields-grid mark')?.textContent === 'D');
  assert.equal(await reads('rawEvent'), 1);
  await page.getByRole('tablist',{name:'요청 자료',exact:true}).getByRole('tab',{name:'HTTP 원문',exact:true}).click();
  assert.equal(await page.getByLabel('HTTP 원문에서 찾기',{exact:true}).inputValue(),'');
  await tab('판정 결과'); await page.locator('.evidence-item').filter({hasText:'missing-quote-canary'}).getByRole('button',{name:'입력에서 보기'}).click();
  await page.getByText('이 필드에서 발췌문과 같은 문구를 찾지 못했습니다.', {exact:false}).waitFor();
  assert.equal(await page.locator('mark:visible').count(), 0);
  await page.getByRole('tab',{name:'인코딩·난독화',exact:true}).click();
  await page.getByText('표시할 변환 후보가 없습니다.',{exact:false}).waitFor();
  assert.equal(await reads('rawEvent'), 1);
  await tab('보고서');
  for (const format of ['PDF', 'Excel']) {
    await page.getByRole('button',{name:'보고서 다운로드',exact:true}).click();
    const saving = page.waitForEvent('download');
    await page.getByRole('button',{name:format+' 다운로드',exact:true}).click(); await saving;
  }
  const files = await page.evaluate(()=>fixture.downloads);
  assert.match(files[1].path, /include_decoding=true/); assert.match(files[2].path, /report\.xlsx$/);
  assert.equal(await reads('rawEvent'), 1);
  await tab('Agent 실행 이력'); await page.getByRole('button',{name:'실행 식별자',exact:true}).waitFor();
  assert.equal(await reads('agentRuns'), 1); assert.match(await page.locator('.inspection-repair-summary').innerText(), /출력 형식 재시도\s*2회/);
  await page.getByRole('tablist',{name:'단계 자료',exact:true}).getByRole('tab',{name:'처리 결과',exact:true}).focus();
  await page.keyboard.press('ArrowRight'); await page.keyboard.press('Enter');
  await page.getByText('trace-input-canary',{exact:false}).waitFor();
  await page.keyboard.press('End'); await page.keyboard.press('Enter');
  await page.getByRole('region',{name:'단계 실행 정보 열람'}).waitFor();
  await page.getByRole('button',{name:/추가 검증.*완료/}).click();
  await page.getByRole('button',{name:'테마',exact:true}).click(); await page.getByRole('button',{name:'Dark',exact:true}).click();
  assert.match(await page.locator('.inspection-step-detail h3').innerText(), /추가 검증/);
  await page.screenshot({path:join(output,'trace-ko-dark.png'),fullPage:true});
  assert.equal(await page.locator('.inspection-steps').evaluate(el=>el.getBoundingClientRect().width),310);
  assert.equal(await page.locator('.inspection-step-detail').evaluate(el=>getComputedStyle(el).backgroundColor),await page.locator('.inference-execution').evaluate(el=>getComputedStyle(el).backgroundColor));
  await tab('결과 JSON'); await tab('Agent 실행 이력');
  assert.match(await page.locator('.inspection-step-detail h3').innerText(), /추가 검증/);
  await page.getByRole('button',{name:'실행 식별자',exact:true}).click(); await page.getByRole('dialog',{name:'실행 식별자',exact:true}).waitFor();
  await page.keyboard.press('Escape'); assert.equal(await page.getByRole('dialog').count(),0);
  await tab('판정 결과');
  for (const theme of ['Light','SK']) {
    await page.getByRole('button',{name:'테마',exact:true}).click(); await page.getByRole('button',{name:theme,exact:true}).click();
    await page.screenshot({path:join(output,`result-ko-${theme.toLowerCase()}.png`),fullPage:true}); await noOverflow();
  }
  await page.getByRole('button',{name:'답안·데이터 관리',exact:true}).click(); await page.getByRole('button',{name:'참고 답안 입력',exact:true}).click();
  const reference = page.getByRole('dialog',{name:'참고 답안 입력',exact:true}); await reference.waitFor();
  await reference.getByRole('combobox',{name:'참고 답안',exact:true}).selectOption('inconclusive');
  await reference.getByLabel('메모', {exact:true}).fill('fixture-only-comment');
  await reference.getByRole('button',{name:'답안 저장',exact:true}).click();
  await page.getByText('1건의 참고 답안을 저장했습니다.',{exact:true}).waitFor();
  await page.locator('.compact-evaluation-detail').waitFor();
  assert.equal(await page.evaluate(()=>fixture.writes.length),1);
  await page.getByRole('button',{name:'답안·데이터 관리',exact:true}).click(); await page.getByRole('button',{name:'데이터셋에 추가',exact:true}).click();
  await page.getByRole('dialog',{name:'데이터셋에 추가',exact:true}).waitFor(); await page.keyboard.press('Escape');
  await page.setViewportSize({width:390,height:844}); await noOverflow(); await page.screenshot({path:join(output,'result-ko-mobile.png'),fullPage:true});
  await tab('Agent 실행 이력'); await noOverflow(); await page.screenshot({path:join(output,'trace-ko-mobile.png'),fullPage:true});
  await page.setViewportSize({width:1440,height:900});
  await page.getByRole('button',{name:'EN',exact:true}).click();
  await page.getByRole('tab',{name:'Result JSON',exact:true}).waitFor(); await page.screenshot({path:join(output,'trace-en-light.png'),fullPage:true});
  await page.getByRole('button',{name:'KR',exact:true}).click();
  // Navigate to another analysis while the previous raw request is pending.
  await page.evaluate(key=>{fixture.holdRaw=true;location.hash='#analyses/'+key+'/raw';},second);
  await page.waitForFunction(()=>typeof fixture.resolveRaw==='function');
  await page.evaluate(key=>{fixture.holdRaw=false;location.hash='#analyses/'+key;},id);
  await tabs.waitFor(); await page.getByText('저장된 1차 모델',{exact:true}).first().waitFor();
  await page.evaluate(()=>fixture.resolveRaw());
  await page.evaluate(()=>{fixture.rawError=true;}); await tab('입력');
  await page.getByText('입력을 불러오지 못했습니다. 다시 조회해 주세요.',{exact:true}).waitFor();
  assert.doesNotMatch(await page.locator('body').innerText(), /SECOND-INPUT-CANARY|private-upstream-error/);
  await page.evaluate(()=>{fixture.rawError=false;}); await page.getByRole('button',{name:'다시 조회',exact:true}).click();
  await page.getByRole('region',{name:'HTTP 원문 열람'}).waitFor();
  assert.equal(await page.evaluate(()=>window.injected),undefined);
  await page.evaluate(key=>{location.hash='#analyses/'+key;},second);
  await page.getByRole('button',{name:'재실행',exact:true}).click();
  const retry = page.getByRole('dialog',{name:'실패한 분석 재실행',exact:true}); await retry.getByText('저장된 1차 모델',{exact:true}).waitFor();
  assert.equal(await retry.getByRole('button',{name:'재실행 시작',exact:true}).isDisabled(),true);
  await page.keyboard.press('Escape');
  assert.deepEqual(errors,[]); assert.deepEqual(blocked,[]); assert.deepEqual(await page.evaluate(()=>fixture.unknown),[]);
  console.log(JSON.stringify({result:'passed',screenshots:output,checked:['five tabs and Back','audit reads only on activation','literal evidence jumps and missing excerpts','no source data in URL/storage','recorded metadata and repair counts','trace selection retained','PDF options and reference save','dataset and retry dialogs','3 themes / EN headings / 390px','late raw response and sanitized errors','no real API or LLM calls']}));
} catch (error) {
  if(page) await page.screenshot({path:join(output,'failure.png'),fullPage:true});
  console.error('Screenshot directory:',output); throw error;
} finally { await browser.close(); }
