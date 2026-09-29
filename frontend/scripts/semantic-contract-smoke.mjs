// Real React, synthetic API fixture, all external network blocked.
import assert from 'node:assert/strict';
import { existsSync, readdirSync, mkdirSync } from 'node:fs';
import { homedir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { build } from 'esbuild';
import { r5BrowserFixture } from './r5-browser-fixture.mjs';

const output = fileURLToPath(new URL('../../test-results/followup-policy-v214', import.meta.url));
mkdirSync(output, { recursive: true });
const mock = r5BrowserFixture().replace('export const api=', `
state.detail.event_name='요청 처리 의미 검토'; state.detail.signature='SQL Injection';
state.detail.result.signature_assessment={version:'signature-assessment-v2',relation:'mismatch',explanation_ko:null,matched_points:[],mismatched_points:['SQL 탐지 설명과 달리 HTML 실행 구문입니다.'],uncertainty_ko:null};
state.detail.result.recommended_checks=['LEGACY_FALLBACK_NOT_ALLOWED'];
state.detail.result.analyst_assessment.decision_issues=[];
export const api=`);
const bundle = await build({ stdin: { contents: `import {createRoot} from 'react-dom/client';import App from './App.jsx';import './styles.css';import './console.css';import './lifecycle.css';import './r3.css';createRoot(document.getElementById('root')).render(<App/>);`, loader: 'jsx', resolveDir: fileURLToPath(new URL('../src', import.meta.url)) },
  plugins: [{ name: 'offline-api', setup(b) { b.onLoad({ filter: /\/api\.js$/ }, () => ({ contents: mock, loader: 'js' })); } }],
  bundle: true, write: false, outfile: join(output, 'bundle.js'), platform: 'browser', format: 'iife', jsx: 'automatic', define: { 'process.env.NODE_ENV': '"production"' }, loader: { '.md': 'text', '.woff2': 'dataurl' }, logLevel: 'silent' });
const archive = join(homedir(), '.cache/uv/archive-v0');
const driver = process.env.PLAYWRIGHT_MODULE || readdirSync(archive).map(name => join(archive, name, 'playwright/driver/package/index.mjs')).find(existsSync);
const { chromium } = await import(pathToFileURL(driver).href);
const browser = await chromium.launch({ executablePath: process.env.CHROMIUM_PATH || join(homedir(), '.cache/ms-playwright/chromium-1208/chrome-linux64/chrome'), headless: true, args: ['--no-sandbox'] });
try {
  const page = await browser.newPage({ viewport: { width: 1440, height: 960 }, reducedMotion: 'reduce' });
  const errors = [], network = [], screenshots = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.route('**/*', route => route.request().url().split('#')[0] === 'https://fixture.invalid/' ? route.fulfill({ contentType: 'text/html', body: '<html lang="ko"><head><meta name="viewport" content="width=device-width,initial-scale=1"></head><body><div id="root"></div></body></html>' }) : (network.push(route.request().url()), route.abort()));
  await page.goto('https://fixture.invalid/');
  await page.addStyleTag({ content: bundle.outputFiles.find(f => f.path.endsWith('.css')).text });
  await page.addScriptTag({ content: bundle.outputFiles.find(f => f.path.endsWith('.js')).text });
  page.setDefaultTimeout(10000);
  const go = hash => page.evaluate(hash => { location.hash = hash; }, hash);
  const snap = async name => { await page.evaluate(() => scrollTo(0, 0)); assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true, name); await page.screenshot({ path: join(output, name+'.png'), fullPage: true }); screenshots.push(name); };
  await page.locator('.r3-overview').waitFor();
  for (const color of ['SK', 'Light', 'Dark']) {
    await page.getByRole('button', { name: /^(테마|Theme)$/, exact: true }).click(); await page.getByRole('button', { name: color, exact: true }).click();
    for (const locale of ['KR', 'EN']) {
      await page.getByRole('button', { name: locale, exact: true }).click();
      for (const verdict of ['inconclusive', 'true_positive', 'false_positive']) {
        await go('#overview'); await page.locator('.r3-overview').waitFor();
        await page.evaluate(verdict => {
          const result = fixture.detail.result;
          result.verdict = verdict;
          result.diagnostics = { inconclusive_reasons: verdict === 'inconclusive' ? ['primary_inconclusive'] : [] };
          result.primary = { verdict };
          result.verifier = { executed: false };
          result.threat_analysis.severity = { true_positive: 'HIGH', false_positive: 'NONE', inconclusive: 'UNKNOWN' }[verdict];
          result.summary_ko = verdict === 'inconclusive' ? '입력값을 실행하는지 데이터로 저장하는지에 따라 판정이 달라집니다.' : '기록된 요청의 구문과 처리 맥락을 검토했습니다.';
          result.analyst_guidance = { summary_ko: result.summary_ko, limitations: [], checks: [
            ...(verdict === 'inconclusive' ? [{ purpose: 'decision_condition', source_ko: '애플리케이션 입력 규격', check_ko: '입력값을 실행하는지 데이터로 저장하는지 확인합니다.', why_ko: '공격 실행 구문과 정상 데이터의 의미를 구분합니다.' }] : []),
            { purpose: 'impact_followup', source_ko: '서비스 처리 기록', check_ko: '같은 대상의 후속 피해를 확인합니다.', why_ko: '현재 판정과 별개로 대응 범위를 정합니다.' },
            { purpose: 'tuning_validation', source_ko: '정책 검토 제안', check_ko: '정책 변경 전에 공격 회귀 사례를 확인합니다.', why_ko: '예외를 추가해 공격을 놓치지 않는지 검토합니다.' },
          ] };
          result.policy = { fixed_rules_version: 'waf-system-v2.14' };
          result.tuning_recommendation = { recommended: verdict !== 'true_positive' };
          // API fixture represents the final server policy; the UI must not
          // reconstruct suppressed checks from the preserved model records.
          result.primary.analyst_checks = structuredClone(result.analyst_guidance.checks);
          if (verdict === 'true_positive') result.analyst_guidance.checks = [];
          result.analyst_checks = structuredClone(result.analyst_guidance.checks);
        }, verdict);
        await go('#analyses/11111111-1111-4111-8111-111111111111'); await page.locator('.decision-hero').waitFor();
        assert.equal(await page.locator('[data-purpose="conditions"]').count(), verdict === 'inconclusive' ? 1 : 0);
        assert.equal(await page.locator('[data-purpose="impact"]').count(), verdict === 'true_positive' ? 0 : 1);
        assert.equal(await page.locator('[data-purpose="tuning"]').count(), verdict === 'true_positive' ? 0 : 1);
        assert.doesNotMatch(await page.locator('body').innerText(), /후속 확인 · 선택사항|튜닝 검증 · 선택사항/);
        if (verdict === 'inconclusive') assert.doesNotMatch(await page.locator('body').innerText(), /판정은 이미 확정되었습니다/);
        if (verdict === 'inconclusive') assert.doesNotMatch(await page.locator('[data-purpose="conditions"]').innerText(), /후속 피해|공격 회귀/);
        await page.locator('.decision-technical > details > summary').focus(); await page.keyboard.press('Enter');
        await page.getByText('SQL 탐지 설명과 달리 HTML 실행 구문입니다.', { exact: true }).waitFor();
        assert.doesNotMatch(await page.locator('body').innerText(), /LEGACY_FALLBACK_NOT_ALLOWED/);
        await snap(verdict+'-'+locale+'-'+color);
        if (color === 'Light' && locale === 'KR') {
          await page.setViewportSize({ width: 390, height: 844 }); await snap(verdict+'-mobile');
          await page.setViewportSize({ width: 1440, height: 960 });
        }
      }
    }
  }
  assert.deepEqual(errors, []); assert.deepEqual(network, []);
  console.log(JSON.stringify({ screenshots: screenshots.length, checks: 'TP/FP/HOLD purposes, structured signature, legacy fallback disabled, KR/EN, SK/Light/Dark, mobile overflow, keyboard expansion, no external requests' }));
} finally { await browser.close(); }
