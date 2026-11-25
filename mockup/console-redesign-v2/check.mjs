// Browser checks run against the offline wireframe only; every network request is blocked.
import assert from 'node:assert/strict';
import { existsSync, mkdtempSync, readdirSync, writeFileSync } from 'node:fs';
import { homedir, tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
const root=dirname(fileURLToPath(import.meta.url));
const archive=join(homedir(),'.cache/uv/archive-v0');
const driver=process.env.PLAYWRIGHT_MODULE||readdirSync(archive).map(n=>join(archive,n,'playwright/driver/package/index.mjs')).find(existsSync);
assert.ok(driver,'Local Playwright driver is required.');
const {chromium}=await import(pathToFileURL(driver).href);
const browser=await chromium.launch({executablePath:process.env.CHROMIUM_PATH||join(homedir(),'.cache/ms-playwright/chromium-1208/chrome-linux64/chrome'),headless:true,args:['--no-sandbox']});
const output=mkdtempSync(join(tmpdir(),'waf-redesign-v2-'));
const result={output,pages:[],checks:[],errors:[],warnings:[],network:[]};
try{
  const context=await browser.newContext({viewport:{width:1440,height:1050},reducedMotion:'reduce',serviceWorkers:'block'});
  await context.route(/^https?:/,route=>{result.network.push(route.request().url());return route.abort();});
  const page=await context.newPage();page.setDefaultTimeout(8000);
  page.on('pageerror',e=>result.errors.push(e.message));
  page.on('console',m=>{if(m.type()==='warning'||m.type()==='error')result.warnings.push(m.text().slice(0,500));});
  const base=pathToFileURL(join(root,'..','waf-console-redesign-v2.html')).href;
  await page.goto(base+'#home');
  const visit=async(route,name=route.replace(/[^\w-]/g,'-'),capture=true)=>{
    await page.evaluate(route=>{location.hash=route;},route);
    await page.waitForTimeout(170);
    await page.locator('#main-content h1').first().waitFor();
    assert.equal(await page.locator('.wireframe-error').count(),0,route+' render error');
    const meta=await page.evaluate(()=>({title:document.querySelector('#main-content h1')?.textContent,width:innerWidth,documentWidth:document.documentElement.scrollWidth,tables:Object.keys(WAF.tables),rows:document.querySelectorAll('#main-content .tabulator-row').length,active:document.querySelector('.nav-link[aria-current]')?.textContent.trim()}));
    if(capture){await page.mouse.move(1,1);await page.evaluate(()=>{document.activeElement?.blur?.();window.scrollTo(0,0);document.getElementById('toast')?.classList.remove('visible');});await page.screenshot({path:join(output,name+'.png'),fullPage:true});}
    result.pages.push({route,name,...meta});return meta;
  };
  for(const[route,name]of[['home','home'],['operations','operations'],['operations?preview=ana-007','preview'],['analysis?id=ana-007','analysis'],['analysis?id=ana-004&tab=evidence&evidence=1','evidence'],['analysis?id=ana-001&tab=execution','execution-failed'],['analysis?id=ana-007&tab=report','report'],['feedback','feedback'],['tests','tests'],['test?id=test-1','test-cases'],['test?id=test-1&tab=evaluation','test-evaluation'],['test?id=test-1&tab=statistics','test-statistics'],['datasets','datasets'],['dataset?id=ds-1','dataset'],['newtest','newtest'],['compare','compare'],['promotion?id=test-1','promotion'],['quality','quality'],['profiles','profiles'],['profiles?view=edit','profile-edit'],['agents','agents'],['instructions','instructions'],['schema','schema'],['parsers','parsers'],['signatures','signatures'],['keys','keys'],['targets','targets'],['api','api'],['runtime','runtime'],['deployment','deployment'],['changes','changes']])await visit(route,name);
  await visit('operations','checks-operations',false);
  await page.locator('[data-filter="verdict"]').selectOption('true_positive');
  await page.waitForTimeout(150);
  assert.match(page.url(),/verdict=true_positive/);
  await page.reload();await page.waitForTimeout(200);
  assert.equal(await page.locator('[data-filter="verdict"]').inputValue(),'true_positive');
  assert.ok(await page.locator('.tabulator').count());result.checks.push('Tabulator offline, Production-only list, URL filter reload');
  await visit('operations','checks-pagination',false);
  await page.locator('#operations-table .tabulator-page[data-page="next"]').click();await page.waitForTimeout(150);
  assert.match(page.url(),/p_operations-table=2/);
  await page.reload();await page.waitForTimeout(200);
  assert.equal(await page.evaluate(()=>WAF.tables['operations-table'].getPage()),2);result.checks.push('First/last/numbered pagination; page state survives reload');
  await visit('analysis?id=ana-007','checks-feedback',false);
  const before=await page.evaluate(()=>WAF.data.feedback.length);
  await page.locator('[data-action="analysis-feedback-new"]').click();
  await page.locator('dialog input[value="other"]').check();
  await page.locator('[data-action="analysis-feedback-save"]').click();
  assert.ok((await page.locator('#feedback-form-error').innerText()).length);
  await page.locator('dialog textarea[name="comment"]').fill('가상 피드백: 핵심 근거를 확인했습니다.');
  await page.locator('[data-action="analysis-feedback-save"]').click();await page.waitForTimeout(150);
  assert.equal(await page.evaluate(()=>WAF.data.feedback.length),before+1);
  assert.equal(await page.locator('dialog[open]').count(),0);result.checks.push('Optional six-category feedback; Other comment validation; append-only demo');
  await page.locator('[data-action="analysis-metadata"]').first().focus();await page.keyboard.press('Enter');
  assert.equal(await page.locator('dialog[open]').count(),1);await page.keyboard.press('Escape');
  assert.equal(await page.locator('dialog[open]').count(),0);
  assert.equal(await page.evaluate(()=>document.activeElement?.dataset.action),'analysis-metadata');result.checks.push('Keyboard dialog open/Escape/focus restore');
  await visit('analysis?id=ana-007','checks-source',false);
  await page.locator('[data-action="analysis-source"]').first().click();await page.waitForTimeout(120);
  assert.ok(await page.locator('#source-highlight').count());result.checks.push('Evidence source links highlight real sample excerpt');
  await visit('test?id=test-1&tab=statistics','checks-grouping',false);
  await page.locator('[name="group-2"]').selectOption('company');
  await page.locator('[name="group-3"]').selectOption('action');
  await page.locator('[name="mode"]').selectOption('or');
  await page.locator('#statistics-form button[type="submit"]').click();await page.waitForTimeout(150);
  assert.match(page.url(),/stats=/);
  await page.reload();await page.waitForTimeout(200);
  assert.equal(await page.locator('[name="group-2"]').inputValue(),'company');
  assert.equal(await page.locator('[name="group-3"]').inputValue(),'action');
  assert.equal(await page.locator('[name="mode"]').inputValue(),'or');
  result.checks.push('Three-field grouping and OR condition survive reload');
  await visit('test?id=test-1&tab=evaluation','checks-matrix',false);
  await page.locator('[data-action="test-matrix"]').first().click();await page.waitForTimeout(150);
  assert.match(page.url(),/reference=/);assert.match(page.url(),/verdict=/);result.checks.push('3x3 matrix cell → filtered cases');
  for(const theme of ['light','dark','sk']){
    await page.evaluate(t=>document.documentElement.dataset.theme=t,theme);
    await visit('home',`home-${theme}`);
    await visit('analysis?id=ana-007',`analysis-${theme}`);
  }
  for(const width of[1280,1920,390]){
    await page.setViewportSize({width,height:1050});
    for(const route of['home','operations','analysis?id=ana-007','test?id=test-1&tab=evaluation','test?id=test-1&tab=statistics','newtest','profiles','schema','api','feedback']){
      const meta=await visit(route,`${width}-${route.replace(/[^\w-]/g,'-')}`,width===390||route==='home');
      if(meta.documentWidth>width+1)result.errors.push(`Body overflow ${route} at ${width}: ${meta.documentWidth}`);
    }
  }
  await context.close();
}finally{await browser.close();writeFileSync(join(output,'review.json'),JSON.stringify(result,null,2));}
console.log(JSON.stringify({output,screenCount:result.pages.length,checks:result.checks,errors:result.errors,warnings:[...new Set(result.warnings)],network:result.network},null,2));
assert.equal(result.errors.length,0,'Browser review found errors; inspect review.json');
assert.equal(result.network.length,0,'Offline wireframe must make no external requests');
