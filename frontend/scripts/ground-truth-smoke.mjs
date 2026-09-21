// Synthetic fixture only. All network requests are blocked; no DB/LLM changes.
import assert from "node:assert/strict";
import { existsSync, readdirSync, readFileSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { build } from "esbuild";

const source = fileURLToPath(new URL("../src", import.meta.url));
const archive = join(homedir(), ".cache/uv/archive-v0");
const driver = process.env.PLAYWRIGHT_MODULE || readdirSync(archive).map(name => join(archive, name, "playwright/driver/package/index.mjs")).find(existsSync);
if (!driver) throw new Error("Existing development browser required; nothing is downloaded");
const { chromium } = await import(pathToFileURL(driver).href);
const mock = `
window.fixture={writes:[],reads:[],revision:2,history:[],stale:false};const state=window.fixture;
const event={event_id:'fixture-event',company_name:'Example',src_ip:'192.0.2.10',dest_ip:'198.51.100.20',src_port:1234,dest_port:443,waf_vendor:'fixture',waf_action:'D',signature:'fixture',event_name:'fixture',payload:'GET /fixture HTTP/1.1\\r\\n\\r\\n'};
state.item={id:'v1',item_id:'item',revision:1,review_status:'draft',reference_verdict:'inconclusive',case_name:'경계 사례',event,comment:'보류 판단 이유',created_by:'fixture',created_at:'2026-09-17T00:00:00Z'};
state.history=[state.item];const clone=value=>JSON.parse(JSON.stringify(value));
export const api={
 validationDataset:async(id,query)=>{state.reads.push(query);const items=query.review_status&&query.review_status!==state.item.review_status?[]:[state.item];
 return {id,name:'검증 fixture',description:'',revision:state.revision,current_revision:state.revision,total:1,labeled:1,filtered_total:items.length,review_counts:{draft:0,reviewed:0,approved:0,[state.item.review_status]:1},items:clone(items),versions:[{revision:state.revision,created_at:state.item.created_at}]};},
 datasetItem:async(id,item,version)=>({...clone(state.history.find(v=>v.id===version)||state.item),history:clone(state.history).reverse()}),
 reviewDatasetItem:async(id,item,payload)=>{state.writes.push(payload);await new Promise(resolve=>setTimeout(resolve,50));if(state.stale)throw new Error('dataset_changed_reload');
 state.revision++;state.item={...state.item,id:'v'+(state.item.revision+1),revision:state.item.revision+1,review_status:payload.review_status};state.history.push(state.item);return {item:clone(state.item),revision:state.revision};},
 saveDatasetItem:async(id,item,payload)=>{state.writes.push(payload);state.revision++;state.item={...state.item,...payload,id:'v'+(state.item.revision+1),revision:state.item.revision+1,review_status:'draft'};state.history.push(state.item);return {item:clone(state.item),revision:state.revision};}
};`;
const bundle = await build({ stdin: { contents: `import {createRoot} from 'react-dom/client';import DataManagement from './DataManagement.jsx';createRoot(document.getElementById('root')).render(<DataManagement id="fixture" onSelect={()=>{}}/>);`, loader: "jsx", resolveDir: source },
  plugins: [{ name: "offline-api", setup(b) { b.onLoad({ filter: /\/api\.js$/ }, () => ({ contents: mock, loader: "js" })); } }],
  bundle: true, write: false, platform: "browser", format: "iife", jsx: "automatic", define: { "process.env.NODE_ENV": '"production"' }, loader: { ".css": "empty" }, logLevel: "silent" });
const css = ["styles.css", "ux.css", "validationData.css", "dataTable.css"].map(file => readFileSync(join(source, file), "utf8")).join("\n");
const browser = await chromium.launch({ executablePath: process.env.CHROMIUM_PATH || join(homedir(), ".cache/ms-playwright/chromium-1208/chrome-linux64/chrome"), headless: true, args: ["--no-sandbox"] });
try {
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
  const errors = []; let network = 0;
  page.on("pageerror", error => errors.push(error.message));
  await page.route("**/*", route => { network++; return route.abort(); });
  await page.setContent('<html lang="ko"><body><main id="root" style="padding:24px;max-width:1320px;margin:auto"></main></body></html>');
  await page.addStyleTag({ content: css }); await page.addScriptTag({ content: bundle.outputFiles[0].text });
  const open = async () => { await page.getByRole("button", { name: "경계 사례", exact: true }).click(); await page.getByRole("tab", { name: "답안·검토", exact: true }).click(); };
  const dialog = page.getByRole("dialog", { name: "검증 문항", exact: true });
  const review = dialog.getByRole("region", { name: "답안 검토" });
  const version = async value => { await dialog.getByRole("tab", { name: "버전 이력", exact: true }).click(); await dialog.getByRole("combobox", { name: "문항 버전", exact: true }).selectOption(value); await dialog.getByRole("tab", { name: "답안·검토", exact: true }).click(); };
  await open();
  assert.equal(await review.getByRole("button", { name: "승인", exact: true }).count(), 0);
  await review.getByRole("button", { name: "검토 완료", exact: true }).click();
  const confirm = review.getByRole("button", { name: "검토 완료 확인", exact: true });
  await confirm.evaluate(button => { button.click(); button.click(); });
  await dialog.waitFor({ state: "hidden" });
  assert.equal(await page.evaluate(() => window.fixture.writes.length), 1);
  await open();
  await review.getByRole("button", { name: "승인", exact: true }).click();
  assert.match(await review.innerText(), /분석이 실행되거나 운영 설정이 변경되지는/);
  await review.getByRole("button", { name: "승인 확인", exact: true }).click();
  await dialog.waitFor({ state: "hidden" });
  const filters = page.getByRole("group", { name: "검토 상태 필터" });
  await filters.getByRole("button", { name: "승인됨 1", exact: true }).click();
  await page.waitForFunction(() => window.fixture.reads.at(-1)?.review_status === 'approved');
  await open();
  await version("v1");
  await page.getByText("이전 버전 · 읽기 전용", { exact: true }).waitFor();
  assert.equal(await review.getByRole("button").count(), 0);
  assert.equal(await dialog.getByRole("combobox", { name: "답안", exact: true }).isDisabled(), true);
  await version("v3");
  await review.getByRole("button", { name: "미검토로 되돌리기", exact: true }).waitFor();
  await dialog.getByRole("combobox", { name: "답안", exact: true }).selectOption("true_positive");
  assert.equal(await review.getByRole("button", { name: "미검토로 되돌리기", exact: true }).isDisabled(), true);
  await dialog.getByRole("button", { name: "수정본 저장", exact: true }).click();
  await dialog.waitFor({ state: "hidden" });
  await page.getByText("이 상태의 문항이 없습니다.", { exact: true }).waitFor();
  await filters.getByRole("button", { name: "미검토 1", exact: true }).click();
  await open();
  assert.equal(await review.getByRole("button", { name: "승인", exact: true }).count(), 0);
  await page.evaluate(() => { window.fixture.stale = true; });
  await review.getByRole("button", { name: "검토 완료", exact: true }).click();
  await review.getByRole("button", { name: "검토 완료 확인", exact: true }).click();
  await dialog.getByRole("alert").waitFor();
  assert.match(await dialog.getByRole("alert").innerText(), /데이터셋이 변경/);
  await page.setViewportSize({ width: 1280, height: 900 });
  assert.equal(await dialog.evaluate(node => node.getBoundingClientRect().right <= innerWidth + 1), true);
  assert.deepEqual(errors, []); assert.equal(network, 0);
  console.log(JSON.stringify({ passed: ["separate review/approval", "confirmation", "double-click guard", "status filter", "historical read-only", "dirty edit guard", "edited approval becomes draft", "stale conflict", "desktop dialog"], actualApiCalls: 0, llmCalls: 0 }));
} finally { await browser.close(); }
