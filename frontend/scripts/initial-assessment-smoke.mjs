// Synthetic single-request form/API fixture; no external or model traffic.
import assert from 'node:assert/strict';
import { existsSync, readdirSync, mkdirSync } from 'node:fs';
import { homedir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { build } from 'esbuild';
import { r5BrowserFixture } from './r5-browser-fixture.mjs';

const output = fileURLToPath(new URL('../../test-results/initial-test-assessment', import.meta.url));
mkdirSync(output, { recursive: true });
const mock = r5BrowserFixture().replace('export const api=', `
state.singleRequests=[];state.initialFailure=false;
methods.createTestRun=async payload=>{state.singleRequests.push(payload);if(state.initialFailure){const error=Error('HTTP 422');error.issues=[{field:'body.initial_probability',type:'float_type'}];throw error;}return runs[0];};
const initialAnalysis=methods.analysis;
methods.analysis=async value=>({...await initialAnalysis(value),initial_assessment:{verdict:'true_positive',probability:.9,model_version:'fixture-v1',comparison:'final_inconclusive'}});
export const api=`);
const bundle = await build({ stdin: { contents: `import {createRoot} from 'react-dom/client';import App from './App.jsx';import './styles.css';import './console.css';import './lifecycle.css';import './r3.css';createRoot(document.getElementById('root')).render(<App/>);`, loader: 'jsx', resolveDir: fileURLToPath(new URL('../src', import.meta.url)) },
  plugins: [{ name: 'offline-api', setup(b) { b.onLoad({ filter: /\/api\.js$/ }, () => ({ contents: mock, loader: 'js' })); } }],
  bundle: true, write: false, outfile: join(output, 'bundle.js'), platform: 'browser', format: 'iife', jsx: 'automatic', define: { 'process.env.NODE_ENV': '"production"' }, loader: { '.md': 'text', '.woff2': 'dataurl' }, logLevel: 'silent' });
const archive = join(homedir(), '.cache/uv/archive-v0');
const driver = process.env.PLAYWRIGHT_MODULE || readdirSync(archive).map(name => join(archive, name, 'playwright/driver/package/index.mjs')).find(existsSync);
const { chromium } = await import(pathToFileURL(driver).href);
const browser = await chromium.launch({ executablePath: process.env.CHROMIUM_PATH || join(homedir(), '.cache/ms-playwright/chromium-1208/chrome-linux64/chrome'), headless: true, args: ['--no-sandbox'] });
let page;
try {
  page = await browser.newPage({ viewport: { width: 1440, height: 1100 }, reducedMotion: 'reduce' });
  const errors=[],network=[];let screenshots=0;
  page.on('pageerror',error=>errors.push(error.message));
  await page.route('**/*',route=>route.request().url().split('#')[0]==='https://fixture.invalid/'?route.fulfill({contentType:'text/html',body:'<html lang="ko"><head><meta name="viewport" content="width=device-width,initial-scale=1"></head><body><div id="root"></div></body></html>'}):(network.push(route.request().url()),route.abort()));
  await page.goto('https://fixture.invalid/');
  await page.addStyleTag({content:bundle.outputFiles.find(f=>f.path.endsWith('.css')).text});
  await page.addScriptTag({content:bundle.outputFiles.find(f=>f.path.endsWith('.js')).text});
  page.setDefaultTimeout(10000);await page.locator('.r3-overview').waitFor();
  const go=hash=>page.evaluate(hash=>{location.hash=hash;},hash);
  const snap=async name=>{await page.evaluate(()=>window.scrollTo(0,0));assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);await page.screenshot({path:join(output,name+'.png'),fullPage:true});screenshots++;};
  const prepare=async locale=>{
    const kr=locale==='KR';await go('#evaluate/tests/new');
    await page.getByRole('radio',{name:kr?/개발 테스트/:/Development Test/}).check();
    const form=page.locator('form.form-panel');
    for(const [ko,en,value]of [['회사명','Company','Synthetic'],['WAF 벤더','WAF vendor','generic'],['출발지 IP','Source IP','192.0.2.1'],['목적지 IP','Destination IP','198.51.100.1'],['HTTP 원문','Raw HTTP','GET /fixture HTTP/1.1\r\nHost: fixture.invalid\r\n\r\n']])await form.getByLabel(kr?ko:en,{exact:true}).fill(value);
    await form.getByLabel(kr?'WAF 조치':'WAF action',{exact:true}).selectOption('D');
    await form.getByRole('combobox',{name:kr?/^참고 판정 \(선택\)/:/^Reference Verdict \(optional\)/}).selectOption('false_positive');
    await form.getByRole('button',{name:kr?'추가 입력':'Optional fields',exact:true}).click();
    return form;
  };
  for(const theme of ['SK','Light','Dark']){
    await page.getByRole('button',{name:/^(테마|Theme)$/}).click();await page.getByRole('button',{name:theme,exact:true}).click();
    for(const locale of ['KR','EN']){
      const kr=locale==='KR';await page.getByRole('button',{name:locale,exact:true}).click();
      const form=await prepare(locale);
      await form.getByRole('combobox',{name:kr?/^1차 판정/:/^Initial verdict/}).selectOption('true_positive');
      await form.getByLabel(kr?'1차 신뢰도 (0~1)':'Initial confidence (0–1)',{exact:true}).fill('0.9');
      await form.getByLabel(kr?'1차 모델 버전':'Initial model version',{exact:true}).fill('fixture-v1');
      await form.getByRole('combobox',{name:kr?/^1차 판정/:/^Initial verdict/}).focus();await page.keyboard.press('Tab');
      assert.equal(await page.locator(':focus').getAttribute('type'),'number');
      await snap('single-'+locale+'-'+theme);
      if(theme==='Light'&&kr){await page.setViewportSize({width:390,height:844});await snap('single-mobile');await page.setViewportSize({width:1440,height:1100});}
      await form.getByRole('button',{name:kr?'분석 시작':'Start analysis',exact:true}).click();await page.locator('.test-run-detail').waitFor();
      const payload=await page.evaluate(()=>fixture.singleRequests.at(-1));
      assert.equal(payload.initial_verdict,'true_positive');assert.equal(payload.initial_probability,.9);assert.equal(payload.expected_verdict,'false_positive');
      assert.equal(Object.hasOwn(payload.event,'initial_verdict'),false);assert.equal(Object.hasOwn(payload.event,'initial_probability'),false);assert.equal(Object.hasOwn(payload.event,'initial_model_version'),false);
    }
  }
  await page.getByRole('button',{name:'KR',exact:true}).click();let form=await prepare('KR');
  await form.getByRole('textbox',{name:/^스키마 추가 필드 \(JSON\)/}).fill(JSON.stringify({initial_verdict:'true_positive',initial_probability:90}));
  const before=await page.evaluate(()=>fixture.singleRequests.length);
  await form.getByRole('button',{name:'분석 시작',exact:true}).click();await form.getByText('1차 신뢰도는 0~1 사이의 숫자로 입력하세요. 90%는 0.9입니다.',{exact:true}).waitFor();
  assert.equal(await page.evaluate(()=>fixture.singleRequests.length),before);
  await form.getByRole('textbox',{name:/^스키마 추가 필드 \(JSON\)/}).fill(JSON.stringify({initial_verdict:'false_positive',initial_probability:0}));
  await page.evaluate(()=>{fixture.initialFailure=true;});await form.getByRole('button',{name:'분석 시작',exact:true}).click();await page.waitForFunction(count=>fixture.singleRequests.length===count+1,before);
  await form.getByText('1차 신뢰도는 0~1 사이의 숫자로 입력하세요. 90%는 0.9입니다.',{exact:true}).waitFor();await snap('safe-error');
  await page.evaluate(()=>{fixture.initialFailure=false;});await form.getByRole('button',{name:'분석 시작',exact:true}).click();await page.locator('.test-run-detail').waitFor();
  const inline=await page.evaluate(()=>fixture.singleRequests.at(-1));assert.equal(inline.initial_probability,0);assert.equal(Object.hasOwn(inline.event,'initial_probability'),false);
  assert.deepEqual(network,[]);assert.deepEqual(errors,[]);
  console.log(JSON.stringify({screenshots,checks:'single form KR/EN and SK/Light/Dark, mobile, keyboard order, dedicated and JSON metadata separation, zero confidence, expected verdict independence, safe errors; no external traffic'}));
}catch(error){if(page){console.error(await page.locator('form.form-panel label').allTextContents());await page.screenshot({path:join(output,'failure.png'),fullPage:true});}throw error;}
finally{await browser.close();}
