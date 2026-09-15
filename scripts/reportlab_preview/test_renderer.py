"""Offline, synthetic checks for the isolated prototype; no app/database import."""
import copy
import json
from pathlib import Path
import socket
import subprocess
import urllib.request

import pytest

import renderer


def document(block=None):
    return {"title": "가상 보고서", "intro": [], "sections": [{"title": "판정 근거", "blocks": [
        block or {"type": "paragraph", "text": "실제 요청이 아닌 디자인 점검 자료입니다."}
    ]}]}


@pytest.fixture(autouse=True)
def prohibit_external_io(monkeypatch):
    def blocked(*args, **kwargs):
        raise AssertionError("network_or_child_process_not_allowed")
    monkeypatch.setattr(socket, "create_connection", blocked)
    monkeypatch.setattr(socket.socket, "connect", blocked)
    monkeypatch.setattr(urllib.request, "urlopen", blocked)
    monkeypatch.setattr(subprocess, "Popen", blocked)


def test_renders_without_browser_or_network_and_preserves_input():
    source = document()
    before = copy.deepcopy(source)
    content = renderer.render_pdf(source)
    assert content.startswith(b"%PDF-")
    assert b"/FontFile2" in content and b"/ToUnicode" in content
    assert b"/URI" not in content and b"/JavaScript" not in content
    assert source == before


def test_untrusted_markup_is_literal_not_a_resource_or_link():
    content = renderer.render_pdf(document({"type": "code", "text":
        '<img src="http://127.0.0.1/private"/><a href="file:///etc/passwd">text</a>\n'
        '<script>previewOnly()</script>  &lt;literal&gt;\tEND\x00😀'}))
    assert content.startswith(b"%PDF-")
    assert b"/URI" not in content and b"/Subtype /Image" not in content
    assert "&lt;img" in renderer.literal('<img src="x"/>')
    assert renderer.literal("&lt;x&gt;") == "&amp;lt;x&amp;gt;"
    assert "[U+0000]" in renderer.literal("\x00")


def test_code_longer_than_one_page_splits():
    content = renderer.render_pdf(document({"type": "code", "text":
        "LONG-START\n" + ("Ab09" * 650) + "\n" + "한글 줄바꿈 검증 문장입니다.\n" * 100 + "LONG-END"}))
    assert content.count(b"/Type /Page\n") >= 2


def test_single_table_cell_longer_than_page_splits():
    content = renderer.render_pdf(document({"type": "table", "headers": ["항목", "내용"],
        "rows": [["분석 내용", "긴 분석 문장입니다. " * 1800]]}))
    assert content.count(b"/Type /Page\n") >= 2


@pytest.mark.parametrize("value", [None, [], {}, {"title": "x", "intro": "bad", "sections": []}])
def test_invalid_document_has_safe_error(value):
    with pytest.raises(renderer.PreviewError, match="^preview_invalid_document$"):
        renderer.render_pdf(value)


@pytest.mark.parametrize("block", [
    {"type": "image", "url": "http://127.0.0.1/private"},
    {"type": "paragraph", "text": {"private": "not-text"}},
    {"type": "table", "headers": ["one", "two"], "rows": [["only-one"]]},
])
def test_rejects_unknown_or_malformed_blocks(block):
    with pytest.raises(renderer.PreviewError, match="^preview_"):
        renderer.render_pdf(document(block))


def test_size_limits(monkeypatch):
    with pytest.raises(renderer.PreviewError, match="^preview_input_too_large$"):
        renderer.render_pdf(document({"type": "code", "text": "x" * renderer.MAX_INPUT_BYTES}))
    monkeypatch.setattr(renderer, "MAX_BLOCKS", 2)
    with pytest.raises(renderer.PreviewError, match="^preview_too_many_blocks$"):
        renderer.render_pdf(document({"type": "list", "items": ["a", "b", "c"]}))


def test_page_limit(monkeypatch):
    monkeypatch.setattr(renderer, "MAX_PAGES", 1)
    with pytest.raises(renderer.PreviewError, match="^preview_too_many_pages$"):
        renderer.render_pdf(document({"type": "code", "text": "검증 문장\n" * 140}))


def test_output_limit(monkeypatch):
    monkeypatch.setattr(renderer, "MAX_PDF_BYTES", 100)
    with pytest.raises(renderer.PreviewError, match="^preview_pdf_too_large$"):
        renderer.render_pdf(document())


