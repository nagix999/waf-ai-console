"""Offline renderer and download contract checks; synthetic data only."""
import copy
import json
import os
from pathlib import Path
import subprocess

import pytest
from sqlalchemy import select

from app.models import AccessAudit
from app.services.analysis_exports import ReportExportError
from app.services import web_report_pdf as pdf
from test_analysis_exports import detail_fixture, stored_analysis


def document():
    return pdf.pdf_document(json.loads(json.dumps(detail_fixture(), default=str)))


def test_only_report_fields_leave_api_process():
    detail = detail_fixture()
    for key in ("agent", "policy", "diagnostics"):
        detail["result"].setdefault(key, {})["payload"] = "HIDDEN-CHILD-INPUT"
    before = copy.deepcopy(detail)
    result = pdf.pdf_document(detail, include_appendix=True, theme="dark")
    value = json.dumps(result, default=str)
    for marker in ("RAW-PAYLOAD-MUST", "EXTRA-MUST", "PRIMARY-MUST", "VERIFIER-MUST", "UNKNOWN-MUST", "HIDDEN-CHILD-INPUT"):
        assert marker not in value
    assert result["theme"] == "dark" and result["includeAppendix"] is True
    assert detail == before


def test_busy_limit_releases_and_size_is_bounded():
    pdf._SLOTS.acquire()
    try:
        with pytest.raises(ReportExportError, match="report_export_busy"):
            pdf.render_web_pdf({})
    finally:
        pdf._SLOTS.release()
    with pytest.raises(ReportExportError, match="report_too_large"):
        pdf.render_web_pdf({"oversize": "x" * pdf.MAX_PDF_INPUT_BYTES})


@pytest.mark.parametrize("exitcode,body,error", [(1, b"secret exception", "report_export_unavailable"), (2, b"", "report_too_large"), (0, b"not a pdf", "report_export_unavailable"), (0, b"%PDF-" + b"x" * pdf.MAX_DOWNLOAD_BYTES, "report_too_large")])
def test_safe_child_failure_and_slot_release(monkeypatch, exitcode, body, error):
    class Child:
        returncode = exitcode
        def __init__(self, *args, **kwargs):
            assert kwargs["start_new_session"] is True
            assert kwargs["stderr"] == subprocess.DEVNULL
            assert "WAF_PRIVATE_FIXTURE" not in kwargs["env"]
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def communicate(self, *args, **kwargs): return body, None
    monkeypatch.setenv("WAF_PRIVATE_FIXTURE", "secret")
    monkeypatch.setattr(pdf.subprocess, "Popen", Child)
    with pytest.raises(ReportExportError, match=f"^{error}$"):
        pdf.render_web_pdf({})
    assert pdf._SLOTS.acquire(blocking=False)
    pdf._SLOTS.release()


def test_timeout_kills_entire_renderer_group_without_report_text(monkeypatch):
    killed = []
    class Child:
        pid = 999999
        def __init__(self, *args, **kwargs): self.calls = 0
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def communicate(self, *args, **kwargs):
            self.calls += 1
            if self.calls == 1: raise subprocess.TimeoutExpired("private fixture", 45)
            return b"", None
    monkeypatch.setattr(pdf.subprocess, "Popen", Child)
    monkeypatch.setattr(pdf.os, "killpg", lambda *args: killed.append(args))
    with pytest.raises(ReportExportError, match="^report_export_timeout$"):
        pdf.render_web_pdf({})
    assert killed == [(999999, pdf.signal.SIGKILL)]
    assert pdf._SLOTS.acquire(blocking=False)
    pdf._SLOTS.release()


def test_pdf_options_match_web_and_decoding_access_is_explicitly_audited(client, event_payload, monkeypatch):
    identifier = stored_analysis(client, event_payload)
    captured = []
    monkeypatch.setattr("app.api.analysis_exports.render_web_pdf", lambda value: captured.append(value) or b"%PDF-fixture")
    path = f"/api/v1/analyses/{identifier}/report.pdf"
    assert client.get(path + "?include_appendix=true&include_decoding=true&theme=dark").status_code == 200
    assert captured[-1]["includeAppendix"] is True and captured[-1]["theme"] == "dark"
    assert isinstance(captured[-1]["decoding"], dict)
    with client.app.state.session_factory() as db:
        assert db.scalar(select(AccessAudit.id).where(AccessAudit.action == "export_analysis_pdf_decoding"))
    for query in ("include_raw=true", "theme=red", "include_appendix=invalid"):
        assert client.get(path + "?" + query).status_code == 422
    assert len(captured) == 1


def test_real_chromium_pdf_has_korean_font_no_active_content_and_all_pages(tmp_path):
    # Local development can run pure contract tests without downloading a
    # browser. CI/release image checks set this flag and require real rendering.
    if os.environ.get("WAF_TEST_REPORT_BROWSER") != "1":
        pytest.skip("Set WAF_TEST_REPORT_BROWSER=1 in the Chromium-enabled image")
    value = document()
    value["detail"]["result"]["summary_ko"] = "보안 경계 우회 시도를 확인했습니다. 한글 보고서 검증."
    value["detail"]["result"]["evidence"][0]["excerpt"] = '<img src="https://example.invalid/no-network" onerror="alert(1)"> <a href="file:///private">한글</a>'
    for theme in ("light", "dark"):
        value["theme"] = theme
        body = pdf.render_web_pdf(value)
        assert body.startswith(b"%PDF-") and body.rstrip().endswith(b"%%EOF")
        assert b"/ToUnicode" in body and b"/FontFile2" in body
        assert not any(marker in body for marker in (b"/URI", b"/JavaScript", b"/Launch"))
        (tmp_path / f"report-{theme}.pdf").write_bytes(body)


def test_real_long_report_keeps_evidence_groups_and_unbroken_strings(tmp_path):
    if os.environ.get("WAF_TEST_REPORT_BROWSER") != "1":
        pytest.skip("Set WAF_TEST_REPORT_BROWSER=1 in the Chromium-enabled image")
    value = document()
    cases = json.loads((Path(__file__).parent / "fixtures/analyst_assessment_cases.json").read_text())
    value["detail"]["result"].update(cases[0]["result"])
    value["detail"]["result"]["analyst_assessment"]["evidence"][0]["excerpt"] = "LONG-START " + "x" * 2400 + " LONG-END"
    value["detail"]["result"]["summary_ko"] = "요청 구조와 로그 발췌를 검토한 결과입니다. " * 200 + "끝까지 보존한 문장입니다."
    value["includeAppendix"] = True
    for theme in ("light", "dark"):
        value["theme"] = theme
        body = pdf.render_web_pdf(value)
        assert len(__import__("re").findall(rb"/Type\s*/Page\b", body)) >= 4
        (tmp_path / f"long-{theme}.pdf").write_bytes(body)
