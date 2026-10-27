// README screenshots of the current React UI. All records and metrics are synthetic.
// No server, database, secrets, model calls or network downloads are used.
import assert from 'node:assert/strict';
import { existsSync, mkdirSync, readdirSync } from 'node:fs';
import { homedir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { build } from 'esbuild';
import { r5BrowserFixture } from './r5-browser-fixture.mjs';

const output = fileURLToPath(new URL('../../assets/screenshots/', import.meta.url));
mkdirSync(output, { recursive: true });
const mock = r5BrowserFixture().replace('export const api=', `
// Synthetic demonstration values, not measured model performance.
profiles[0].name='내부 WAF 분석 모델'; profiles[0].model_name='demo-security-model';
profiles[1].name='WAF 분석 후보'; profiles[1].model_name='demo-candidate-model';
production.profile_names[id(1)]=profiles[0].name;
production.profile_names[id(2)]=profiles[1].name;
production.version_names[id(3)]='v3 · WAF 분석 지침';
production.version_names[id(5)]='v2 · 기본 입력';
Object.assign(evaluation, {
 total:150,labeled:150,evaluable:150,matches:139,binary_evaluable:140,binary_decided:135,
 support_positive:70,support_negative:70,label_coverage:1,
 metrics:{accuracy:131/135,precision:65/67,recall:65/67,f1:65/67,coverage:135/140,
  abstention_rate:5/140,balanced_accuracy:(65/67+66/68)/2,mcc:.9407},
 outcomes:{match:131,abstained:5,false_positive:2,false_negative:2,
  expected_abstention_match:8,expected_abstention_mismatch:2,unlabeled:0,failed:0,pending:0},
 confusion_matrix:{tp:65,tn:66,fp:2,fn:2,abstained_positive:3,abstained_negative:2,
  expected_hold_positive:1,expected_hold_negative:1,expected_hold_match:8}
});
runs.forEach((run,i)=>Object.assign(run,{name:i===0?'WAF 공격·정상 요청 검증':'이전 구성 회귀 검증',
 total:150,accepted:150,completed:150,pending:0,processing:0,failed:0,total_elapsed_ms:284000,
 created_at:'2026-10-27T00:00:00Z',input_schema_version:'v2',input_schema_metadata:{version_number:2},prompt_version:'v3',
 profile_metadata:{model_name:profiles[i===0?1:0].model_name,verifier_profile:{model_name:profiles[i===0?1:0].model_name}},
 ground_truth:{...run.ground_truth,dataset_name:'WAF 분석 평가 데이터',dataset_revision:3,
  sample_count:150,approved_count:150,excluded_count:0},evaluation_summary:evaluation}));
Object.assign(working,{name:'WAF 분석 평가 데이터',total:156,working_changes_count:6,
 counts:{ready:150,needs_attention:4,excluded:2}});
const readOverview=methods.overview;
methods.overview=async()=>{const data=await readOverview();return {...data,
 setup:{...data.setup,production_api_traffic:{status:'observed'}},
 ground_truth_working_draft:working,
 trend:[.88,.91,131/135].map((accuracy,i)=>({...official,id:id(200+i),
  created_at:['2026-10-06T00:00:00Z','2026-10-16T00:00:00Z','2026-10-27T00:00:00Z'][i],
  summary:{...runs[0],name:'구성 검증 '+(i+1),evaluation_summary:{...evaluation,
   metrics:{...evaluation.metrics,accuracy,f1:[.86,.90,65/67][i],coverage:[.91,.94,135/140][i]}}}})),
 recent_comparable_tests:[{...official,created_at:'2026-10-27T00:00:00Z',summary:runs[0]}],
 actions:[{kind:'ground_truth',count:4,dataset_id:id(7),name:'기대 판정·입력 확인'},
  {kind:'promotion',test_run_id:id(8),name:runs[0].name}]};};
methods.runtimeStatus=async()=>({...runtime,updated_at:'2026-10-27T00:00:00Z',
 queue:{pending:0,processing:0},request_count:486,outcome_summary:{completed:486,failed:0},
 latency_summary:{p50:18300,p95:28100},health:{...runtime.health,
  analysis_worker:{status:'healthy',observed_at:'2026-10-27T00:00:00Z'}}});
const evidence=[
 {evidence_id:'demo-e1',field:'payload.body',excerpt:"' OR 1=1 --",
  interpretation_ko:'로그인 입력에 항상 참인 조건식과 SQL 주석 구문이 함께 있습니다. 사용자 입력으로 인증 조건을 우회하려는 SQL Injection 시도로 판단합니다.',supports:'true_positive'},
 {evidence_id:'demo-e2',field:'waf_action',excerpt:'D',
  interpretation_ko:'WAF가 요청을 차단했습니다. 이 기록만으로 애플리케이션의 SQL 실행이나 실제 침해가 발생했다고 볼 수는 없습니다.',supports:'context'}
];
Object.assign(state.detail,{event_id:'demo-waf-001',company_name:'예시 서비스',signature:'SQL Injection Attempt',
 waf_vendor:'Demo WAF',created_at:'2026-10-27T00:00:00Z',model_profile:profiles[0].name,
 prompt_version:'지침 v3',total_elapsed_ms:18450,queue_wait_ms:50,processing_duration_ms:18400,
 evaluation:{outcome:'match',reference_label:{verdict:'true_positive',source_kind:'reference',ai_visible:false}},
 result:{schema_version:'waf-analysis-v2',verdict:'true_positive',confidence_score:.96,
  summary_ko:'로그인 요청에서 SQL 조건을 우회하는 구문이 확인되었습니다. WAF가 요청을 차단했으며, 침해 성공을 보여 주는 정보는 없습니다.',
  threat_analysis:{severity:'HIGH',category:'sql_injection',target:'로그인 요청 본문',
   technique_ko:'항상 참인 SQL 조건과 주석을 조합해 인증 조건을 우회하려는 요청입니다.',
   potential_impact_ko:'입력값이 SQL에 직접 반영되는 환경에서는 인증 우회로 이어질 수 있습니다.',obfuscations:[]},
  signature_assessment:{relation:'exact',explanation_ko:'로그인 입력의 논리식과 주석이 SQL Injection 탐지 내용과 일치합니다.'},
  analyst_assessment:{version:'analyst-assessment-v1',evidence,decision_issues:[]},evidence,
  analyst_guidance:{summary_ko:'로그인 요청에서 SQL 조건을 우회하는 구문이 확인되었습니다. WAF가 요청을 차단했으며, 침해 성공을 보여 주는 정보는 없습니다.',checks:[],limitations:[]},
  recommended_checks:[],analyst_checks:[],conflicting_evidence:[],diagnostics:{},input_truncated:false,
  tuning_recommendation:{recommended:false},primary:{verdict:'true_positive'},
  verifier:{executed:true,agreement:true,output:{verdict:'true_positive'}},
  agent:{framework:'moduagent',role_profiles:{primary:{model_profile:profiles[0].name},verifier:{model_profile:profiles[0].name}}}
 }});
methods.analysis=async value=>({...structuredClone(state.detail),id:value,analysis_purpose:'production',test_run_id:null,initial_assessment:null});
methods.me=async()=>({username:'demo-admin',kind:'admin_session',scopes:['admin']});
export const api=`);

const bundle = await build({
  stdin: { contents: `import {createRoot} from 'react-dom/client';import App from './App.jsx';import './styles.css';import './console.css';import './lifecycle.css';import './r3.css';createRoot(document.getElementById('root')).render(<App/>);`, loader: 'jsx', resolveDir: fileURLToPath(new URL('../src', import.meta.url)) },
  plugins: [{ name: 'offline-readme-api', setup(builder) { builder.onLoad({ filter: /\/api\.js$/ }, () => ({ contents: mock, loader: 'js' })); } }],
  bundle: true, write: false, outfile: join(output, 'bundle.js'), platform: 'browser', format: 'iife', jsx: 'automatic',
  define: { 'process.env.NODE_ENV': '"production"' }, loader: { '.md': 'text', '.woff2': 'dataurl' }, logLevel: 'silent',
});
const archive = join(homedir(), '.cache/uv/archive-v0');
const driver = process.env.PLAYWRIGHT_MODULE || (existsSync(archive) ? readdirSync(archive).map(name => join(archive, name, 'playwright/driver/package/index.mjs')).find(existsSync) : null);
if (!driver) throw Error('A cached development Playwright is required. No download is attempted.');
const { chromium } = await import(pathToFileURL(driver).href);
const browser = await chromium.launch({ executablePath: process.env.CHROMIUM_PATH || join(homedir(), '.cache/ms-playwright/chromium-1208/chrome-linux64/chrome'), headless: true, args: ['--no-sandbox'] });
const errors = [], requests = [];
try {
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 }, reducedMotion: 'reduce', timezoneId: 'Asia/Seoul' });
  page.setDefaultTimeout(15000);
  page.on('pageerror', error => errors.push(error.message));
  await page.route('**/*', route => {
    if (route.request().url().split('#')[0] === 'https://fixture.invalid/') return route.fulfill({ contentType: 'text/html', body: '<html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head><body><div id="root"></div></body></html>' });
    requests.push(route.request().url()); return route.abort();
  });
  await page.goto('https://fixture.invalid/#overview');
  await page.addStyleTag({ content: bundle.outputFiles.find(file => file.path.endsWith('.css')).text });
  await page.addScriptTag({ content: bundle.outputFiles.find(file => file.path.endsWith('.js')).text });
  await page.locator('.r3-overview').waitFor();
  await page.getByRole('button', { name: '테마', exact: true }).click();
  await page.getByRole('button', { name: 'Light', exact: true }).click();
  const snap = async name => {
    await page.evaluate(async () => { scrollTo(0, 0); await document.fonts.ready; await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))); });
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true, name + ' must fit the page');
    assert.equal(await page.getByRole('alert').count(), 0, name + ' must not show an error');
    await page.screenshot({ path: join(output, name + '.png'), fullPage: true });
  };
  await page.locator('.r3-evaluation-chart').waitFor(); await snap('home');
  await page.evaluate(() => { location.hash = '#analyses/11111111-1111-4111-8111-111111111111'; });
  await page.locator('.decision-hero-summary').waitFor();
  await snap('analysis');
  await page.evaluate(() => { location.hash = '#evaluate/tests/00000008-1111-4111-8111-111111111111'; });
  await page.locator('.test-run-detail').waitFor();
  await page.getByRole('tab', { name: '평가 상세', exact: true }).click();
  await page.locator('.r5-matrix').waitFor();
  await snap('evaluation');
  assert.deepEqual(errors, []); assert.deepEqual(requests, []);
  assert.deepEqual(await page.evaluate(() => fixture.unknown), []);
  assert.equal(await page.evaluate(() => fixture.reads.some(row => ['rawEvent', 'agentRuns'].includes(row.name))), false);
  assert.equal(await page.evaluate(() => fixture.writes.length), 0);
  console.log('README screenshots: home, analysis and evaluation. Current React, synthetic data, zero external requests or writes.');
} finally { await browser.close(); }
