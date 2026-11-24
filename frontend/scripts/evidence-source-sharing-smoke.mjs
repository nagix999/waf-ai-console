// Real components and synthetic data only. Every non-fixture request is denied.
import assert from "node:assert/strict";
import { existsSync, mkdtempSync, readdirSync } from "node:fs";
import { homedir, tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { build } from "esbuild";

const root = fileURLToPath(new URL("../src/", import.meta.url));
const output = mkdtempSync(join(tmpdir(), "waf-source-sharing-"));
const archive = join(homedir(), ".cache/uv/archive-v0");
const driver = process.env.PLAYWRIGHT_MODULE || readdirSync(archive).map(name => join(archive, name, "playwright/driver/package/index.mjs")).find(existsSync);
assert.ok(driver, "Local browser driver required");
const { chromium } = await import(pathToFileURL(driver).href);
const excerpt = "Cookie: SID=synthetic <img src=https://never.invalid/canary>\r\nRange: bytes=0-65536  ";
const evidence = [
  { evidence_id: "e3", field: "payload", excerpt, source_spans: [{ start: 20, end: 120 }], supports: "attack_request", interpretation_ko: "민감한 자료를 내려받으려는 요청 문맥을 뒷받침합니다.", origins: [{ role: "primary", index: 0, source_id: "c1" }] },
  { evidence_id: "e6", field: "payload", excerpt, source_spans: [{ start: 20, end: 120 }], supports: "normal_request", interpretation_ko: "반대 방향의 정상 사용 해석도 별도로 유지합니다.", origins: [{ role: "verifier", index: 0, source_id: "c1" }] },
  { evidence_id: "e4", field: "payload", excerpt, source_spans: [{ start: 200, end: 300 }], supports: "context", interpretation_ko: "다른 원문 위치는 별도 발췌로 유지합니다.", origins: [{ role: "primary", index: 1, source_id: "c2" }] },
];
const detail = { id: "synthetic-only", status: "completed", result: { schema_version: "waf-analysis-v3", verdict: "inconclusive", confidence_score: .5, summary_ko: "요청 행위와 두 가지 보안 해석을 확인했습니다.", evidence, decision_issues: [], threat_analysis: { severity: "UNKNOWN", category: "request", technique_ko: "서로 다른 해석을 원문과 대조했습니다.", potential_impact_ko: "관측한 요청만으로 실제 처리 결과는 확인하지 못했습니다." } } };
const bundle = await build({ stdin: { resolveDir: root, loader: "jsx", contents: `
  import React, {useState} from 'react'; import {createRoot} from 'react-dom/client';
  import {EvidenceCards,EvidenceWorkspace} from './AnalysisDecision.jsx';
  import AnalysisReport from './AnalysisReport.jsx';
  import {ConsolePreferencesProvider} from './consolePreferences.jsx';
  import {rememberEvidenceMeta} from './analysisEvidenceMeta.js';
  import './styles.css'; import './console.css'; import './lifecycle.css'; import './r3.css';
  const detail=${JSON.stringify(detail)}; rememberEvidenceMeta(detail,{view:'core-v1',status:'completed'});
  function App(){const[mode,setMode]=useState('cards'),[selected,setSelected]=useState(null);
    window.showMode=setMode; window.sharingDetail=detail;
    const select=item=>{window.fixtureSelection=item;setSelected(item)};
    return <ConsolePreferencesProvider><main className="inference-detail" style={{maxWidth:1180,margin:'0 auto',padding:24}}>
      {mode==='cards'?<EvidenceCards detail={detail} onViewInput={select}/>:mode==='workspace'?<EvidenceWorkspace detail={detail} target={selected} onSelect={select} active={false}/>:<AnalysisReport detail={detail} mode="preview" onModeChange={()=>{}}/>}
    </main></ConsolePreferencesProvider>};createRoot(document.getElementById('root')).render(<App/>);
  ` }, bundle: true, write: false, outfile: join(output, "bundle.js"), platform: "browser", format: "iife", jsx: "automatic", define: { "process.env.NODE_ENV": '"production"' }, loader: { ".md": "text", ".woff2": "dataurl" }, logLevel: "silent" });
const browser = await chromium.launch({ executablePath: process.env.CHROMIUM_PATH || join(homedir(), ".cache/ms-playwright/chromium-1208/chrome-linux64/chrome"), headless: true, args: ["--no-sandbox"] });
try {
  for (const [theme, locale, width] of [["sk", "ko", 1440], ["light", "ko", 1440], ["dark", "ko", 1440], ["sk", "en", 1440], ["light", "en", 1440], ["dark", "en", 1440], ["sk", "ko", 390], ["dark", "en", 390]]) {
    const context = await browser.newContext({ viewport: { width, height: 1050 }, reducedMotion: "reduce", serviceWorkers: "block" });
    const errors = [], network = [];
    try {
      await context.addInitScript(({theme,locale})=>{localStorage.setItem('waf-console-theme',theme);localStorage.setItem('waf-console-locale',locale)}, {theme,locale});
      await context.route("**/*", route => route.request().url().split("#")[0] === "https://fixture.invalid/" ? route.fulfill({ contentType: "text/html", body: '<html><meta name="viewport" content="width=device-width, initial-scale=1"><body><div id="root"></div></body></html>' }) : (network.push(route.request().url()), route.abort()));
      const page = await context.newPage(); page.setDefaultTimeout(10000);
      page.on("pageerror", error => errors.push(error.message));
      await page.goto("https://fixture.invalid/#analyses/synthetic-only");
      await page.addStyleTag({content:bundle.outputFiles.find(file=>file.path.endsWith('.css')).text});
      await page.addScriptTag({content:bundle.outputFiles.find(file=>file.path.endsWith('.js')).text});
      await page.locator(".decision-core-evidence").first().waitFor();
      assert.equal(await page.locator(".decision-core-evidence").count(), 3);
      assert.equal(await page.locator(".decision-evidence-content > code").count(), 2);
      const jump = page.locator(".decision-shared-source");
      assert.equal(await jump.count(), 1);
      assert.match(await jump.innerText(), locale === "ko" ? /같은 원문 보기 \(근거 3\)/ : /View the source shared with evidence 3/);
      await jump.focus(); await page.keyboard.press("Enter");
      assert.equal(await page.evaluate(()=>document.activeElement.textContent), excerpt);
      assert.equal(await page.evaluate(()=>document.activeElement.id), await jump.getAttribute("aria-controls"));
      assert.match(page.url(), /#analyses\/synthetic-only$/);
      const sourceButton=page.getByRole('button',{name:locale==='ko'?'근거 6 원문 보기':'View evidence 6 source',exact:true});
      await sourceButton.focus(); await page.keyboard.press('Enter');
      const selection=await page.evaluate(()=>window.fixtureSelection);
      assert.equal(selection.evidence_id,'e6'); assert.equal(selection.supports,'normal_request');
      assert.deepEqual(selection.source_spans,[{start:20,end:120}]);
      assert.equal(await page.locator('img,script[src]').count(),0);
      await page.screenshot({path:join(output,`${theme}-${locale}-${width}-cards.png`),fullPage:true});
      await page.evaluate(()=>window.showMode('workspace'));
      const sourceRows=page.locator('.decision-source-list button'); await sourceRows.first().waitFor();
      assert.equal(await sourceRows.count(),3);
      assert.equal(await page.locator('.decision-source-list code').count(),2);
      await sourceRows.nth(1).focus(); await page.keyboard.press('Enter');
      assert.equal(await page.evaluate(()=>window.fixtureSelection.evidence_id),'e6');
      await page.getByText('반대 방향의 정상 사용 해석도 별도로 유지합니다.',{exact:true}).waitFor();
      await page.evaluate(()=>window.showMode('report'));
      await page.locator('.report-paper').waitFor();
      assert.equal(await page.locator('.report-excerpt').count(),2);
      await page.getByText('같은 원문은 근거 3에서 확인할 수 있습니다.',{exact:true}).waitFor();
      assert.equal(await page.locator('img,script[src]').count(),0);
      assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));
      await page.screenshot({path:join(output,`${theme}-${locale}-${width}-report.png`),fullPage:true});
      assert.deepEqual(errors,[]); assert.deepEqual(network,[]);
      console.log(`PASS ${theme}/${locale}/${width}: source once, distinct interpretations, keyboard focus/input navigation, inert text, shared report`);
    } finally { await context.close(); }
  }
  console.log("Screenshots: "+output);
} finally { await browser.close(); }
