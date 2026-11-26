// Actual React, offline synthetic APIs, all page families. Never calls a model,
// live service or DB. Reference screenshots are the approved local HTML only.
import assert from "node:assert/strict";
import { existsSync, readdirSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { homedir } from "node:os";
import { join, extname, resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { build } from "esbuild";
import { parserWorkbenchFixture } from "./parser-workbench-fixture.mjs";

const output = fileURLToPath(new URL("../../test-results/wireframe-alignment", import.meta.url));
mkdirSync(output, { recursive: true });
const run = "00000008-1111-4111-8111-111111111111";
const prod = "11111111-1111-4111-8111-111111111111";
const testCase = "00000041-1111-4111-8111-111111111111";
const mock = parserWorkbenchFixture().replace("export const api=", `
methods.analysisEvidenceMeta=()=>null;
const vSignature={id:id(901),version_no:1,name:'검토한 탐지 설명',entry_count:2,created_at:date,created_by:'fixture-admin',fingerprint:hash,entries:[{waf_vendor:'Example WAF',signature:'설정 파일 접근',signature_explanation:'서비스 용도와 요청 경로를 대조하기 위한 참고 설명입니다. 개별 요청의 판정 근거로 대신 사용하지 않습니다.'},{waf_vendor:'Example WAF',signature:'쿼리 변경 입력',signature_explanation:'원문에 입력된 구문과 처리 맥락을 함께 확인합니다.'}]};
methods.signatureExplanations=async()=>({items:[vSignature],total:1,active_version_id:null,revision:1});methods.signatureExplanation=async()=>vSignature;
const frow={id:id(1000),analysis_id:'${prod}',event_id:'SYNTHETIC-01',company_name:'검토용 회사',signature:'설정 파일 접근',analysis_purpose:'production',feedback_categories:['evidence_mismatch','explanation_error'],comment:'발췌문과 설명의 연결을 다시 확인해 주세요.',analyst_id:'검토자',source_system:'test-fixture',created_at:new Date().toISOString(),production_configuration_id:id(300),production_configuration_label:'검토용 운영 구성'};
methods.feedback=async q=>({items:[frow],total:1,page:q.page,page_size:q.page_size});
methods.feedbackSummary=async()=>({registration_count:1,analysis_count:1,multi_category_count:1,comment_only_count:0,category_counts:['insufficient_evidence','evidence_mismatch','explanation_error','exaggerated_impact','inappropriate_severity','other'].map(category=>({category,count:frow.feedback_categories.includes(category)?1:0,percent:frow.feedback_categories.includes(category)?100:0}))});
methods.feedbackVersions=async()=>({items:[{id:id(300),label:'검토용 운영 구성',count:1,analysis_count:1}],unrecorded_count:0});methods.feedbackDetail=async()=>frow;
const stat={cases:3,completed:3,failed:0,pending:0,processing:0,canceled:0,true_positive:1,false_positive:1,inconclusive:1,distribution_total:3,verdict_rates:{true_positive:1/3,false_positive:1/3,inconclusive:1/3},comparable:3,labeled:3,matches:2,accuracy:2/3,false_negatives:0,false_positives:0};
methods.testStatistics=async which=>({test_run_id:which,summary:stat,items:[{...stat,value:'설정 파일 접근'},{...stat,value:'쿼리 변경 입력'}],total_groups:2,reference_basis:'ground_truth',rejected:0,duplicates:0});
const dt=n=>({recorded:3,unrecorded:0,mean_ms:n,p50_ms:n,p95_ms:n*1.4,max_ms:n*2,min_ms:n/2});
const tk=n=>({recorded:3,unrecorded:0,mean:n,p95:n*1.4});
const perfRole=()=>({accounting:{recorded_cases:3,unrecorded_cases:0,cases_with_output_retries:1,cases_with_citation_repairs:0},first_attempt_ms:dt(8200),output_retry_ms:dt(1500),citation_repair_ms:dt(0),slot_wait_ms:dt(400),tokens:{input_tokens:tk(1800),output_tokens:tk(500),first_attempt_output_tokens:tk(420)},output_failures:{cases_with_output_failure:1,completed_cases_with_output_failure:1,attempt_outcomes:{output_validation:1}}});
const times={analysis_queue_ms:dt(3000),analysis_processing_ms:dt(19000),decision_elapsed_ms:dt(22000),result_ready_elapsed_ms:dt(24000)};
methods.testPerformance=async which=>({version:'test-performance-summary-v1',test_run_id:which,cases:3,completed_cases:3,statuses:{completed:3},timings:times,completed_timings:times,phases:{llm_primary:{entered_cases:3,not_observed_cases:0,states:{completed:3}},llm_verifier:{entered_cases:2,not_observed_cases:1,states:{completed:2}}},roles:{primary:perfRole(),verifier:perfRole()},display:{states:{completed:3},no_job_cases:0,queue_ms:dt(500),processing_ms:dt(1500)}});
export const api=`).replace("get(target,name){", "get(target,name){if(name==='analysisEvidenceMeta')return target[name];");
const bundle = await build({ stdin: { contents: `import {StrictMode} from 'react';import {createRoot} from 'react-dom/client';import App from './App.jsx';import './styles.css';import './console.css';import './lifecycle.css';import './r3.css';import './workspacePolish.css';createRoot(document.getElementById('root')).render(<StrictMode><App/></StrictMode>);`, loader:"jsx",resolveDir:fileURLToPath(new URL("../src",import.meta.url)) },
  plugins:[{name:"offline-api",setup(b){b.onLoad({filter:/\/api\.js$/},()=>({contents:mock,loader:"js"}));}}],bundle:true,write:false,outfile:join(output,"bundle.js"),platform:"browser",format:"iife",jsx:"automatic",define:{"process.env.NODE_ENV":'"production"'},loader:{".md":"text",".woff2":"dataurl"},logLevel:"silent" });
const archive=join(homedir(),".cache/uv/archive-v0");
const driver=process.env.PLAYWRIGHT_MODULE||readdirSync(archive).map(n=>join(archive,n,"playwright/driver/package/index.mjs")).find(existsSync);
const {chromium}=await import(pathToFileURL(driver).href);
const browser=await chromium.launch({executablePath:process.env.CHROMIUM_PATH||join(homedir(),".cache/ms-playwright/chromium-1208/chrome-linux64/chrome"),headless:true,args:["--no-sandbox"]});
const pages=[
  ["home","#overview",".r3-overview"], ["tests","#evaluate/tests",".test-run-history"],
  ["test-cases",`#evaluate/tests/${run}`,".test-run-detail"], ["test-evaluation",`#evaluate/tests/${run}/metrics`,".oe-evaluation-v2"],
  ["test-statistics",`#evaluate/tests/${run}/statistics`,".test-stat-table"],
  ["new-test","#evaluate/tests/new",".r5-configuration-form"],
  ["ground-truth","#evaluate/ground-truth",".r3-ground-truth"],
  ["ground-truth-detail","#evaluate/ground-truth/00000007-1111-4111-8111-111111111111",".r3-ground-truth-split"],
  ["production-evaluation","#evaluate/production-evaluation",".production-evaluation-workspace"],
  ["production-review",`#promotion/${run}`,".v5-preflight"],
  ["models","#configure/llm-profiles",".profile-section"], ["agent-roles","#configure/agent-roles",".r5-readonly-roles"],
  ["instructions","#configure/instructions",".prompt-settings"], ["input-schema","#configure/input-schema",".input-schema-settings"],
  ["parsers","#configure/parsers",".parser-workbench"], ["parser-new","#configure/parsers/new/data",".pw-input-panel"],
  ["signature-explanations","#configure/signature-explanations",".signature-catalog"],
  ["runtime","#operate/runtime",".agent-settings-panel"], ["production-history","#operate/inference",".analysis-data-table"],
  ["feedback","#operate/feedback",".feedback-workspace"], ["activity","#operate/activity",".v5-workbench"],
  ["deployment","#runtime/deployment",".deployment-workspace"],
  ["api-document","#connect/production-api",".api-document-page"], ["api-keys","#connect/api-keys",".service-api-keys"],
  ["vllm-targets","#connect/vllm-targets",".internal-egress-settings"],
  ...[["result","analysis-workspace"],["input","analysis-workspace"],["agent-trace","decision-execution"],["report","analysis-report"]].map(([tab,selector])=>["production-"+tab,`#operate/inference/${prod}/${tab}`,"."+selector]),
  ["test-analysis",`#evaluate/tests/${run}/case/${testCase}/result`,".test-analysis-workspace"],
  ["test-execution",`#evaluate/tests/${run}/case/${testCase}/agent-trace`,".decision-execution"],
];
const results=[],errors=[],network=[];
let page;
try {
  page=await browser.newPage({viewport:{width:1440,height:1000},reducedMotion:"reduce"});page.setDefaultTimeout(10000);
  page.on("pageerror",e=>{errors.push(e.message);console.error("Browser:",e.message);});
  await page.route("**/*",r=>r.request().url().split("#")[0]==="https://fixture.invalid/"?r.fulfill({contentType:"text/html",body:'<html lang="ko"><meta name="viewport" content="width=device-width,initial-scale=1"><body><div id="root"></div></body></html>'}):(network.push(r.request().url()),r.abort()));
  await page.goto("https://fixture.invalid/");await page.addStyleTag({content:bundle.outputFiles.find(f=>f.path.endsWith(".css")).text});await page.addScriptTag({content:bundle.outputFiles.find(f=>f.path.endsWith(".js")).text});
  await page.locator(".r3-overview").waitFor();
  const go=async hash=>{await page.evaluate(hash=>{location.hash=hash;scrollTo(0,0);},hash);};
  const theme=async name=>{await page.getByRole("button",{name:/^(테마|Theme)$/,exact:true}).click();await page.getByRole("button",{name,exact:true}).click();};
  async function snap(name){
    await page.evaluate(()=>scrollTo(0,0));
    await page.evaluate(()=>new Promise(done=>requestAnimationFrame(()=>requestAnimationFrame(done))));
    const geometry=await page.evaluate(()=>({width:innerWidth,scrollWidth:document.documentElement.scrollWidth,theme:document.documentElement.dataset.theme,locale:document.documentElement.lang,tabulators:document.querySelectorAll(".tabulator").length}));
    if(geometry.width>=1200){
      const cards=await page.locator('.settings-summary:visible > .settings-summary-item').evaluateAll(nodes=>nodes.map(node=>({top:node.getBoundingClientRect().top,left:node.getBoundingClientRect().left})));
      if(cards.length>1)assert.ok(cards.every((card,index)=>Math.abs(card.top-cards[0].top)<2&&(!index||card.left>cards[index-1].left)),name+" summary cards must form a desktop row");
    }
    results.push({name,...geometry});await page.screenshot({path:join(output,name+".png"),fullPage:true});
    assert.ok(geometry.scrollWidth<=geometry.width+1,name+" page overflow "+JSON.stringify(geometry));
    console.log(name,"fits");
  }
  for(const [color,locale,width] of [["Light","KR",1440],["SK","KR",390],["Dark","EN",1440]]){
    await page.setViewportSize({width,height:1000});await theme(color);await page.getByRole("button",{name:locale,exact:true}).click();
    for(const [name,hash,selector] of pages){
      await go(hash);
      // The selector is used for readiness, never to hide missing/error content.
      await page.locator(selector).first().waitFor();
      if(name==="ground-truth")await page.locator(".evaluation-dataset-table .dataset-open").first().waitFor();
      if(name==="ground-truth-detail"){await page.getByRole("button",{name:"SQLi 경계 사례",exact:true}).click();await page.locator(".r3-draft-editor").waitFor();}
      if(name==="test-statistics")await page.locator(".test-stat-table .tabulator-row").first().waitFor();
      await snap(`${color}-${locale}-${width}-${name}`);
      if(name==="new-test"){
        await page.getByRole("radio",{name:locale==="KR"?/개발 테스트/:/Development Test/}).check();
        for(const [tab,ko,en] of [["single","단건 분석","Single Analysis"],["batch","배치 파일 분석","Batch File Analysis"],["dataset","평가 데이터셋","Dataset"]]){
          await page.getByRole("tab",{name:locale==="KR"?ko:en,exact:true}).click();await snap(`${color}-${locale}-${width}-new-test-${tab}`);
        }
      }
    }
  }
  await page.setViewportSize({width:1440,height:1000});await theme("Light");await page.getByRole("button",{name:"KR",exact:true}).click();await go("#overview");
  assert.equal(await page.locator(".console-brand").evaluate(e=>e.getBoundingClientRect().bottom),await page.locator(".console-header").evaluate(e=>e.getBoundingClientRect().bottom));
  await page.keyboard.press("/");await page.locator(".console-search-dialog[open] input").waitFor();assert.equal(await page.locator(".console-search-dialog input").evaluate(e=>document.activeElement===e),true);
  await snap("search-dialog");await page.keyboard.press("Escape");
  await go("#evaluate/tests");await page.locator(".test-run-history .tabulator-row").first().waitFor();
  const density=page.getByRole("button",{name:"촘촘한 표",exact:true}),before=await page.locator(".tabulator-cell").first().evaluate(e=>getComputedStyle(e).paddingTop);
  await density.click();assert.equal(await density.getAttribute("aria-pressed"),"true");
  assert.notEqual(await page.locator(".tabulator-cell").first().evaluate(e=>getComputedStyle(e).paddingTop),before);await density.click();
  await go("#configure/llm-profiles");await page.getByRole("button",{name:"모델 추가",exact:true}).click();
  await page.locator(".model-profile-editor").waitFor();assert.equal(await page.locator("dialog[open]").count(),0,"profile editing is a page workspace");
  await snap("model-editor");
  await page.getByLabel("프로필 이름",{exact:true}).fill("화면 검토 초안");await page.getByLabel(/^LLM 공급자/).selectOption("openai");
  assert.equal(await page.getByLabel("Base URL",{exact:true}).inputValue(),"https://api.openai.com/v1");
  assert.equal(await page.getByLabel("Base URL",{exact:true}).evaluate(e=>e.readOnly),true);
  assert.equal(await page.locator('.external-approval input[type="checkbox"]').isChecked(),false,"external transfer is never silently approved");
  await snap("model-openai-editor");assert.ok(!page.url().includes("화면"),"drafts stay out of URLs");
  await page.getByRole("button",{name:/프로필 목록$/}).click();await page.locator(".profile-section").waitFor();
  await go("#configure/instructions");await page.locator(".prompt-settings").first().waitFor();await snap("instruction-editor");
  await go("#configure/input-schema");await page.locator(".input-schema-settings").first().waitFor();await snap("schema-editor");
  await go("#connect/api-keys");await page.getByRole("button",{name:"키 발급",exact:true}).click();await page.locator("dialog[open]").waitFor();await snap("key-dialog");await page.keyboard.press("Escape");
  for(const [analysis,purpose,path] of [[prod,"production",`#operate/inference/${prod}/result`],[testCase,"test",`#evaluate/tests/${run}/case/${testCase}/result`]]){
    await go(path);await page.getByRole("button",{name:"이 분석의 피드백",exact:true}).click();await page.locator(".feedback-analysis-scope").waitFor();
    const filters=new URLSearchParams(page.url().split("?")[1]);assert.equal(filters.get("analysis"),analysis);assert.equal(filters.get("purpose"),purpose);
    assert.equal(filters.get("start"),"2026-09-17","old analysis feedback starts at admission, not the default 30-day period");
    await snap(`${purpose}-feedback-entry`);await page.goBack();await page.getByRole("button",{name:"이 분석의 피드백",exact:true}).waitFor();
  }
  assert.deepEqual(errors,[]);assert.deepEqual(network,[]);assert.deepEqual(await page.evaluate(()=>fixture.unknown),[]);
  assert.equal(await page.evaluate(()=>fixture.writes.length),0,"visual checks must not mutate even fixture configuration");

  const reference=await browser.newPage({viewport:{width:1440,height:1000},reducedMotion:"reduce"});
  const root=fileURLToPath(new URL("../../mockup/console-redesign-v2/",import.meta.url));
  await reference.route("**/*",r=>{
    const u=new URL(r.request().url()),path=resolve(root,"."+(u.pathname==="/"?"/index.html":u.pathname));
    if(u.origin!=="https://wireframe.invalid"||!path.startsWith(root)||!existsSync(path))return r.abort();
    const type={".html":"text/html",".css":"text/css",".js":"text/javascript"}[extname(path)]||"application/octet-stream";
    return r.fulfill({contentType:type,body:readFileSync(path)});
  });
  await reference.goto("https://wireframe.invalid/#home");await reference.locator(".home-feature").waitFor();await reference.screenshot({path:join(output,"reference-Light-KR-1440-home.png"),fullPage:true});await reference.close();
  await page.getByRole("button",{name:"계정",exact:true}).click();await page.getByRole("button",{name:"로그아웃",exact:true}).click();await page.locator(".login-card").waitFor();
  for(const [color,locale,width] of [["Light","KR",1440],["SK","KR",390],["Dark","EN",1440]]){
    await page.setViewportSize({width,height:1000});await theme(color);await page.getByRole("button",{name:locale,exact:true}).click();await snap(`${color}-${locale}-${width}-login`);
  }
  assert.deepEqual(errors,[]);assert.deepEqual(network,[]);
  console.log(JSON.stringify({screenshots:results.length+1,output,checks:"all routes, Light/KR desktop, SK/KR mobile, Dark/EN desktop, exact brand/header alignment, search shortcut/focus, density, inline editors, no network or writes"}));
} catch(error){await page?.screenshot({path:join(output,"failure.png"),fullPage:true});console.error(await page?.evaluate(()=>fixture.unknown));throw error;}
finally{writeFileSync(join(output,"audit.json"),JSON.stringify({results,errors,network},null,2));await browser.close();}