def test_all_prepared_samples_if_available():
    # Optional fixture directory is explicitly supplied, never scans live data.
    directory = Path("/preview")
    if not (directory / "01-short.json").is_file():
        pytest.skip("prepared fixtures not mounted")
    for name in ("01-short", "02-long", "03-evidence"):
        source = json.loads((directory / f"{name}.json").read_text())
        assert renderer.render_pdf(source).startswith(b"%PDF-")


def test_grouping_preserves_blocks_and_explicit_support_only():
    blocks = [
        {"type": "paragraph", "text": "그룹 앞 안내"},
        {"type": "heading", "level": 3, "text": "정탐 근거"},
        {"type": "heading", "level": 4, "text": "근거 1"},
        {"type": "code", "text": "<b>오탐 근거</b>"},
        {"type": "paragraph", "text": "정탐 근거"},
        {"type": "heading", "level": 3, "text": "오탐 근거"},
        {"type": "paragraph", "text": "기록된 근거가 없습니다."},
        {"type": "heading", "level": 3, "text": "다른 절"},
    ]
    before = copy.deepcopy(blocks)
    grouped = list(renderer.evidence_groups(blocks))
    assert grouped == [(None, blocks[:1]), ("정탐 근거", blocks[2:5]),
                       ("오탐 근거", blocks[6:7]), (None, blocks[7:])]
    assert blocks == before


def test_card_repeats_heading_and_color_when_split_without_changing_original():
    renderer.register_fonts()
    sheet = renderer.styles()
    rows = [[renderer.paragraph(f"근거 {index} - 가상 문장", sheet["body"])] for index in range(15)]
    card = renderer.evidence_card("정탐 근거", rows, sheet)
    parts = card.split(renderer.WIDTH, 180)
    def cell_text(cell):
        return "".join(map(cell_text, cell)) if isinstance(cell, (tuple, list)) else cell.getPlainText()
    assert len(parts) == 3
    assert isinstance(parts[1], renderer.PageBreak)
    assert cell_text(parts[0]._cellvalues[0][0]) == "정탐 근거"
    assert cell_text(parts[2]._cellvalues[0][0]) == "정탐 근거 · 계속"
    assert cell_text(card._cellvalues[0][0]) == "정탐 근거"
    for part in (parts[0], parts[2]):
        assert part.repeatRows == 1
        assert part._bkgrndcmds and part._linecmds
    more = parts[2].split(renderer.WIDTH, 180)
    assert len(more) == 3
    assert cell_text(more[2]._cellvalues[0][0]) == "정탐 근거 · 계속"


def test_card_heading_cannot_be_orphaned():
    renderer.register_fonts()
    sheet = renderer.styles()
    card = renderer.evidence_card("참고 내용", [[renderer.paragraph("긴 줄\n" * 90, sheet["body"])]], sheet)
    assert card.split(renderer.WIDTH, 30) == []


@pytest.mark.parametrize("title", ["정탐 근거", "오탐 근거", "참고 내용"])
def test_only_group_heading_has_support_color_and_body_stays_white(title):
    renderer.register_fonts()
    sheet = renderer.styles()
    card = renderer.evidence_card(title, [[renderer.paragraph("근거 설명", sheet["body"])]], sheet)
    assert card._bkgrndcmds == [
        ("BACKGROUND", (0, 0), (-1, -1), renderer.colors.white),
        ("BACKGROUND", (0, 0), (-1, 0), renderer.colors.HexColor(renderer.GROUPS[title][1])),
    ]
    assert sheet["body"].backColor is None
    assert sheet["code"].backColor == renderer.PAPER


def test_normal_evidence_item_moves_to_next_page_instead_of_fragmenting():
    renderer.register_fonts()
    sheet = renderer.styles()
    card = renderer.evidence_card("정탐 근거", [[renderer.paragraph("근거 설명\n" * 22, sheet["body"])]], sheet)
    assert card.split(renderer.WIDTH, 190) == []


def test_grouped_long_excerpt_splits_and_preserves_input():
    source = document()
    source["sections"][0]["blocks"] = [
        {"type": "heading", "level": 3, "text": "정탐 근거"},
        {"type": "heading", "level": 4, "text": "근거 1"},
        {"type": "code", "text": "LONG-START\n" + "긴 원문을 보존합니다.\n" * 130 + "LONG-END"},
        {"type": "paragraph", "text": "다음 페이지에 이어지는 설명입니다."},
        {"type": "heading", "level": 3, "text": "오탐 근거"},
        {"type": "paragraph", "text": "기록된 근거가 없습니다."},
    ]
    before = copy.deepcopy(source)
    content = renderer.render_pdf(source)
    assert content.count(b"/Type /Page\n") >= 3
    assert source == before
