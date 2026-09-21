// Desktop-only regression with in-bundle fixtures. No server, DB or LLM calls.
import assert from "node:assert/strict";
import { existsSync, readdirSync, mkdtempSync } from "node:fs";
import { homedir, tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { build } from "esbuild";

const source = fileURLToPath(new URL("../src", import.meta.url));
const archive = join(homedir(), ".cache/uv/archive-v0");
const driver = process.env.PLAYWRIGHT_MODULE || readdirSync(archive).map(name => join(archive, name, "playwright/driver/package/index.mjs")).find(existsSync);
if (!driver) throw Error("An existing development browser is required; no download is attempted.");
const { chromium } = await import(pathToFileURL(driver).href);
const output = mkdtempSync(join(tmpdir(), "waf-desktop-workspaces-"));
const id = n => `${String(n).padStart(8, "0")}-1111-4111-8111-111111111111`;
const mock = `
const id=n=>String(n).padStart(8,'0')+'-1111-4111-8111-111111111111', date='2026-09-17T00:00:00Z', hash='a'.repeat(64);
window.fixture={reads:[],writes:[],unknown:[],failRead:'',incompatible:false};const state=window.fixture;
const profiles=[1,2].map(n=>({id:id(n),name:n===1?'운영 모델':'후보 모델',provider:'vllm',model_name:'fixture-model-'+n,base_url:'http://10.1.2.3:8000/v1',context_window:32768,max_output_tokens:4096,test_concurrency:1,timeout_seconds:120,tls_verify:true,status:n===1?'production':'verified',can_assign:true,profile_fingerprint:hash,has_api_key:true,agent_roles:n===1?['production.verifier']:[]}));
const role={primary_profile_id:id(1),verifier_profile_id:null,evidence_editor_profile_id:null,evidence_editor_enabled:false};
const agents={state_token:hash,profiles,assignments:{production:{...role},test:{...role}}};
const fields=[{name:'event_id',description:'이벤트 식별자',type:'string',required:true,nullable:false,min_length:1,max_length:255},{name:'payload',description:'HTTP 원문',type:'string',required:true,nullable:false,min_length:1},{name:'waf_action',description:'WAF 조치',type:'string',required:true,nullable:false,enum:['D','A']}];
const policies=[3,4].map(n=>({id:id(n),name:'지침 '+(n-2),version_number:n-2,policy_text:'관측한 원문에서만 근거를 찾으세요.',change_note:'예시 변경 설명',created_at:date,created_by:'fixture',content_hash:hash,parent_version_id:n===4?id(3):null}));
const schemas=[5,6].map(n=>({id:id(n),name:'스키마 '+(n-4),version_number:n-4,fields,change_note:'예시 필드 정의',created_at:date,created_by:'fixture',content_hash:hash,parent_id:n===6?id(5):null}));
const evaluation={total:3,labeled:3,evaluable:3,matches:2,binary_evaluable:3,binary_decided:2,support_positive:2,support_negative:1,label_coverage:1,metrics:{accuracy:1,precision:1,recall:1,f1:1,coverage:2/3,abstention_rate:1/3},outcomes:{match:2,abstained:1},confusion_matrix:{tp:1,tn:1,fp:0,fn:0,abstained_positive:1,abstained_negative:0},source_groups:[]};
const item={id:id(21),item_id:id(20),revision:2,review_status:'approved',reference_verdict:'true_positive',case_name:'SQLi 경계 사례',comment:'쿼리 변경 구문 확인',difficulty:'hard',test_category:'SQLi',created_at:date,created_by:'fixture',event:{event_id:'fixture-event',company_name:'예시 회사',src_ip:'192.0.2.1',dest_ip:'198.51.100.1',src_port:1234,dest_port:443,waf_vendor:'fixture',waf_action:'D',payload:'GET /?q=1 HTTP/1.1\\r\\nHost: example.test\\r\\n\\r\\n'}};
const dataset={id:id(7),name:'WAF 회귀 검증',description:'승인된 공격·정상 요청 예시',total:1,labeled:1,revision:2,current_revision:2,review_counts:{draft:0,reviewed:0,approved:1},items:[item],versions:[{id:'rev2',revision:2,created_at:date}]};
const runs=[8,9].map(n=>({id:id(n),name:n===8?'후보 모델 검증':'기준 모델 검증',kind:'dataset',status:'completed',evaluation_mode:'ground_truth',execution_mode:'moduagent',dataset_version_id:'rev2',ground_truth:{dataset_name:dataset.name,dataset_revision:2,approved_count:3,excluded_count:1,membership_hash:hash,metrics_version:'v1'},total:3,accepted:3,rejected:0,completed:3,failed:0,pending:0,processing:0,total_elapsed_ms:3600,created_at:date,prompt_version:'v1',profile_metadata:{model_name:'fixture-model-'+(n===8?2:1),verifier_profile:{model_name:'fixture-verifier'}},evaluation_summary:evaluation,source_system:'fixture'}));
const cases=[1,2,3].map(n=>({id:id(30+n),analysis_id:id(40+n),row_number:n,case_name:'예시 요청 '+n,difficulty:'hard',test_category:'SQLi',status:'completed',ingest_status:'accepted',verdict:n===3?'inconclusive':n===2?'false_positive':'true_positive',summary_ko:'원문에서 확인한 구문을 바탕으로 판단했습니다.',evaluation:{outcome:'match',reference_label:{verdict:'true_positive'}}}));
const keys=[{id:id(10),name:'운영 수집기',purpose:'production',source_system:'fixture-collector',scopes:['ingest'],created_at:date,last_used_at:null,revoked_at:null,key_prefix:'fixture-only'}];
const performance={processing_ms:{count:3,missing_count:0,mean_ms:1000,p50_ms:900,p95_ms:1200},llm_step_ms:{count:3,missing_count:0,sum_ms:3000},tokens:{},output_repair_steps:0,missing_agent_histories:0};
const methods={
 me:async()=>({username:'fixture-admin',kind:'admin_session',scopes:['admin']}),
 dashboard:async()=>({counts:{total:3,pending:0,processing:0,completed:3,failed:0},runtime:{agent_mode:'moduagent',production_profile:profiles[0]},evaluation_summary:evaluation,trend:[]}),
 modelProfiles:async()=>profiles,modelProfileTests:async profile=>[{id:id(12),profile_id:profile,status:'completed',mode:'full',completed_at:date,checks:[{name:'structured_output',status:'passed',latency_ms:120}],include_dataset:false}],
 agentSettings:async()=>agents,
 promptPolicies:async()=>({items:policies,active_version_id:id(3),revision:1,max_policy_chars:4000,fixed_rules_version:'fixture-v1',fixed_instructions:'읽기 전용 안전 규칙'}),promptPolicy:async value=>policies.find(p=>p.id===value),
 inputSchemas:async()=>({items:schemas,active_version_id:id(5),revision:1,default_fields:fields}),inputSchema:async value=>schemas.find(s=>s.id===value),inputSchemaHistory:async()=>({items:[]}),
 concurrencySettings:async()=>({state_token:hash,production:3,test:2,servers:[{server_key:'vllm:10.1.2.3:8000',max_calls:4,profiles}],active:{production:1,test:0}}),
 internalEgress:async()=>[{id:id(11),ip_address:'10.1.2.3',port:8000,description:'운영 추론 서버',revision:1,in_use_profiles:[profiles[0]]}],serviceApiKeys:async()=>({items:keys}),serviceApiKeyDeletionPreview:async()=>({name:keys[0].name,analyses:3,active_analyses:0,dataset_copies:1,scope:"fixture-scope"}),
 analyses:async()=>({items:[],total:0,evaluation_summary:evaluation}),testRuns:async()=>({items:runs,total:2}),
 testRun:async value=>({...runs.find(r=>r.id===value),items:cases,total_items:3,facets:{difficulties:['hard'],test_categories:['SQLi']}}),testEvaluations:async()=>({items:[{id:id(13),revision:1,evaluation_kind:'ground_truth',created_at:date}]}),
 testRetryEligibility:async value=>({test_run_id:value,failed_count:0,eligible_count:0,eligible_ids:[],blocked_counts:{}}),
 compareTestRuns:async(candidate,q)=>({baseline:runs.find(r=>r.id===q.baseline_id),candidate:runs.find(r=>r.id===candidate),comparable:!state.incompatible,baseline_evaluation:evaluation,candidate_evaluation:evaluation,performance:{baseline:performance,candidate:performance},counts:{accepted_pairs:3,comparable_pairs:3,changed:1,improved:1,regressed:0,exclusions:{}},items:[],total_items:0,warnings:['comparison_is_not_causal_proof']}),
 validationDatasets:async()=>({items:[dataset],total:1}),searchValidationDatasets:async()=>({items:[dataset],total:1}),validationDataset:async()=>dataset,datasetItem:async(_,__,version)=>({...item,...(version==='old'?{id:'old',revision:1,review_status:'draft'}:{}),history:[item,{...item,id:'old',revision:1,review_status:'draft'}]}),
 agentDiagnostics:async()=>({counts:{total:3,completed:3,measured:3,inconclusive:1,failed:0,primary_repair_attempted:0,primary_repair_recovered:0,verifier_repair_attempted:0,verifier_repair_recovered:0,parser_incomplete:0,input_truncated:0},inconclusive_rate:1/3,unmeasured_completed:0,llm_failure_counts:{}}),
 productionApi:async()=>({markdown:'# Production API\\n\\n스키마 기준 문서입니다.\\n\\n## 인증\\n\\nX-API-Key를 사용합니다.\\n\\n## 요청 입력\\n\\n필수 필드입니다.\\n\\n## 응답 결과\\n\\n저장한 판정을 반환합니다.',input_schema:{version_id:id(5),version_number:1,content_hash:hash}}),
};
export const api=new Proxy(methods,{get(target,name){if(!target[name])return async()=>{state.unknown.push(name);throw Error('Unexpected fixture method '+name)};return async(...args)=>{state.reads.push({name,args});if(state.failRead===name)throw Error('PRIVATE_ERROR_MUST_NOT_RENDER');return target[name](...args)}}});
`;
const bundle = await build({ stdin: { contents: `import {createRoot} from 'react-dom/client';import App from './App.jsx';import './styles.css';import './console.css';createRoot(document.getElementById('root')).render(<App/>);`, loader: "jsx", resolveDir: source },
  plugins: [{ name: "offline-api", setup(b) { b.onLoad({ filter: /\/api\.js$/ }, () => ({ contents: mock, loader: "js" })); } }],
  bundle: true, write: false, outfile: join(output, "bundle.js"), platform: "browser", format: "iife", jsx: "automatic", define: { "process.env.NODE_ENV": '"production"' }, loader: { ".md": "text", ".woff2": "dataurl" }, logLevel: "silent" });
const browser = await chromium.launch({ executablePath: process.env.CHROMIUM_PATH || join(homedir(), ".cache/ms-playwright/chromium-1208/chrome-linux64/chrome"), headless: true, args: ["--no-sandbox"] });
let page;
try {
  page = await browser.newPage({ viewport: { width: 1440, height: 1000 } }); page.setDefaultTimeout(10000);
  const errors = [], traffic = [], checked = [];
  page.on("pageerror", error => { errors.push(error.message); console.error(error.message); });
  await page.route("**/*", route => route.request().url() === "https://fixture.invalid/" ? route.fulfill({ contentType: "text/html", body: '<html lang="ko"><body><div id="root"></div></body></html>' }) : (traffic.push(route.request().url()), route.abort()));
  await page.goto("https://fixture.invalid/");
  await page.addStyleTag({ content: bundle.outputFiles.find(file => file.path.endsWith(".css")).text });
  await page.addScriptTag({ content: bundle.outputFiles.find(file => file.path.endsWith(".js")).text });
  const go = async hash => { await page.evaluate(hash => { location.hash = hash; }, hash); await page.locator(".console-page-heading h1").waitFor(); };
  const close = async name => { const dialog = page.getByRole("dialog", { name, exact: true }); await dialog.getByRole("button", { name: `${name} 닫기`, exact: true }).click(); await dialog.waitFor({ state: "hidden" }); };
  const snap = async name => { await page.screenshot({ path: join(output, name + ".png"), fullPage: true }); };
  const theme = async name => { await page.getByRole("button", { name: "테마", exact: true }).click(); await page.getByRole("button", { name, exact: true }).click(); };
  await page.getByRole("heading", { name: "현재 운영 설정", exact: true }).waitFor();
  for (const color of ["Light", "SK", "Dark"]) {
    await theme(color);
    for (const [route, ready] of [["#settings/models","모델 목록"],["#settings/agents","역할별 모델"],["#settings/instructions","공통 판정 지침"],["#settings/schema","스키마 1"],["#datasets","WAF 회귀 검증"],["#settings/keys","운영 수집기"],["#settings/egress","운영 추론 서버"],["#production-api","운영 API 정의서"]]) {
      await go(route); await page.getByText(ready, { exact: false }).first().waitFor();
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true, color + route);
      await snap(color + route.replaceAll(/[\/#]/g, "-"));
    }
  }
  await theme("Light");
  await go("#test-runs/" + id(8)); await page.getByRole("heading", { name: "후보 모델 검증", exact: true }).waitFor();
  assert.equal(await page.getByRole("tab", { name: "문항별 결과", exact: true }).getAttribute("aria-selected"), "true");
  await page.getByRole("tab", { name: "평가 지표", exact: true }).click(); await page.getByRole("heading", { name: "공식 평가 지표", exact: true }).waitFor();
  await page.getByRole("button", { name: "평가 상세", exact: true }).click();
  const metric = page.getByRole("dialog", { name: "평가 상세", exact: true }); assert.equal(await metric.locator(".quality-matrix td").count(), 9);
  await metric.getByRole("button", { name: /TP.*문항 보기/ }).click();
  await page.getByRole("tab", { name: "문항별 결과", exact: true }).waitFor(); await page.getByText("선택한 셀:", { exact: false }).waitFor();
  await page.getByRole("button", { name: "다른 테스트와 비교", exact: true }).click();
  const compare = page.getByRole("dialog", { name: "테스트 비교", exact: true }); await compare.getByRole("button", { name: /기준 모델 검증/ }).click();
  await compare.locator('.comparison-metric-cards').waitFor();
  assert.equal(await compare.locator('.comparison-metric-cards article').count(),6);
  assert.equal(await compare.locator('.comparison-metric-cards article').evaluateAll(cards=>cards.every(card=>{const bounds=card.getBoundingClientRect();return [...card.querySelectorAll(':scope > div > *')].every(value=>{const box=value.getBoundingClientRect();return box.left>=bounds.left&&box.right<=bounds.right;});})),true);
  assert.equal(await compare.locator('.comparison-picker').count(),0);
  assert.equal(await compare.evaluate(el=>Math.round(el.getBoundingClientRect().width)),1260);
  await snap('comparison-quality');
  await compare.getByRole('button',{name:'비교 기준 변경',exact:true}).click();
  assert.equal(await compare.getByRole('button',{name:/기준 모델 검증/}).getAttribute('aria-pressed'),'true');
  await compare.getByRole('button',{name:'선택 닫기',exact:true}).click();
  await compare.getByRole("tab", { name: "시간·사용량", exact: true }).click(); await compare.getByRole("heading", { name: "처리 시간·토큰", exact: false }).waitFor();
  await snap("comparison-performance"); await page.evaluate(() => { window.fixture.incompatible = true; }); await compare.getByRole("button", { name: "새로고침", exact: true }).click(); await compare.getByText("평가 기준이 달라 점수 차이를 비교할 수 없습니다.", { exact: false }).waitFor(); assert.equal(await compare.getByRole("tab", { name: "판정 품질", exact: true }).count(), 0); await page.evaluate(() => { window.fixture.incompatible = false; }); await close("테스트 비교");
  await page.getByRole("button", { name: "실패 항목 모두 재실행", exact: true }).click();
  await page.getByText("지금 재실행할 수 있는 실패 항목이 없습니다.", { exact: false }).waitFor(); await close("실패 항목 모두 재실행");
  checked.push("test items/official metrics, nine-cell drilldown, comparison tabs, retry preview");

  await go("#datasets/" + id(7)); await page.getByRole("button", { name: "SQLi 경계 사례", exact: true }).click();
  const item = page.getByRole("dialog", { name: "검증 문항", exact: true }); await item.getByLabel("문항명", { exact: true }).fill("작성 중 이름");
  assert.deepEqual(await item.evaluate(el=>{const box=el.getBoundingClientRect();return {width:box.width,right:box.right,height:box.height};}),{width:460,right:1440,height:1000});
  await item.getByRole("tab", { name: "답안·검토", exact: true }).click(); assert.equal(await item.getByRole("button", { name: "미검토로 되돌리기", exact: true }).isDisabled(), true);
  await item.getByRole("tab", { name: "버전 이력", exact: true }).click(); assert.equal(await item.getByLabel("문항 버전", { exact: true }).isDisabled(), true);
  await item.getByRole("tab", { name: "입력", exact: true }).click(); assert.equal(await item.getByLabel("문항명", { exact: true }).inputValue(), "작성 중 이름");
  await item.getByLabel("문항명", { exact: true }).fill("SQLi 경계 사례"); await item.getByRole("tab", { name: "버전 이력", exact: true }).click(); await item.getByLabel("문항 버전", { exact: true }).selectOption("old");
  await item.getByText("이전 버전 · 읽기 전용", { exact: true }).waitFor(); await item.getByRole("tab", { name: "입력", exact: true }).click(); assert.equal(await item.getByLabel("문항명", { exact: true }).isDisabled(), true);
  await snap("ground-truth-history"); await close("검증 문항"); checked.push("Ground Truth tabs, dirty review/version locks, immutable history");

  await go("#settings/models"); await page.getByRole("button", { name: "전체 검증", exact: true }).first().click();
  const validation = page.getByRole("dialog", { name: "전체 검증 범위 선택", exact: true }); await validation.waitFor(); assert.equal(await validation.getByRole("radio").count(), 2); assert.equal(await validation.getByRole("radio").first().isChecked(), true);
  await validation.getByRole("radio").last().check(); await snap("validation-options"); await page.keyboard.press("Escape");
  await page.getByRole("button", { name: "후보 모델 관리", exact: true }).click(); await page.getByRole("button", { name: "검증 결과·연결정보", exact: true }).click();
  const management = page.getByRole("dialog", { name: "후보 모델 · 모델 관리", exact: true }); await management.waitFor(); await management.getByRole("button", { name: "확인 내용", exact: true }).click(); await snap("validation-check-detail"); await page.keyboard.press("Escape"); await close("후보 모델 · 모델 관리");
  checked.push("two-option explicit validation, profile overflow actions, validation detail");

  await go("#settings/instructions"); await page.getByRole("button", { name: "새 버전 작성", exact: true }).click();
  const prompt = page.getByRole("dialog", { name: "새 프롬프트 버전 작성", exact: true }); await prompt.getByLabel("변경 설명", { exact: false }).fill("PRIVATE_DRAFT_KEEP"); await close("새 프롬프트 버전 작성");
  await page.getByRole("button", { name: "작성 중인 버전 열기", exact: true }).click(); assert.equal(await prompt.getByLabel("변경 설명", { exact: false }).inputValue(), "PRIVATE_DRAFT_KEEP"); await close("새 프롬프트 버전 작성");
  await go("#settings/schema"); await page.getByRole("button", { name: "새 버전 작성", exact: true }).click();
  const schema = page.getByRole("dialog", { name: "새 스키마 버전 작성", exact: true }); await schema.getByLabel("버전 이름", { exact: true }).fill("PRIVATE_SCHEMA_DRAFT");
  await schema.getByRole("button", { name: /필드 편집$/ }).first().click(); const field = page.getByRole("dialog", { name: "필드 편집", exact: true }); await field.waitFor(); assert.equal(await field.getByLabel("필드명", { exact: true }).evaluate(el => el.readOnly), true); await page.keyboard.press("Escape"); await close("새 스키마 버전 작성");
  await page.getByRole("button", { name: "작성 중인 버전 열기", exact: true }).click(); assert.equal(await schema.getByLabel("버전 이름", { exact: true }).inputValue(), "PRIVATE_SCHEMA_DRAFT"); await close("새 스키마 버전 작성");
  checked.push("instruction/schema drafts retained, protected built-in field");

  await go("#settings/agents"); const production = page.locator("fieldset").filter({ has: page.locator("legend", { hasText: "프로덕션" }) }); await production.getByLabel("Primary · 1차 판정", { exact: true }).selectOption(id(2)); await page.getByRole("button", { name: "배정 저장", exact: true }).click();
  await page.getByRole("heading", { name: "변경 내용", exact: true }).waitFor(); await snap("assignment-diff"); await close("모델 배정 확인");
  await go("#settings/concurrency"); await page.getByLabel("최대 분석 수", { exact: true }).first().fill("4"); await page.getByRole("button", { name: "설정 저장", exact: true }).click(); await close("동시 처리 변경");
  await go("#settings/keys"); await page.getByRole("button", { name: "운영 수집기 관리", exact: true }).click(); await page.keyboard.press("Escape"); assert.equal(await page.getByRole("button", { name: "운영 수집기 관리", exact: true }).evaluate(el => el === document.activeElement), true);
  await page.getByRole("button", { name: "키 발급", exact: true }).click(); await page.getByRole("dialog", { name: "새 API 키 발급", exact: true }).getByLabel("키 이름", { exact: true }).fill("작성 중 키"); await close("새 API 키 발급");
  await page.getByRole("button", { name: "운영 수집기 관리", exact: true }).click(); await page.getByRole("button", { name: "운영 수집기 삭제", exact: true }).click(); const deleting = page.getByRole("dialog", { name: "API 키 삭제", exact: true }); await deleting.getByRole("radio", { name: "분석 보존", exact: true }).waitFor(); assert.equal(await deleting.getByRole("button", { name: "API 키 삭제", exact: true }).isDisabled(), true); await deleting.getByLabel("확인을 위해 키 이름을 입력하세요").fill("운영 수집기"); await deleting.getByRole("radio", { name: "분석도 함께 삭제", exact: true }).check(); await deleting.getByText("감사 이력과 검증 데이터셋 사본", { exact: false }).waitFor(); await close("API 키 삭제");
  await go("#settings/egress"); await page.getByRole("button", { name: "10.1.2.3:8000 관리", exact: true }).click(); assert.equal(await page.getByRole("button", { name: "10.1.2.3:8000 삭제", exact: true }).isDisabled(), true); await page.keyboard.press("Escape");
  checked.push("Production changes preserved, assignment diff, concurrency confirmation, key dialog, in-use endpoint lock, Escape focus");
  for (const width of [1280,1920]) {
    await page.setViewportSize({ width, height: 1000 }); await go("#settings/models"); await page.getByRole("heading", { name: "모델 목록", exact: true }).waitFor();
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
    const iconsFit = await page.locator(".more-actions > button").evaluateAll(buttons => buttons.length > 0 && buttons.every(button => {
      const icon = button.querySelector("svg")?.getBoundingClientRect(), rect = button.getBoundingClientRect();
      return icon && icon.width >= 18 && icon.left >= rect.left && icon.right <= rect.right && icon.top >= rect.top && icon.bottom <= rect.bottom;
    }));
    assert.equal(iconsFit, true, "overflow icons fit their buttons"); await snap("models-"+width);
  }
  const stored = await page.evaluate(() => JSON.stringify({url:location.href,history:history.state,local:{...localStorage},session:{...sessionStorage}})); assert.doesNotMatch(stored, /PRIVATE_DRAFT|PRIVATE_SCHEMA|작성 중 키/);
  assert.deepEqual(errors, []); assert.deepEqual(traffic, []); assert.deepEqual(await page.evaluate(() => window.fixture.unknown), []); assert.equal(await page.evaluate(() => window.fixture.writes.length), 0);
  console.log(JSON.stringify({result:"passed",checked,screenshots:output,actualApiCalls:0,llmCalls:0}));
} catch (error) { if (page) await page.screenshot({path:join(output,"failure.png"),fullPage:true}); console.error("Screenshots:",output); throw error; }
finally { await browser.close(); }
