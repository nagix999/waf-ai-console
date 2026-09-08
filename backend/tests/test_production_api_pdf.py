"""Synthetic/offline PDF contract, schema race, layout and privilege checks."""
import copy
import re
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.api import production_api as api_module
from app.api_key_schemas import ServiceApiKeyCreate
from app.database import Base
from app.services import production_api_pdf as pdf
from app.services.input_schemas import InputSchemaError
from app.services.service_api_keys import issue_key
from test_input_schema_integration import activate, login, new_version

PATH = "/api/v1/production-api.pdf"


def capture_renderer(monkeypatch):
    captured = []
    def render(markdown, metadata):
        captured.append((markdown, copy.deepcopy(metadata)))
        return b"%PDF-1.4\nsynthetic-only\n%%EOF"
    monkeypatch.setattr(api_module, "render_production_api_pdf", render)
    return captured


def stored_rows(client):
    with client.app.state.session_factory() as db:
        return {table.name: sorted((tuple(row) for row in db.execute(select(table))), key=repr)
                for table in Base.metadata.sorted_tables}


def test_pdf_auth_matches_document_ingest_scope(client, service_headers, monkeypatch):
    captured = capture_renderer(monkeypatch)
    assert client.get(PATH).status_code == 401
    assert not captured
    with client.app.state.session_factory() as db:
        _, review_key = issue_key(db, ServiceApiKeyCreate(name="Synthetic review only", source_system="synthetic-review", scopes=["review"]), "fixture")
        db.commit()
    assert client.get(PATH, headers={"X-API-Key": review_key}).status_code == 403
    assert not captured
    response = client.get(PATH, headers=service_headers)
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["content-disposition"] == 'attachment; filename="Production_API_v0.2.0_schema-v1.pdf"'
    assert len(captured) == 1


def test_pdf_uses_exact_live_markdown_and_schema_without_changing_history(client, event_payload, monkeypatch):
    login(client)
    version = new_version(client, {"name": "risk_rank", "description": "합성 순번 <조건> & 허용값",
        "type": "integer", "required": True, "minimum": 1, "maximum": 9, "enum": [1, 3, 9]})
    activate(client, version, {**event_payload, "risk_rank": 3})
    captured = capture_renderer(monkeypatch)
    document = client.get("/api/v1/production-api").json()
    before = stored_rows(client)
    response = client.get(PATH, params={"expected_schema_hash": version["content_hash"], "expected_schema_version_id": version["id"]})
    assert response.status_code == 200
    assert captured == [(document["markdown"], document["input_schema"])]
    assert response.headers["x-input-schema-hash"] == version["content_hash"]
    assert response.headers["x-input-schema-version-id"] == version["id"]
    assert f'schema-v{version["version_number"]}.pdf' in response.headers["content-disposition"]
    assert stored_rows(client) == before
    blocks = pdf.document_blocks(captured[0][0])
    titles = [block.text for block in blocks if block.kind == "heading" and block.level == 2]
    assert len(titles) == 12
    assert titles == re.findall(r"^## (.+)$", document["markdown"], flags=re.M)
    assert blocks[1].kind == "text"  # Intro is retained, not just the 12 sections.
    assert any("risk_rank" in cell and "`risk_rank`" == cell for block in blocks for row in block.rows for cell in row)
    codes = "\n".join(block.text for block in blocks if block.kind == "code")
    assert '"minimum": 1' in codes and '"maximum": 9' in codes and '"enum": [' in codes
    assert '"event_id"' in codes and '"risk_rank"' in codes and "X-API-Key:" in codes


@pytest.mark.parametrize("changed", ["hash", "id", "both"])
def test_stale_schema_returns_409_before_renderer(client, monkeypatch, changed):
    login(client)
    meta = client.get("/api/v1/production-api").json()["input_schema"]
    captured = capture_renderer(monkeypatch)
    params = {"expected_schema_hash": meta["content_hash"], "expected_schema_version_id": meta["version_id"]}
    if changed in {"hash", "both"}:
        params["expected_schema_hash"] = "0" * 64
    if changed in {"id", "both"}:
        params["expected_schema_version_id"] = str(uuid4())
    before = stored_rows(client)
    response = client.get(PATH, params=params)
    assert response.status_code == 409 and response.json() == {"detail": "input_schema_document_changed"}
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert not captured and stored_rows(client) == before


