// Real Test list interactions with synthetic APIs. No live service or model.
import assert from "node:assert/strict";
import { existsSync, readdirSync, mkdirSync, writeFileSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { build } from "esbuild";
import { parserWorkbenchFixture } from "./parser-workbench-fixture.mjs";

const output = fileURLToPath(new URL("../../test-results/test-run-metrics", import.meta.url));
mkdirSync(output, { recursive: true });
const bundle = await build({ stdin: {
  resolveDir: fileURLToPath(new URL("../src", import.meta.url)), loader: "jsx",
  contents: `import {StrictMode} from 'react';import {createRoot} from 'react-dom/client';import App from './App.jsx';import './styles.css';import './console.css';import './lifecycle.css';import './r3.css';import './workspacePolish.css';createRoot(document.getElementById('root')).render(<StrictMode><App/></StrictMode>);`,
}, plugins: [{ name: "offline-api", setup(b) { b.onLoad({ filter: /\/api\.js$/ }, () => ({ contents: parserWorkbenchFixture(), loader: "js" })); } }],
  bundle: true, write: false, outfile: join(output, "fixture.js"), platform: "browser", format: "iife", jsx: "automatic",
  logLevel: "silent", define: { "process.env.NODE_ENV": JSON.stringify(process.env.REACT_TEST_MODE || "production") },
  loader: { ".woff2": "dataurl", ".md": "text" },
});
const archive = join(homedir(), ".cache/uv/archive-v0");
const driver = process.env.PLAYWRIGHT_MODULE || readdirSync(archive).map(name => join(archive, name, "playwright/driver/package/index.mjs")).find(existsSync);
if (!driver) throw Error("An existing browser is required; no browser download is attempted.");
const { chromium } = await import(pathToFileURL(driver).href);
const browser = await chromium.launch({ executablePath: process.env.CHROMIUM_PATH || join(homedir(), ".cache/ms-playwright/chromium-1208/chrome-linux64/chrome"), headless: true, args: ["--no-sandbox"] });
const errors = [], traffic = [], screenshots = [];
let page;
try {
  page = await browser.newPage({ viewport: { width: 1440, height: 1000 }, reducedMotion: "reduce" });
  page.setDefaultTimeout(10000);
  page.on("pageerror", error => { errors.push(error.stack || error.message); console.error("Browser:", error.stack || error.message); });
  await page.route("**/*", route => route.request().url().split("#")[0] === "https://fixture.invalid/"
    ? route.fulfill({ contentType: "text/html", body: '<html><head><meta name="viewport" content="width=device-width,initial-scale=1"></head><body><div id="root"></div></body></html>' })
    : (traffic.push(route.request().url()), route.abort()));
  await page.goto("https://fixture.invalid/#evaluate/tests");
  await page.addStyleTag({ content: bundle.outputFiles.find(file => file.path.endsWith(".css")).text });
  await page.addScriptTag({ content: bundle.outputFiles.find(file => file.path.endsWith(".js")).text });
  for (const [theme, locale, width] of [["Light", "KR", 1440], ["SK", "KR", 390], ["Dark", "EN", 1440]]) {
    await page.setViewportSize({ width, height: 1000 });
    await page.getByRole("button", { name: /^(테마|Theme)$/ }).click();
    await page.getByRole("button", { name: theme, exact: true }).click();
    await page.getByRole("button", { name: locale, exact: true }).click();
    const table = page.getByRole("region", { name: locale === "KR" ? "테스트 목록" : "Tests", exact: true });
    const toggle = page.locator(".r3-metrics-toggle");
    await table.locator(".tabulator-row").first().waitFor();
    const count = await table.locator(".tabulator-row").count();
    for (let cycle = 0; cycle < 3; cycle++) {
      await toggle.click();
      assert.equal(await toggle.getAttribute("aria-expanded"), "true");
      await table.locator('.tabulator-cell[tabulator-field="accuracy"]').first().waitFor();
      assert.equal(await table.locator(".tabulator-row").count(), count);
      assert.equal(await table.locator('.tabulator-col[tabulator-field="false_negative_rate"]').count(), 1);
      if (cycle === 0) {
        const path = join(output, `${theme}-${locale}-${width}-expanded.png`);
        await page.screenshot({ path, fullPage: true }); screenshots.push(path);
        assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true, "expanded columns scroll within the table");
        const help = table.getByRole("button", { name: /accuracy/ }).first();
        await help.focus();
        assert.match(await help.getAttribute("aria-describedby"), /.+/, "metric explanations remain keyboard accessible");
      }
      await toggle.click();
      assert.equal(await toggle.getAttribute("aria-expanded"), "false");
      await table.locator('.tabulator-cell[tabulator-field="evaluation"]').first().waitFor();
      assert.equal(await table.locator(".tabulator-row").count(), count);
      assert.equal(await table.locator('.tabulator-col[tabulator-field="accuracy"]').count(), 0);
      assert.deepEqual(errors, []);
    }
  }
  assert.deepEqual(traffic, []);
  assert.deepEqual(await page.evaluate(() => fixture.unknown), []);
  assert.equal(await page.evaluate(() => fixture.writes.length), 0);
  console.log("Test metrics: expand/collapse × 9, retained rows, metric headers, keyboard help, Light/SK/Dark, KR/EN and mobile containment passed.");
} catch (error) {
  await page?.screenshot({ path: join(output, "failure.png"), fullPage: true });
  throw error;
} finally {
  writeFileSync(join(output, "audit.json"), JSON.stringify({ errors, traffic, screenshots }, null, 2));
  await browser.close();
}
