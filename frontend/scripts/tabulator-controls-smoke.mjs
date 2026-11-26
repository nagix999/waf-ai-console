// Common list adapter: real React + Tabulator, no API or network data.
import assert from "node:assert/strict";
import { existsSync, readdirSync, mkdirSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { build } from "esbuild";

const output = fileURLToPath(new URL("../../test-results/tabulator-controls", import.meta.url));
mkdirSync(output, { recursive: true });
const bundle = await build({ stdin: { resolveDir: fileURLToPath(new URL("../src", import.meta.url)), loader: "jsx", contents: `
import {StrictMode,useMemo,useState} from 'react';import {createRoot} from 'react-dom/client';
import DataTable from './DataTable.jsx';import {ConsolePreferencesProvider} from './consolePreferences.jsx';
import './styles.css';import './console.css';
const rows=[{id:'zulu',name:'Zulu',value:17},{id:'same',name:'Alpha',value:3},{id:'same',name:'<img src=x onerror="window.injected=true">',value:9}];
window.tableState={sorts:[],opened:[]};
function Fixture(){const [sorting,setSorting]=useState([]),[selected,setSelected]=useState([]),[empty,setEmpty]=useState(false),[disabled,setDisabled]=useState(false),[failed,setFailed]=useState(false),[revision,setRevision]=useState(0),[editing,setEditing]=useState(false);
const data=useMemo(()=>empty?[]:rows.map(row=>({...row,revision})),[empty,revision]);window.tableState.refresh=()=>setRevision(v=>v+1);window.tableState.empty=()=>setEmpty(true);window.tableState.show=()=>setEmpty(false);window.tableState.edit=value=>setEditing(value);
return <ConsolePreferencesProvider><button onClick={()=>setEmpty(v=>!v)}>Toggle empty</button><button onClick={()=>setDisabled(v=>!v)}>Toggle disabled</button><button onClick={()=>setFailed(v=>!v)}>Toggle failed</button>
<DataTable data={data} label="Synthetic controls" getRowId={row=>row.id} sorting={sorting} onSortingChange={next=>{window.tableState.sorts.push(next);setSorting(next);}} onRowClick={row=>window.tableState.opened.push(row.name)} rowClassName={()=>failed?'analysis-row-failed':''}
headerGroups={[{label:'Selection',span:1},{label:<span>Case details</span>,span:2},{label:'Actions',span:1}]} columns={[
{id:'select',width:44,className:'selection-cell',header:<input type="checkbox" aria-label="Select page" checked={selected.length===3} disabled={disabled} onChange={event=>setSelected(event.target.checked?[0,1,2]:[])}/>,render:(row,index)=><input type="checkbox" aria-label={'Select '+index} checked={selected.includes(index)} disabled={disabled} onChange={event=>setSelected(event.target.checked?[...selected,index]:selected.filter(i=>i!==index))}/>},
{id:'name',header:'Name',sortable:true,render:row=><strong>{row.name}</strong>},
{id:'value',header:<span>Count</span>,className:'numeric',width:100,sortable:true,render:row=>row.value+row.revision},
{id:'action',header:<span className="sr-only">Actions</span>,width:140,render:row=>editing?<input aria-label={'Edit '+row.name} defaultValue={row.name}/>:<button type="button" onClick={()=>window.tableState.opened.push('button:'+row.name)}>Open {row.name==='Alpha'?'Alpha':'case'}</button>}
]}/></ConsolePreferencesProvider>}
createRoot(document.getElementById('root')).render(<StrictMode><Fixture/></StrictMode>);` }, bundle: true, write: false,
  outfile: join(output, "fixture.js"), platform: "browser", format: "iife", jsx: "automatic", logLevel: "silent", define: { "process.env.NODE_ENV": JSON.stringify(process.env.REACT_TEST_MODE || "production") }, loader: { ".woff2": "dataurl" } });
const archive = join(homedir(), ".cache/uv/archive-v0");
const driver = process.env.PLAYWRIGHT_MODULE || readdirSync(archive).map(name => join(archive, name, "playwright/driver/package/index.mjs")).find(existsSync);
if (!driver) throw Error("An existing browser is required; no browser download is attempted.");
const { chromium } = await import(pathToFileURL(driver).href);
const browser = await chromium.launch({ executablePath: process.env.CHROMIUM_PATH || join(homedir(), ".cache/ms-playwright/chromium-1208/chrome-linux64/chrome"), headless: true, args: ["--no-sandbox"] });
try {
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 }, reducedMotion: "reduce" });
  const errors = [], traffic = [];
  page.on("pageerror", error => errors.push(error.message));
  await page.route("**/*", route => route.request().url() === "https://fixture.invalid/" ? route.fulfill({ contentType: "text/html", body: '<html><head><meta name="viewport" content="width=device-width,initial-scale=1"></head><body><div id="root" style="padding:30px;max-width:1100px;margin:auto"></div></body></html>' }) : (traffic.push(route.request().url()), route.abort()));
  await page.goto("https://fixture.invalid/");
  await page.addStyleTag({ content: bundle.outputFiles.find(file => file.path.endsWith(".css")).text });
  await page.addScriptTag({ content: bundle.outputFiles.find(file => file.path.endsWith(".js")).text });
  const table = page.getByRole("region", { name: "Synthetic controls", exact: true });
  const rows = table.locator(".tabulator-row");
  await rows.first().getByText("Zulu", { exact: true }).waitFor();
  assert.equal(await rows.count(), 3);
  assert.equal(await table.locator('.selection-cell input').evaluateAll(nodes => nodes.every(node => node.getBoundingClientRect().width >= 16)), true, "selection controls keep a 16px target");
  assert.match(await rows.nth(2).innerText(), /<img src=x/);
  assert.equal(await table.locator("img,script").count(), 0);
  assert.equal(await page.evaluate(() => Boolean(window.injected)), false);
  await page.screenshot({ path: join(output, "headers-initial.png"), fullPage: true });
  assert.equal(await table.getByRole("checkbox", { name: "Select page", exact: true }).isVisible(), true, "rich headers retain a visible selection target");
  await table.getByRole("checkbox", { name: "Select page", exact: true }).check();
  assert.equal(await table.locator('input[type="checkbox"]:checked').count(), 4, "React selection headers and cells update together");
  await table.getByRole("button", { name: "Name", exact: false }).click();
  await table.locator('.tabulator-col[aria-sort="ascending"]').waitFor();
  assert.equal(await rows.first().locator("strong").innerText(), "Zulu", "server page must not sort locally");
  await table.getByRole("button", { name: "Name", exact: false }).click();
  await table.locator('.tabulator-col[aria-sort="descending"]').waitFor();
  assert.equal(await page.evaluate(() => window.tableState.sorts.at(-1)[0].desc), true);
  assert.equal(await table.getByText("Case details", { exact: true }).count(), 1, "React group heading is rendered");
  assert.equal(await table.evaluate(node => {const header=node.querySelector('.tabulator-col[tabulator-field="name"]'),cell=node.querySelector('.tabulator-cell[tabulator-field="name"]');return Math.abs(header.getBoundingClientRect().left-cell.getBoundingClientRect().left)<2;}),true,"grouped column headings align with their rows");
  await rows.first().focus(); await page.keyboard.press("ArrowDown");
  assert.equal(await rows.nth(1).evaluate(node => document.activeElement === node), true);
  await page.keyboard.press("Enter");
  assert.equal(await page.evaluate(() => window.tableState.opened.at(-1)), "Alpha");
  await rows.nth(1).getByRole("button", { name: "Open Alpha", exact: true }).click();
  assert.deepEqual(await page.evaluate(() => window.tableState.opened), ["Alpha", "button:Alpha"], "button click is not duplicated by row activation");
  await rows.nth(1).focus();
  await page.evaluate(() => { window.oldFocusedRow = document.activeElement; window.beforeRowRefreshScroll = scrollY; window.tableState.refresh(); });
  await rows.nth(1).locator('[tabulator-field="value"]').getByText("4", { exact: true }).waitFor();
  assert.equal(await page.evaluate(() => oldFocusedRow.isConnected && document.activeElement === oldFocusedRow), true, "same-key data refresh preserves the row DOM");
  await page.waitForFunction(() => document.activeElement?.classList.contains("tabulator-row") && document.activeElement.textContent.includes("Alpha"));
  assert.equal(await rows.nth(1).evaluate(node => document.activeElement === node), true, "the same keyboard-navigable row retains focus after polling");
  assert.equal(await page.evaluate(() => scrollY === beforeRowRefreshScroll), true, "row focus restoration does not scroll");
  await page.evaluate(() => { window.tableState.refresh(); document.querySelector("button").focus(); });
  await page.waitForTimeout(100);
  assert.equal(await page.getByRole("button", { name: "Toggle empty", exact: true }).evaluate(node => document.activeElement === node), true, "row refresh never takes focus from another control");
  // Same-key polling updates data without replacing the row, cell portal or
  // its focused control. A different data scope still uses replacement.
  await rows.nth(1).getByRole("button", { name: "Open Alpha", exact: true }).focus();
  await page.evaluate(() => { window.oldFocusedControl = document.activeElement; window.tableState.refresh(); });
  await rows.nth(1).locator('[tabulator-field="value"]').getByText("6", { exact: true }).waitFor();
  assert.equal(await page.evaluate(() => oldFocusedControl.isConnected && document.activeElement === oldFocusedControl), true, "same-key polling keeps the existing cell control");
  await page.waitForFunction(() => document.activeElement?.textContent === "Open Alpha");
  assert.equal(await rows.nth(1).getByRole("button", { name: "Open Alpha", exact: true }).evaluate(node => document.activeElement === node), true, "same duplicate-id occurrence and cell retain focus");
  await page.evaluate(() => { window.tableState.refresh(); document.querySelector("button").focus(); });
  await page.waitForTimeout(100);
  assert.equal(await page.getByRole("button", { name: "Toggle empty", exact: true }).evaluate(node => document.activeElement === node), true, "refresh does not steal focus from another control");
  await page.evaluate(() => window.tableState.edit(true));
  await rows.nth(1).getByRole("textbox", { name: "Edit Alpha", exact: true }).focus();
  await page.evaluate(() => { document.activeElement.setSelectionRange(1, 4); window.tableState.refresh(); });
  await page.waitForFunction(() => document.activeElement?.getAttribute("aria-label") === "Edit Alpha" && document.activeElement.selectionStart === 1 && document.activeElement.selectionEnd === 4);
  await page.evaluate(() => window.tableState.edit(false));
  await rows.nth(1).getByRole("button", { name: "Open Alpha", exact: true }).focus();
  await page.evaluate(() => window.tableState.empty());
  await table.getByText("표시할 항목이 없습니다.", { exact: true }).waitFor();
  assert.equal(await page.evaluate(() => document.activeElement === document.body), true, "removing the focused row does not choose another row");
  await page.evaluate(() => window.tableState.show());
  await rows.nth(1).getByRole("button", { name: "Open Alpha", exact: true }).waitFor();
  assert.equal(await page.evaluate(() => document.activeElement === document.body), true, "a later reappearance never restores stale row focus");
  await rows.nth(1).focus();
  await page.evaluate(() => window.tableState.empty());
  await table.getByText("표시할 항목이 없습니다.", { exact: true }).waitFor();
  assert.equal(await page.evaluate(() => document.activeElement === document.body), true, "removing a keyboard-focused row has no fallback focus");
  await page.evaluate(() => window.tableState.show());
  await rows.nth(1).getByRole("button", { name: "Open Alpha", exact: true }).waitFor();
  assert.equal(await page.evaluate(() => document.activeElement === document.body), true, "reappearing rows never restore stale keyboard focus");
  await page.getByRole("button", { name: "Toggle disabled", exact: true }).click();
  assert.equal(await table.locator('input[type="checkbox"]:disabled').count(), 4);
  await page.getByRole("button", { name: "Toggle failed", exact: true }).click();
  assert.equal(await table.locator(".tabulator-row.analysis-row-failed").count(), 3);
  await page.getByRole("button", { name: "Toggle failed", exact: true }).click();
  assert.equal(await table.locator(".tabulator-row.analysis-row-failed").count(), 0, "old state classes are cleared");
  await page.getByRole("button", { name: "Toggle empty", exact: true }).click();
  await table.getByText("표시할 항목이 없습니다.", { exact: true }).waitFor();
  assert.equal(await rows.count(), 0);
  await page.getByRole("button", { name: "Toggle empty", exact: true }).click();
  await rows.first().getByText("Zulu", { exact: true }).waitFor();
  assert.equal(await rows.count(), 3);
  assert.equal(await table.locator(".numeric").evaluateAll(nodes => nodes.every(node => getComputedStyle(node).textAlign === "right")), true);
  assert.equal(await table.locator(".tabulator-tableholder").evaluate(node => node.scrollHeight <= node.clientHeight + 1), true, "automatic-height tables must not clip portal rows");
  await page.screenshot({ path: join(output, "common-table.png"), fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true, "table scroll remains inside the page");
  await page.screenshot({ path: join(output, "common-table-mobile.png"), fullPage: true });
  assert.deepEqual(errors, []);
  assert.deepEqual(traffic, []);
  console.log("Tabulator controls: sorting, grouped headers, selection, duplicate ids, escaping, keyboard and refresh focus, row states, empty state and mobile overflow passed.");
} finally { await browser.close(); }
