// Offline browser regression: fake clock, synthetic APIs, no service/LLM calls.
import assert from "node:assert/strict";
import { existsSync, readdirSync, mkdirSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { build } from "esbuild";
import { r5BrowserFixture } from "./r5-browser-fixture.mjs";

const source = fileURLToPath(new URL("../src", import.meta.url));
const output = fileURLToPath(new URL("../../test-results/sqlite-polling", import.meta.url));
mkdirSync(output, { recursive: true });
const mock = r5BrowserFixture().replace("export const api=", `
state.homeRevision='initial';state.runState='completed';state.officialPending=false;
const oldActivity=methods.activity,oldRun=methods.testRun;
methods.activity=async (...args)=>({...await oldActivity(...args),home_revision:state.homeRevision});
methods.testRun=async (...args)=>({...await oldRun(...args),status:state.runState,official_evaluation_pending:state.officialPending});
export const api=`);
const archive = join(homedir(), ".cache/uv/archive-v0");
const driver = process.env.PLAYWRIGHT_MODULE || readdirSync(archive).map(name => join(archive, name, "playwright/driver/package/index.mjs")).find(existsSync);
const { chromium } = await import(pathToFileURL(driver).href);
const bundle = await build({ stdin: { contents: "import {createRoot} from 'react-dom/client';import App from './App.jsx';import './styles.css';import './console.css';import './lifecycle.css';import './r3.css';createRoot(document.getElementById('root')).render(<App/>);", loader: "jsx", resolveDir: source },
  plugins: [{ name: "offline-api", setup(b) { b.onLoad({ filter: /\/api\.js$/ }, () => ({ contents: mock, loader: "js" })); } }], bundle: true, write: false,
  outfile: join(output, "bundle.js"), platform: "browser", format: "iife", jsx: "automatic", define: { "process.env.NODE_ENV": '"production"' }, loader: { ".md": "text", ".woff2": "dataurl" }, logLevel: "silent" });
const browser = await chromium.launch({ executablePath: process.env.CHROMIUM_PATH || join(homedir(), ".cache/ms-playwright/chromium-1208/chrome-linux64/chrome"), headless: true, args: ["--no-sandbox"] });
try {
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
  const errors = [], network = [];
  page.on("pageerror", error => errors.push(error.message));
  await page.route("**/*", route => route.request().url() === "https://fixture.invalid/" ? route.fulfill({ contentType: "text/html", body: '<html lang="ko"><head><meta name="viewport" content="width=device-width,initial-scale=1"></head><body><div id="root"></div></body></html>' }) : (network.push(route.request().url()), route.abort()));
  await page.goto("https://fixture.invalid/");
  await page.clock.install();
  await page.addStyleTag({ content: bundle.outputFiles.find(file => file.path.endsWith(".css")).text });
  await page.addScriptTag({ content: bundle.outputFiles.find(file => file.path.endsWith(".js")).text });
  const reads = name => page.evaluate(name => fixture.reads.filter(row => row.name === name).length, name);
  const waitReads = (name, count) => page.waitForFunction(([name, count]) => fixture.reads.filter(row => row.name === name).length === count, [name, count]);
  await page.locator(".r3-evaluation-chart").waitFor();
  await waitReads("overview", 1);
  await page.clock.fastForward(61000);
  assert.equal(await reads("overview"), 1); assert.equal(await reads("serviceApiKeys"), 1);
  assert.ok(await reads("activity") >= 2); assert.ok(await reads("runtimeStatus") >= 2);
  await page.evaluate(() => { fixture.homeRevision = "after-official-evaluation"; });
  await page.clock.fastForward(61000); await waitReads("overview", 2);
  await page.evaluate(() => document.dispatchEvent(new Event("waf:home-invalidated")));
  await waitReads("overview", 3);
  await page.getByRole("button", { name: "새로고침", exact: true }).click(); await waitReads("overview", 4);
  await page.screenshot({ path: join(output, "home-desktop.png"), fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
  await page.screenshot({ path: join(output, "home-mobile.png"), fullPage: true });
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.evaluate(() => { location.hash = "#evaluate/tests/00000008-1111-4111-8111-111111111111"; });
  await page.locator(".test-run-header").waitFor();
  const initial = await reads("testRun");
  await page.clock.fastForward(60000); assert.equal(await reads("testRun"), initial);
  await page.evaluate(() => { fixture.runState = "processing"; });
  await page.getByRole("button", { name: "새로고침", exact: true }).click(); await waitReads("testRun", initial + 1);
  await page.clock.fastForward(5100); await waitReads("testRun", initial + 2);
  await page.evaluate(() => { fixture.runState = "completed"; fixture.officialPending = true; });
  await page.clock.fastForward(5100); await waitReads("testRun", initial + 3);
  await page.clock.fastForward(5100); await waitReads("testRun", initial + 4);
  await page.evaluate(() => { fixture.officialPending = false; fixture.runState = "failed"; });
  await page.clock.fastForward(5100); await waitReads("testRun", initial + 5);
  await page.clock.fastForward(60000); assert.equal(await reads("testRun"), initial + 5);
  assert.deepEqual(errors, []); assert.deepEqual(network, []);
  console.log("Home static/active reads, official evaluation invalidation, manual refresh, terminal Test stop, desktop/mobile: passed.");
} finally { await browser.close(); }
