// R5 offline React workflows, themes, locales and accessibility. Synthetic API fixtures only, offline.
// Reuses test data, never an older handoff or visual mockup.
import assert from "node:assert/strict";
import { existsSync, readdirSync, mkdirSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { build } from "esbuild";
import { r5BrowserFixture } from "./r5-browser-fixture.mjs";

const source = fileURLToPath(new URL("../src", import.meta.url));
const output = process.env.SCREENSHOT_DIR || fileURLToPath(new URL("../../test-results/r5-final", import.meta.url));
mkdirSync(output, { recursive: true });
const mock = r5BrowserFixture();

const archive = join(homedir(), ".cache/uv/archive-v0");
const driver = process.env.PLAYWRIGHT_MODULE || readdirSync(archive).map(name => join(archive, name, "playwright/driver/package/index.mjs")).find(existsSync);
const { chromium } = await import(pathToFileURL(driver).href);
const bundle = await build({ stdin: { contents: `import {createRoot} from 'react-dom/client';import App from './App.jsx';import './styles.css';import './console.css';import './lifecycle.css';import './r3.css';createRoot(document.getElementById('root')).render(<App/>);`, loader: "jsx", resolveDir: source },
  plugins: [{ name: "offline-api", setup(b) { b.onLoad({ filter: /\/api\.js$/ }, () => ({ contents: mock, loader: "js" })); } }],
  bundle: true, write: false, outfile: join(output, "bundle.js"), platform: "browser", format: "iife", jsx: "automatic", define: { "process.env.NODE_ENV": '"production"' }, loader: { ".md": "text", ".woff2": "dataurl" }, logLevel: "silent" });
const browser = await chromium.launch({ executablePath: process.env.CHROMIUM_PATH || join(homedir(), ".cache/ms-playwright/chromium-1208/chrome-linux64/chrome"), headless: true, args: ["--no-sandbox"] });
try {
  const page=await browser.newPage({viewport:{width:1440,height:900}});page.setDefaultTimeout(10000);
  const errors=[], requests=[];page.on("pageerror",e=>{errors.push(e.message);console.error(e.message)});
  await page.route("**/*",r=>r.request().url()==="https://fixture.invalid/"?r.fulfill({contentType:"text/html",body:'<html lang="ko"><body><div id="root"></div></body></html>'}):(requests.push(r.request().url()),r.abort()));
  await page.goto("https://fixture.invalid/");
  await page.addStyleTag({content:bundle.outputFiles.find(f=>f.path.endsWith(".css")).text});
  await page.addScriptTag({content:bundle.outputFiles.find(f=>f.path.endsWith(".js")).text});
  const go=async hash=>{await page.evaluate(hash=>{location.hash=hash},hash);};
  const snap=async name=>{await page.evaluate(()=>{scrollTo(0,0)});await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));await page.screenshot({path:join(output,name+".png"),fullPage:true})};
  const theme=async value=>{await page.getByRole("button",{name:/^(테마|Theme)$/,exact:true}).click();await page.getByRole("button",{name:value,exact:true}).click();};
  const run='00000008-1111-4111-8111-111111111111';
  await page.locator(".r3-overview").waitFor();
  for(const color of ["SK","Light","Dark"]){
    await theme(color);
    for(const locale of ["KR","EN"]){
      await page.getByRole("button",{name:locale,exact:true}).click();
      await go("#overview");await page.locator(".r3-evaluation-chart").waitFor();await snap("home-"+locale+"-"+color);
      await go("#evaluate/tests/"+run);await page.locator(".test-run-detail").waitFor();
      await page.getByRole("tab",{name:locale==="KR"?"평가 상세":"Evaluation Details",exact:true}).click();
      await page.locator(".r5-matrix").waitFor();assert.equal(await page.locator(".r5-matrix-cell").count(),4);
      assert.equal(await page.locator(".test-run-detail > .v5-metric-strip").count(),0);
      await snap("test-evaluation-"+locale+"-"+color);
      const focused=page.getByRole("tab",{name:locale==="KR"?"평가 상세":"Evaluation Details",exact:true});await focused.focus();
      if(color!=="Dark")assert.notEqual(await focused.evaluate(el=>getComputedStyle(el).color),"rgb(255, 255, 255)");
      await page.getByRole("button",{name:locale==="KR"?"운영 반영 검토 →":"Production Review →",exact:true}).click();
      await page.locator(".v5-preflight").waitFor();await snap("production-review-"+locale+"-"+color);
      assert.equal(await page.locator(".brand-mutation").isDisabled(),true);
      await go("#evaluate/ground-truth");await page.getByLabel(locale==="KR"?"데이터셋":"Dataset",{exact:true}).selectOption("00000007-1111-4111-8111-111111111111");
      await page.getByRole("button",{name:"SQLi 경계 사례",exact:true}).click();await page.locator(".r3-draft-editor").waitFor();await snap("ground-truth-"+locale+"-"+color);
      assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true,locale+color);
    }
  }
  await page.getByRole("button",{name:"KR",exact:true}).click();await theme("SK");
  await go("#evaluate/tests");await page.getByRole("button",{name:"기본 테스트 설정",exact:true}).click();
  await page.getByRole("dialog").waitFor();await page.getByRole("button",{name:"기본 설정 저장",exact:true}).waitFor();
  await snap("default-test-configuration");await page.getByRole("button",{name:"기본 설정 저장",exact:true}).click();await page.getByRole("status").filter({hasText:"기본 테스트 설정을 저장"}).waitFor();await page.keyboard.press("Escape");
  assert.equal(await page.getByRole("button",{name:"기본 테스트 설정",exact:true}).evaluate(el=>el===document.activeElement),true);
  await page.getByRole("button",{name:"+ 새 테스트",exact:true}).click();await page.locator(".r5-configuration-form").waitFor();await snap("new-official-test");
  await page.getByRole("radio",{name:/개발 테스트/}).check();await page.getByRole("tab",{name:"단건 분석",exact:true}).waitFor();
  await page.locator(".form-panel .test-name-field input").fill("fixture retained draft");
  await page.getByRole("button",{name:"EN",exact:true}).click();assert.equal(await page.getByRole("radio",{name:/Development Test/}).isChecked(),true);
  assert.equal(await page.locator(".form-panel .test-name-field input").inputValue(),"fixture retained draft");
  const directTab=page.getByRole("tab",{name:"Single Analysis",exact:true});await directTab.focus();await page.keyboard.press("ArrowRight");
  assert.equal(await directTab.getAttribute("aria-selected"),"true");await page.keyboard.press("Enter");
  await page.getByRole("tabpanel").filter({has:page.locator(".upload-panel")}).waitFor({state:"visible"});
  await page.getByRole("button",{name:"KR",exact:true}).click();
  await go("#evaluate/tests/"+run);await page.getByRole("button",{name:"이 설정으로 다시 테스트",exact:true}).click();await page.locator(".r5-configuration-form").waitFor();
  await page.getByText("더 최신 공식 버전이 있지만 이전 테스트의 버전을 유지했습니다.").waitFor();await snap("clone-pinned-version");
  await go("#evaluate/tests/"+run);await page.getByRole("button",{name:"테스트 작업",exact:true}).click();
  await page.getByRole("button",{name:"테스트 사례를 정답 데이터에 추가",exact:true}).click();await page.getByRole("button",{name:"미리보기",exact:true}).click();
  await page.locator(".r5-import-summary").waitFor();await snap("import-preview");
  await page.getByRole("button",{name:"편집 중 데이터에 추가",exact:true}).click();await page.getByRole("button",{name:"정답 데이터 열기",exact:true}).click();
  await page.locator(".r3-ground-truth").waitFor();
  await go("#evaluate/tests/"+run);await page.getByRole("button",{name:"예시 요청 1",exact:true}).click();await page.locator(".r3-case-drawer[open]").waitFor();
  await page.getByRole("button",{name:/크게 보기/}).click();await page.locator(".inference-detail").waitFor();
  assert.match(new URL(page.url()).hash,/^#evaluate\/tests\/.*\/case\/.*\/result$/);
  await page.goBack();await page.locator(".r3-case-drawer[open]").waitFor();await page.keyboard.press("Escape");
  await go("#operate/inference");await page.locator(".analysis-data-table").waitFor();
  assert.equal(await page.evaluate(()=>window.fixture.searchQuery.analysis_purpose),"production");
  await snap("production-inference");
  await page.getByRole("button",{name:"인코딩 요청 검사",exact:true}).click();await page.locator(".r5-initial").waitFor();await snap("initial-deep-comparison");
  await go("#promotion/"+run);await page.evaluate(()=>{window.fixture.incompatible=true});await page.getByRole("button",{name:"다시 확인",exact:true}).click();
  await page.locator(".v5-preflight").waitFor();assert.equal(await page.locator(".brand-mutation").isDisabled(),true);await snap("production-review-blocked");
  for(const state of ["unconfigured","legacy_active"]){await page.evaluate(state=>{window.fixture.productionState=state},state);await go("#overview");await page.locator(state==="unconfigured"?".r5-setup":".r3-overview").waitFor();await snap("home-"+state);await go("#evaluate/tests");}
  for(const [route,selector] of [["#configure/llm-profiles",".profile-section"],["#configure/agent-roles",".r5-readonly-roles"],["#configure/instructions",".prompt-settings"],["#configure/input-schema",".input-schema-settings"],["#operate/runtime",".agent-settings-panel"],["#operate/activity",".v5-workbench"],["#connect/production-api",".api-document-page"],["#connect/api-keys",".service-api-keys"],["#connect/vllm-targets",".internal-egress-settings"]]){await go(route);await page.locator(selector).first().waitFor();await snap(route.replaceAll(/[\/#]/g,"-"));assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true,route);}
  await page.emulateMedia({reducedMotion:"reduce"});
  for(const width of [1280,1024]){await page.setViewportSize({width,height:900});await go("#evaluate/tests/"+run);await page.locator(".test-run-header").waitFor();assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true,"narrow "+width);await snap("test-detail-"+width);}
  await page.setViewportSize({width:1440,height:900});await page.evaluate(()=>{window.fixture.incompatible=false});
  await go("#promotion/"+run);await page.locator(".v5-preflight").waitFor();
  assert.equal(await page.locator(".brand-mutation").isDisabled(),true);
  await page.getByRole("checkbox",{name:"필드 변경과 수집기 영향을 확인했습니다.",exact:true}).check();
  await page.getByRole("button",{name:"EN",exact:true}).click();
  assert.equal(await page.getByRole("checkbox",{name:"I reviewed the field changes and client impact.",exact:true}).isChecked(),true);
  await page.locator(".brand-mutation").evaluate(el=>{el.click();el.click()});
  await page.waitForFunction(()=>window.fixture.writes.some(row=>row.name==="promoteConfiguration"));
  assert.deepEqual(errors,[]);assert.deepEqual(requests,[]);assert.deepEqual(await page.evaluate(()=>window.fixture.unknown),[]);
  const writes=await page.evaluate(()=>window.fixture.writes);assert.equal(writes.filter(r=>r.name==="saveTestDefaults").length,1);assert.equal(writes.filter(r=>r.name==="confirmTestImport").length,1);assert.equal(writes.filter(r=>r.name==="promoteConfiguration").length,1);
  console.log("R5: themes × locales, inline evaluation, defaults, pinned clone, preview/confirm, Production scope, case/back/focus and screenshots passed. No network or real data.");
} finally {await browser.close();}
