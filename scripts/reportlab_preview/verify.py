"""Check synthetic PDF text preservation and page bounds using local Poppler.

This separate verification utility is not a renderer dependency.
"""
import argparse
import json
from pathlib import Path
import re
import subprocess
import xml.etree.ElementTree as ET


def strings(document):
    yield document["title"]
    for section in [{"title": "", "blocks": document["intro"]}, *document["sections"]]:
        if section["title"]:
            yield section["title"]
        for block in section["blocks"]:
            if "text" in block:
                yield block["text"]
            yield from block.get("items", [])
            # Generic key/value headers are replaced by typographic hierarchy.
            if block.get("headers") != ["항목", "내용"]:
                yield from block.get("headers", [])
            for row in block.get("rows", []):
                yield from row


def flattened(text):
    return re.sub(r"\s+", "", text)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    args = parser.parse_args()
    for name in ("01-short", "02-long", "03-evidence"):
        pdf = args.directory / f"{name}.pdf"
        document = json.loads((args.directory / f"{name}.json").read_text())
        extracted = subprocess.check_output(["pdftotext", "-raw", str(pdf), "-"], text=True)
        lines = [line for line in extracted.splitlines() if not (
            line.startswith("WAF AI /") or "디자인 시안 · 가상 데이터" in line
            or line.startswith("ReportLab 시안 ") or line.startswith("WAF AI Console ·")
            or line == "분석 보고서" or re.fullmatch(r"\d+", line)
            or re.fullmatch(r"(?:정탐 근거|오탐 근거|참고 내용|구분 미기록) · 계속", line))]
        flattened_text = flattened("\n".join(lines))
        # A page number may equal a legitimate numeric metadata field. Preserve
        # numbers when validating short fields; remove furniture for long text.
        raw_text = flattened(extracted)
        missing = [index for index, value in enumerate(strings(document))
                   if flattened(value) not in (raw_text if len(value) < 80 else flattened_text)]
        if missing:
            raise SystemExit(f"{name}: missing text block indices: {missing}")
        bbox = ET.fromstring(subprocess.check_output(["pdftotext", "-bbox", str(pdf), "-"]))
        pages = bbox.findall(".//{*}page")
        for page in pages:
            width, height = float(page.get("width")), float(page.get("height"))
            for word in page.findall(".//{*}word"):
                x0, x1 = float(word.get("xMin")), float(word.get("xMax"))
                y0, y1 = float(word.get("yMin")), float(word.get("yMax"))
                assert 38 <= x0 <= x1 <= width - 38, f"{name}: horizontal overflow"
                assert 20 <= y0 <= y1 <= height - 20, f"{name}: vertical overflow"
        data = pdf.read_bytes()
        assert b"/FontFile2" in data and b"/ToUnicode" in data
        assert b"/URI" not in data and b"/JavaScript" not in data
        print(f"{name}: {len(pages)} pages; all {len(list(strings(document)))} text fields retained; text inside page bounds")


if __name__ == "__main__":
    main()
