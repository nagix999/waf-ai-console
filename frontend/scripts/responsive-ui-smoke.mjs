// Actual React UI, synthetic offline APIs. No live service, model or database access.
import assert from 'node:assert/strict';
import { existsSync, readdirSync, mkdirSync, writeFileSync } from 'node:fs';
import { homedir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { build } from 'esbuild';
import { r5BrowserFixture } from './r5-browser-fixture.mjs';

const output = process.env.SCREENSHOT_DIR || fileURLToPath(new URL('../../test-results/responsive-ui/after', import.meta.url));
mkdirSync(output, { recursive: true });
const archive = join(homedir(), '.cache/uv/archive-v0');
const driver = process.env.PLAYWRIGHT_MODULE || readdirSync(archive).map(name => join(archive, name, 'playwright/driver/package/index.mjs')).find(existsSync);
const { chromium } = await import(pathToFileURL(driver).href);
const bundle = await build({ stdin: { contents: `import {createRoot} from 'react-dom/client';import App from './App.jsx';import './styles.css';import './console.css';import './lifecycle.css';import './r3.css';createRoot(document.getElementById('root')).render(<App/>);`, loader: 'jsx', resolveDir: fileURLToPath(new URL('../src', import.meta.url)) },
  plugins: [{ name: 'offline-api', setup(b) { b.onLoad({ filter: /\/api\.js$/ }, () => ({ contents: r5BrowserFixture(), loader: 'js' })); } }], bundle: true, write: false, outfile: join(output, 'bundle.js'), platform: 'browser', format: 'iife', jsx: 'automatic', define: { 'process.env.NODE_ENV': '"production"' }, loader: { '.md': 'text', '.woff2': 'dataurl' }, logLevel: 'silent' });
const browser = await chromium.launch({ executablePath: process.env.CHROMIUM_PATH || join(homedir(), '.cache/ms-playwright/chromium-1208/chrome-linux64/chrome'), headless: true, args: ['--no-sandbox'] });
const results = [], errors = [], network = [];
const run = '00000008-1111-4111-8111-111111111111';
const routes = [
  ['home', '#overview', '.r3-overview'],
  ['tests', '#evaluate/tests', '.test-run-history'],
  ['test-detail', '#evaluate/tests/' + run, '.test-run-detail'],
  ['ground-truth', '#evaluate/ground-truth', '.r3-ground-truth'],
  ['production-evaluation', '#evaluate/production-evaluation', '.page-stack'],
  ['production-review', '#promotion/' + run, '.v5-preflight'],
  ['llm-profiles', '#configure/llm-profiles', '.profile-section'],
  ['agent-roles', '#configure/agent-roles', '.r5-readonly-roles'],
  ['instructions', '#configure/instructions', '.prompt-settings'],
  ['input-schema', '#configure/input-schema', '.input-schema-settings'],
  ['runtime', '#operate/runtime', '.agent-settings-panel'],
  ['inference', '#operate/inference', '.analysis-data-table'],
  ['activity', '#operate/activity', '.v5-workbench'],
  ['production-api', '#connect/production-api', '.api-document-page'],
  ['api-keys', '#connect/api-keys', '.service-api-keys'],
  ['vllm-targets', '#connect/vllm-targets', '.internal-egress-settings'],
];
try {
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 }, reducedMotion: 'reduce' });
  page.setDefaultTimeout(10000);
  page.on('pageerror', e => errors.push(e.message));
  await page.route('**/*', r => r.request().url() === 'https://fixture.invalid/' ? r.fulfill({ contentType: 'text/html', body: '<html lang="ko"><head><meta name="viewport" content="width=device-width,initial-scale=1"></head><body><div id="root"></div></body></html>' }) : (network.push(r.request().url()), r.abort()));
  await page.goto('https://fixture.invalid/');
  await page.addStyleTag({ content: bundle.outputFiles.find(f => f.path.endsWith('.css')).text });
  await page.addScriptTag({ content: bundle.outputFiles.find(f => f.path.endsWith('.js')).text });
  const go = async (route, selector) => { await page.evaluate(route => { location.hash = route; scrollTo(0, 0); }, route); await page.locator(selector).first().waitFor(); };
  let prefix;
  const snap = async name => {
    const overlay = await page.locator('dialog[open],.console-floating-popover').count() > 0;
    if (!overlay && name !== 'scrolled-header') await page.evaluate(() => scrollTo(0, 0));
    await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
    const dimensions = await page.evaluate(() => ({ width: innerWidth, scrollWidth: document.documentElement.scrollWidth,
      overflowing: [...document.querySelectorAll('.console-content *')].filter(el => { const r = el.getBoundingClientRect(); return r.width && r.right > innerWidth + 1 && !el.closest('.table-wrap,pre,.json-viewer,.raw-viewer,.detail-tabs,.v5-workspace-tabs'); }).slice(0, 12).map(el => ({ tag: el.tagName, className: el.className, width: Math.round(el.getBoundingClientRect().width) })) }));
    results.push({ name: prefix + '-' + name, ...dimensions });
    await page.screenshot({ path: join(output, prefix + '-' + name + '.png'), fullPage: !overlay && name !== 'scrolled-header' });
    console.log(prefix + '-' + name, dimensions.scrollWidth > dimensions.width ? 'OVERFLOW ' + dimensions.scrollWidth : 'fits');
  };
  const closeDialog = async title => { await page.getByRole('dialog', { name: title, exact: true }).getByRole('button', { name: title + ' 닫기', exact: true }).click(); };
  const checkPopup = async trigger => {
    await trigger.click();
    const popup = page.locator('.console-floating-popover');
    await popup.waitFor();
    assert.equal(await popup.evaluate(el => { const r = el.getBoundingClientRect(); return r.left >= 0 && r.right <= innerWidth && r.top >= 0 && r.bottom <= innerHeight; }), true);
    assert.equal(await popup.evaluate(el => el.contains(document.activeElement)), true);
    await snap('row-menu'); await page.keyboard.press('Escape');
    assert.equal(await trigger.evaluate(el => el === document.activeElement), true);
  };
  for (const width of [1440, 390]) {
    prefix = width === 1440 ? 'desktop' : 'mobile';
    await page.setViewportSize({ width, height: width === 1440 ? 900 : 844 });
    for (const [name, route, selector] of routes) {
      await go(route, selector);
      if (name === 'ground-truth') { await page.getByLabel('데이터셋', { exact: true }).selectOption('00000007-1111-4111-8111-111111111111'); await page.getByRole('button', { name: 'SQLi 경계 사례', exact: true }).click(); await page.locator('.r3-draft-editor').waitFor(); }
      await snap(name);
      assert.equal(await page.locator('.data-table .numeric').evaluateAll(cells => cells.every(cell => getComputedStyle(cell).textAlign === 'right')), true, 'Numeric headers and values align: ' + name);
      if (name === 'tests') {
        const spacing = await page.locator('.v5-list-action > button').evaluateAll(buttons => { const [first, next] = buttons.map(el => el.getBoundingClientRect()); return { gap: next.left - first.right, aligned: Math.abs(next.top - first.top) < 1 }; });
        assert.ok(spacing.gap >= 8 && spacing.aligned, 'Default configuration and new Test buttons have aligned spacing');
      }
    }
    await go('#evaluate/tests/' + run, '.test-run-detail');
    await page.getByRole('tab', { name: '평가 상세', exact: true }).click(); await snap('test-evaluation');
    await page.getByRole('button', { name: '추가 지표', exact: true }).click(); await page.getByRole('dialog').waitFor(); await snap('additional-metrics'); await page.keyboard.press('Escape');
    await page.getByRole('button', { name: '테스트 작업', exact: true }).click();
    await page.getByRole('button', { name: '테스트 사례를 정답 데이터에 추가', exact: true }).click();
    await page.getByRole('button', { name: '미리보기', exact: true }).click(); await page.locator('.r5-import-summary').waitFor(); await snap('import-preview'); await page.keyboard.press('Escape');
    await page.getByRole('tab', { name: '문항별 결과', exact: true }).click();
    await page.getByRole('button', { name: '예시 요청 1', exact: true }).click(); await page.locator('.r3-case-drawer[open]').waitFor(); await snap('case-drawer'); await page.keyboard.press('Escape');
    await go('#evaluate/tests', '.test-run-history');
    await page.getByRole('button', { name: '평가지표 펼치기', exact: true }).click(); await snap('tests-expanded'); await page.getByRole('button', { name: '평가지표 접기', exact: true }).click();
    await page.getByRole('button', { name: '기본 테스트 설정', exact: true }).click(); await page.locator('.r5-configuration-form').waitFor(); await snap('test-defaults'); await page.keyboard.press('Escape');
    await page.getByRole('button', { name: '+ 새 테스트', exact: true }).click(); await page.locator('.r5-configuration-form').waitFor(); await snap('new-test');
    await page.getByRole('radio', { name: /개발 테스트/ }).check(); await snap('single-analysis');
    await page.getByRole('tab', { name: '배치 파일 분석', exact: true }).click(); await snap('batch-analysis');
    await page.getByRole('tab', { name: '데이터셋', exact: true }).click(); await snap('dataset-analysis');
    await go('#configure/llm-profiles', '.profile-section');
    await checkPopup(page.getByRole('button', { name: '후보 모델 관리', exact: true }));
    await page.getByRole('button', { name: '모델 추가', exact: true }).click(); await page.getByRole('dialog').waitFor(); await snap('profile-form'); await page.keyboard.press('Escape');
    await page.getByRole('button', { name: '전체 검증', exact: true }).first().click(); await page.getByRole('dialog').waitFor(); await snap('profile-validation'); await page.keyboard.press('Escape');
    await go('#configure/instructions', '.prompt-settings'); await page.getByRole('button', { name: '새 버전 작성', exact: true }).click(); await page.getByRole('dialog').waitFor(); await snap('instructions-editor'); await closeDialog('새 프롬프트 버전 작성');
    await go('#configure/input-schema', '.input-schema-settings'); await page.getByRole('button', { name: '새 버전 작성', exact: true }).click(); await page.getByRole('dialog').waitFor(); await snap('schema-editor');
    await page.getByRole('button', { name: /필드 편집$/ }).first().click(); await page.getByRole('dialog', { name: '필드 편집', exact: true }).waitFor(); await snap('schema-field-editor'); await page.keyboard.press('Escape'); await closeDialog('새 스키마 버전 작성');
    await go('#connect/api-keys', '.service-api-keys'); await page.getByRole('button', { name: '키 발급', exact: true }).click(); await page.getByRole('dialog').waitFor(); await snap('key-issue'); await page.keyboard.press('Escape');
    await page.getByRole('button', { name: '운영 수집기 관리', exact: true }).click(); await page.getByRole('button', { name: '운영 수집기 삭제', exact: true }).click(); await page.getByRole('dialog', { name: 'API 키 삭제', exact: true }).waitFor(); await snap('key-delete'); await page.keyboard.press('Escape');
    await go('#connect/vllm-targets', '.internal-egress-settings'); await page.getByRole('button', { name: '10.1.2.3:8000 편집', exact: true }).click(); await page.getByRole('dialog').waitFor(); await snap('target-editor'); await page.keyboard.press('Escape');
    await go('#operate/inference', '.analysis-data-table'); await page.getByRole('button', { name: '인코딩 요청 검사', exact: true }).click(); await page.locator('.inference-detail').waitFor();
    for (const name of ['판정 결과', 'Agent 실행 이력', 'HTTP 원문', '이벤트 실행 정보', '보고서']) {
      const tabs = page.locator('.inference-detail > .detail-tabs button');
      const index = ['판정 결과', 'Agent 실행 이력', 'HTTP 원문', '이벤트 실행 정보', '보고서'].indexOf(name);
      await tabs.nth(index).click(); await snap('analysis-tab-' + index);
    }
    if (width === 390) { await page.getByRole('button', { name: '메뉴 열기', exact: true }).click(); await snap('navigation'); await page.keyboard.press('Escape'); }
    await go('#overview', '.r3-overview');
    if (width === 1440) {
      assert.equal(await page.locator('.console-brand').evaluate(el => el.getBoundingClientRect().bottom), await page.locator('.console-header').evaluate(el => el.getBoundingClientRect().bottom), 'Brand and header dividers align');
      await page.evaluate(() => scrollTo(0, 700));
      assert.equal(await page.locator('.console-header').evaluate(el => el.getBoundingClientRect().top), 0, 'Desktop header stays accessible'); await snap('scrolled-header');
    } else { await page.evaluate(() => scrollTo(0, 500)); assert.equal(await page.locator('.console-header').evaluate(el => el.getBoundingClientRect().bottom < 0), true, 'Mobile header does not occupy reading space'); }
  }
  for (const width of [360, 768, 1024]) {
    await page.setViewportSize({ width, height: 900 }); prefix = 'width-' + width;
    for (const [name, route, selector] of routes) { await go(route, selector); await snap(name); }
    await go('#evaluate/tests/' + run, '.test-run-detail'); await page.getByRole('tab', { name: '평가 상세', exact: true }).click(); await snap('evaluation');
  }
  for (const color of ['SK', 'Light', 'Dark']) {
    for (const locale of ['KR', 'EN']) {
      await page.getByRole('button', { name: /^(테마|Theme)$/, exact: true }).click(); await page.getByRole('button', { name: color, exact: true }).click();
      await page.getByRole('button', { name: locale, exact: true }).click();
      for (const width of [1440, 390]) {
        prefix = (width === 1440 ? 'desktop' : 'mobile') + '-' + color + '-' + locale;
        await page.setViewportSize({ width, height: 900 });
        await go('#overview', '.r3-overview'); await snap('home');
        await go('#evaluate/tests/' + run, '.test-run-detail'); await page.getByRole('tab', { name: locale === 'KR' ? '평가 상세' : 'Evaluation Details', exact: true }).click(); await snap('evaluation');
        const tab = page.getByRole('tab', { name: locale === 'KR' ? '평가 상세' : 'Evaluation Details', exact: true });
        await page.keyboard.press('Tab'); await tab.focus(); assert.notEqual(await tab.evaluate(el => getComputedStyle(el).outlineStyle), 'none');
        const nav = page.locator('.navigation-action').first(); await nav.focus(); assert.notEqual(await nav.evaluate(el => getComputedStyle(el).outlineStyle), 'none');
      }
    }
  }
  await page.getByRole('button', { name: 'KR', exact: true }).click();
  await page.getByRole('button', { name: '계정', exact: true }).click(); await page.getByRole('button', { name: '로그아웃', exact: true }).click(); await page.locator('.login-card').waitFor();
  for (const width of [1440, 390]) { prefix = width === 1440 ? 'desktop' : 'mobile'; await page.setViewportSize({ width, height: 900 }); await snap('login'); }
  assert.deepEqual(errors, []); assert.deepEqual(network, []); assert.deepEqual(await page.evaluate(() => window.fixture.unknown), []);
  writeFileSync(join(output, 'audit.json'), JSON.stringify(results, null, 2));
  if (process.env.AUDIT_ONLY !== '1') assert.deepEqual(results.filter(row => row.scrollWidth > row.width), [], 'Page must not overflow; wide tables scroll within their frame.');
  console.log(results.length + ' offline browser screenshots saved to ' + output);
} finally { writeFileSync(join(output, 'audit.json'), JSON.stringify(results, null, 2)); await browser.close(); }