def test_same_definition_different_version_is_not_silently_downloaded(client, event_payload, monkeypatch):
    login(client)
    old = client.get("/api/v1/production-api").json()["input_schema"]
    version = new_version(client)
    assert version["content_hash"] == old["content_hash"] and version["id"] != old["version_id"]
    activate(client, version, event_payload)
    captured = capture_renderer(monkeypatch)
    response = client.get(PATH, params={"expected_schema_hash": old["content_hash"], "expected_schema_version_id": old["version_id"]})
    assert response.status_code == 409 and not captured
    assert client.get(PATH).status_code == 200
    assert captured[-1][1]["version_id"] == version["id"]
    assert client.get(PATH, params={"expected_schema_hash": version["content_hash"].upper()}).status_code == 200
    assert client.get(PATH, params={"expected_schema_version_id": version["id"]}).status_code == 200


@pytest.mark.parametrize("query", ["expected_schema_hash=PRIVATE-SYNTHETIC-QUERY", "expected_schema_hash=" + "z" * 64,
    "expected_schema_version_id=PRIVATE-SYNTHETIC-QUERY", "unexpected=PRIVATE-SYNTHETIC-QUERY",
    "expected_schema_hash=" + "1" * 64 + "&expected_schema_hash=" + "1" * 64])
def test_bad_or_duplicate_query_is_rejected_without_render_or_echo(client, monkeypatch, query):
    login(client)
    captured = capture_renderer(monkeypatch)
    response = client.get(PATH + "?" + query)
    assert response.status_code == 422 and not captured
    assert "PRIVATE-SYNTHETIC-QUERY" not in response.text


@pytest.mark.parametrize("where", ["contract", "markdown", "renderer"])
def test_upstream_layout_storage_errors_are_generic(client, monkeypatch, caplog, where):
    login(client)
    def fail(*args, **kwargs):
        raise ValueError("SYNTHETIC-PRIVATE-DOCUMENT-FRAGMENT")
    attribute = {"contract": "active_contract", "markdown": "markdown_contract", "renderer": "render_production_api_pdf"}[where]
    monkeypatch.setattr(api_module, attribute, fail)
    response = client.get(PATH)
    assert response.status_code == 503 and response.json() == {"detail": "production_api_pdf_unavailable"}
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert "SYNTHETIC-PRIVATE" not in response.text + caplog.text


def test_invalid_stored_schema_does_not_render_old_or_default_contract(client, monkeypatch):
    login(client)
    captured = capture_renderer(monkeypatch)
    def fail(*args, **kwargs):
        raise InputSchemaError("input_schema_definition_invalid", 503)
    monkeypatch.setattr(api_module, "active_contract", fail)
    response = client.get(PATH)
    assert response.status_code == 503
    assert response.json() == {"detail": "input_schema_definition_invalid"}
    assert not captured


def test_openapi_documents_binary_pdf_and_both_snapshot_guards(client):
    operation = client.get("/openapi.json").json()["paths"][PATH]["get"]
    assert "application/pdf" in operation["responses"]["200"]["content"]
    assert {"ServiceAPIKey": []} in operation["security"]
    assert {item["name"] for item in operation["parameters"]} == {"expected_schema_hash", "expected_schema_version_id"}
    assert {"409", "413", "503"}.issubset(operation["responses"])


def test_markdown_parser_retains_sections_code_whitespace_and_literal_table_pipes():
    source = "# 정의서\n\n소개\n\n## 첫 항목\n\n| 필드 | 설명 |\n| --- | --- |\n| `a|b` | 원문 &#124; 설명 |\n\n```json\n{\n  \"nested\": [1, 2]\n}\n```\n\n## 마지막 항목\n끝"
    blocks = pdf.document_blocks(source)
    assert [block.text for block in blocks if block.kind == "heading"] == ["정의서", "첫 항목", "마지막 항목"]
    table = next(block for block in blocks if block.kind == "table")
    assert table.rows == (("필드", "설명"), ("`a|b`", "원문 &#124; 설명"))
    assert next(block.text for block in blocks if block.kind == "code") == '{\n  "nested": [1, 2]\n}'
    assert blocks[-1].text == "끝"


def test_real_pdf_covers_current_full_document_and_korean_fonts(client):
    pytest.importorskip("reportlab")
    login(client)
    response = client.get(PATH)
    assert response.status_code == 200
    body = response.content
    assert body.startswith(b"%PDF-") and body.rstrip().endswith(b"%%EOF")
    assert b"/FontFile2" in body and b"/ToUnicode" in body
    assert len(re.findall(rb"/Type\s*/Page\b", body)) > 1
    assert not any(marker in body for marker in (b"/JavaScript", b"/OpenAction", b"/Launch", b"/EmbeddedFile", b"/URI "))


