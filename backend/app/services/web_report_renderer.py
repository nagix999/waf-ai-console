"""Private child process: one document, no URLs/cookies, only PDF on stdout."""
import json
from pathlib import Path
import re
import sys

from .analysis_exports import MAX_DOWNLOAD_BYTES, MAX_PDF_PAGES
from .web_report_pdf import MAX_PDF_INPUT_BYTES


def render(document):
    from playwright.sync_api import sync_playwright
    assets = Path(__file__).resolve().parents[1] / "data/report-ui"
    with sync_playwright() as driver:
        browser = driver.chromium.launch(headless=True, args=["--disable-background-networking", "--disable-component-update"])
        try:
            context = browser.new_context(service_workers="block", accept_downloads=False, locale="ko-KR")
            context.route("**/*", lambda route: route.abort())
            page = context.new_page()
            page.set_default_timeout(20_000)
            page.set_content('<!doctype html><html lang="ko"><head><meta charset="utf-8"></head><body><main id="report-root"></main></body></html>')
            page.add_style_tag(content=(assets / "reportPdf.css").read_text())
            page.add_script_tag(content=(assets / "reportPdf.js").read_text())
            page.evaluate("document => window.renderWafReport(document)", document)
            page.wait_for_function("window.wafReportReady === true")
            page.evaluate("document.fonts.ready")
            pdf = page.pdf(format="A4", prefer_css_page_size=True, print_background=True, tagged=True, outline=True)
            # Chromium emits uncompressed page dictionaries; fail closed if its
            # format changes instead of returning an uncounted/truncated file.
            pages = len(re.findall(rb"/Type\s*/Page\b", pdf))
            if not pages or pages > MAX_PDF_PAGES or len(pdf) > MAX_DOWNLOAD_BYTES:
                raise ValueError("report_too_large")
            return pdf
        finally:
            browser.close()


if __name__ == "__main__":
    try:
        raw = sys.stdin.buffer.read(MAX_PDF_INPUT_BYTES + 1)
        if len(raw) > MAX_PDF_INPUT_BYTES:
            sys.exit(2)
        sys.stdout.buffer.write(render(json.loads(raw)))
    except Exception as error:
        # Never print browser/JS errors; they may contain analyst evidence.
        sys.exit(2 if "report_too_large" in str(error) else 1)
