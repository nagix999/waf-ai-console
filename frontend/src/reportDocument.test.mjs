import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import { buildAnalysisReport, decodeReportText } from "./analysisReport.js";
import { parseApiDocument } from "./apiDocument.js";
import { buildReportDocument, normalizeReportBlock } from "./reportDocument.js";

const cases = JSON.parse(readFileSync(new URL("../../backend/tests/fixtures/analyst_assessment_cases.json", import.meta.url)));

test("offline report shares every web section, field and option without mutating results", () => {
  for (const item of cases) for (const includeAppendix of [false, true]) {
    const detail = { id: "fictional", status: "completed", result: item.result, payload: "RAW-EXCLUDED", extra_fields: { value: "EXTRA-EXCLUDED" } };
    const options = { detail, includeAppendix };
    const before = JSON.stringify(options);
    const actual = buildReportDocument(options);
    const web = parseApiDocument(buildAnalysisReport(detail, { includeAppendix }));
    assert.equal(actual.title, decodeReportText(web.title));
    assert.deepEqual(actual.intro, web.intro.map(normalizeReportBlock));
    assert.deepEqual(actual.sections, web.sections.map(section => ({ ...section, title: decodeReportText(section.title), blocks: section.blocks.map(normalizeReportBlock) })));
    assert.equal(JSON.stringify(options), before);
    assert.doesNotMatch(JSON.stringify(actual), /RAW-EXCLUDED|EXTRA-EXCLUDED/);
  }
});

test("only the fence separator LF is removed; CR, spaces and HTML entities in excerpts remain literal", () => {
  const text = " <a href='https://example.invalid'>&lt;x&gt;</a>  \r\n\n";
  assert.equal(normalizeReportBlock({ type: "code", text: text + "\n" }).text, text);
  assert.equal(normalizeReportBlock({ type: "paragraph", text: "&lt;x&gt;" }).text, "<x>");
});

test("oversized shared Markdown is rejected, not silently truncated", () => {
  assert.throws(() => buildReportDocument({ detail: { status: "completed", result: { summary_ko: "가".repeat(200_000) } } }), /report_too_large/);
});