def test_schema_markup_is_literal_never_image_link_network_or_execution(monkeypatch):
    pytest.importorskip("reportlab")
    import reportlab.pdfgen.canvas
    import reportlab.platypus.paraparser
    import urllib.request
    from reportlab.platypus import Paragraph
    seen = []
    original = Paragraph.__init__
    def capture(self, text, *args, **kwargs):
        seen.append(text)
        original(self, text, *args, **kwargs)
    def forbidden(*args, **kwargs):
        raise AssertionError("External resources must not be evaluated")
    monkeypatch.setattr(Paragraph, "__init__", capture)
    monkeypatch.setattr(reportlab.platypus.paraparser.ParaParser, "start_img", forbidden)
    monkeypatch.setattr(reportlab.pdfgen.canvas.Canvas, "linkURL", forbidden)
    monkeypatch.setattr(urllib.request, "urlopen", forbidden)
    content = pdf.render_production_api_pdf('# 문서\n\n## 제한\n\n| 이름 | 설명 |\n| --- | --- |\n| x | &lt;img src="https://example.invalid/private"/&gt; &amp; 한글 |\n\n<a href="file:///private">실행 안 함</a>\n\n```json\n{"x": "<img src=\"https://example.invalid/\"/>"}\n```\n\n😀\x00\u202e', {"version_number": 1})
    assert content.startswith(b"%PDF-")
    rendered = "\n".join(value for value in seen if isinstance(value, str))
    assert "&lt;img" in rendered and "&amp; 한글" in rendered
    assert "[U+1F600]" in rendered and "[U+0000]" in rendered and "[U+202E]" in rendered
    assert "<img" not in rendered and "<a href" not in rendered


def test_long_korean_cells_and_code_lines_paginate_without_silent_truncation(monkeypatch):
    pytest.importorskip("reportlab")
    from reportlab.platypus import Paragraph
    original = Paragraph.__init__
    seen = []
    def capture(self, text, *args, **kwargs):
        if isinstance(text, str):
            seen.append(text)
        original(self, text, *args, **kwargs)
    monkeypatch.setattr(Paragraph, "__init__", capture)
    long_cell = "한글 설명의 긴 내용 " * 160 + "CELL-END"
    code = '  "long_value": "' + "x" * 6000 + 'CODE-END"'
    content = pdf.render_production_api_pdf("# 문서\n\n| 필드 | 설명 |\n| --- | --- |\n| field | " + long_cell + " |\n\n```json\n" + code + "\n```", {"version_number": 1})
    assert content.startswith(b"%PDF-")
    assert len(re.findall(rb"/Type\s*/Page\b", content)) >= 2
    assert any("CELL-END" in value for value in seen) and any("CODE-END" in value for value in seen)


@pytest.mark.parametrize("name,value,source", [
    ("MAX_DOCUMENT_BYTES", 15, "# 문서\n한글" * 20),
    ("MAX_DOCUMENT_LINES", 3, "# 문서\n\nx\n\ny"),
    ("MAX_BLOCKS", 1, "# 문서\n\nx"),
    ("MAX_TABLE_COLUMNS", 1, "# 문서\n\n| a | b |\n| --- | --- |\n| 1 | 2 |"),
    ("MAX_PDF_PAGES", 1, "# 문서\n\n" + "긴 문장입니다. " * 5000),
    ("MAX_DOWNLOAD_BYTES", 100, "# 문서\n\n짧은 내용"),
], ids=["bytes", "lines", "blocks", "columns", "pages", "download"])
def test_all_document_limits_fail_closed(monkeypatch, name, value, source):
    pytest.importorskip("reportlab")
    monkeypatch.setattr(pdf, name, value)
    with pytest.raises(pdf.ProductionAPIPDFError) as caught:
        pdf.render_production_api_pdf(source, {"version_number": 1})
    assert (caught.value.code, caught.value.status_code) == ("production_api_pdf_too_large", 413)


def test_export_error_returns_status_without_partial_pdf(client, monkeypatch):
    login(client)
    def too_large(*args, **kwargs):
        raise pdf.ProductionAPIPDFError("production_api_pdf_too_large", 413)
    monkeypatch.setattr(api_module, "render_production_api_pdf", too_large)
    response = client.get(PATH)
    assert response.status_code == 413 and response.json() == {"detail": "production_api_pdf_too_large"}
    assert response.headers["content-type"] == "application/json"
    assert "content-disposition" not in response.headers


def test_layout_failures_never_include_document_or_upstream_exception(monkeypatch):
    pytest.importorskip("reportlab")
    from reportlab.platypus import SimpleDocTemplate
    def fail(*args, **kwargs):
        raise ValueError("SYNTHETIC-PRIVATE-LAYOUT-CONTENT")
    monkeypatch.setattr(SimpleDocTemplate, "build", fail)
    with pytest.raises(pdf.ProductionAPIPDFError) as caught:
        pdf.render_production_api_pdf("# 안전한 제목\n\nSYNTHETIC-PRIVATE-LAYOUT-CONTENT", {"version_number": 1})
    assert str(caught.value) == "production_api_pdf_unavailable"
    assert caught.value.__suppress_context__ is True
