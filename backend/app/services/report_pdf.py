"""Isolated, bounded PDF rendering from the shared report text template."""
import json
import os
import signal
import subprocess
import sys
from threading import BoundedSemaphore

from .analysis_exports import MAX_DOWNLOAD_BYTES, ReportExportError, build_report

_SLOTS = BoundedSemaphore(1)
PDF_TIMEOUT_SECONDS = 45
MAX_PDF_INPUT_BYTES = 2 * 1024 * 1024


def pdf_document(detail, *, decoding=None, include_appendix=False, theme="light"):
    # Keep existing final/non-stub eligibility and bounds. The child also
    # checks shared document/body limits including optional sections.
    build_report(detail)
    # Raw, extension values and intermediate role output must never be handed
    # to the rendering process, including when an older DB contains extras.
    fields = "id event_id source_system status analysis_purpose ingest_channel company_name src_ip dest_ip src_port dest_port waf_vendor waf_action event_name signature created_at started_at completed_at total_elapsed_ms queue_wait_ms processing_duration_ms model_profile prompt_version verdict summary_ko severity input_truncated evaluation review_state error_code".split()
    clean = {key: detail[key] for key in fields if key in detail}
    result = detail.get("result") or {}
    allowed = "schema_version verdict summary_ko confidence_score threat_analysis signature_assessment evidence analyst_guidance analyst_checks recommended_checks conflicting_evidence tuning_recommendation input_truncated analyst_assessment evidence_presentation follow_up_presentation".split()
    clean["result"] = {key: result[key] for key in allowed if key in result}
    def pick(value, keys):
        return {key: value[key] for key in keys.split() if key in value} if isinstance(value, dict) else {}
    clean["result"]["agent"] = pick(result.get("agent"), "framework execution agent_mode llm_called llm_provider model_name")
    clean["result"]["policy"] = pick(result.get("policy"), "prompt_version")
    diagnostic = result.get("diagnostics") if isinstance(result.get("diagnostics"), dict) else {}
    clean["result"]["diagnostics"] = {
        **pick(diagnostic, "inconclusive_reasons"),
        "roles": {role: pick((diagnostic.get("roles") or {}).get(role), "correction_requires_review")
                  for role in ("primary", "verifier") if isinstance(diagnostic.get("roles"), dict)},
        "request_integrity": pick(diagnostic.get("request_integrity"), "downgraded_to_inconclusive affected_issue_codes"),
    }
    if isinstance(result.get("verifier"), dict):
        clean["result"]["verifier"] = {key: result["verifier"][key] for key in ("executed", "agreement", "failure_id", "reasons", "error") if key in result["verifier"]}
    return {"detail": clean, "decoding": decoding, "includeAppendix": include_appendix, "theme": theme}


def render_report_pdf(document):
    try:
        raw = json.dumps(document, ensure_ascii=False, allow_nan=False).encode()
    except (ValueError, UnicodeError, RecursionError):
        raise ReportExportError() from None
    if len(raw) > MAX_PDF_INPUT_BYTES:
        raise ReportExportError("report_too_large", 413)
    if not _SLOTS.acquire(blocking=False):
        raise ReportExportError("report_export_busy", 503)
    try:
        # Do not give the renderer DB/key/provider credentials from the API env.
        environment = {key: value for key, value in os.environ.items()
            if key in {"PATH", "LANG", "LC_ALL", "PYTHONPATH"}}
        environment["PYTHONDONTWRITEBYTECODE"] = "1"
        with subprocess.Popen([sys.executable, "-m", "app.services.report_renderer"],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                env=environment, start_new_session=True) as process:
            try:
                body, _ = process.communicate(raw, timeout=PDF_TIMEOUT_SECONDS)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.communicate()
                raise ReportExportError("report_export_timeout", 503) from None
            if process.returncode == 2:
                raise ReportExportError("report_too_large", 413)
            if process.returncode != 0 or not body.startswith(b"%PDF-"):
                raise ReportExportError()
            if len(body) > MAX_DOWNLOAD_BYTES:
                raise ReportExportError("report_too_large", 413)
            return body
    except ReportExportError:
        raise
    except Exception:
        raise ReportExportError() from None
    finally:
        _SLOTS.release()
