"""Private child: shared text formatter -> offline ReportLab -> PDF stdout."""
import json
import os
from pathlib import Path
import subprocess
import sys

from .report_pdf import MAX_PDF_INPUT_BYTES
from .report_pdf_layout import MAX_INPUT_BYTES, ReportLayoutError, render_pdf


def render(document):
    asset = Path(__file__).resolve().parents[1] / "data/report-document.cjs"
    raw = json.dumps(document, ensure_ascii=False, allow_nan=False).encode()
    if len(raw) > MAX_PDF_INPUT_BYTES:
        raise ReportLayoutError("report_too_large")
    # Fixed local bundle, no NODE_OPTIONS, provider secrets, browser or URLs.
    # The API's 45 s process-group deadline bounds both formatter and layout.
    result = subprocess.run(
        ["node", "--max-old-space-size=96", "--disallow-code-generation-from-strings", str(asset)],
        input=raw, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        env={key: value for key, value in os.environ.items() if key in {"PATH", "LANG", "LC_ALL"}},
        check=False,
    )
    if result.returncode == 2 or len(result.stdout) > MAX_INPUT_BYTES:
        raise ReportLayoutError("report_too_large")
    if result.returncode:
        raise ReportLayoutError("report_formatter_failed")
    return render_pdf(json.loads(result.stdout), theme=document.get("theme", "light"))


if __name__ == "__main__":
    try:
        raw = sys.stdin.buffer.read(MAX_PDF_INPUT_BYTES + 1)
        if len(raw) > MAX_PDF_INPUT_BYTES:
            sys.exit(2)
        sys.stdout.buffer.write(render(json.loads(raw)))
    except Exception as error:
        limits = {"report_too_large", "report_input_too_large", "report_too_many_blocks",
                  "report_too_many_pages", "report_pdf_too_large"}
        # Only stable error codes leave this process, never report/layout text.
        sys.exit(2 if isinstance(error, ReportLayoutError) and str(error) in limits else 1)
