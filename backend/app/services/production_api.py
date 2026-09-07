"""Live API documentation generated from the same definition used at ingestion."""
from importlib.resources import files
import json

from .input_schemas import get_active_schema, load_definition, to_json_schema


def active_contract(db, crypto):
    version = get_active_schema(db, crypto)
    fields = load_definition(version, crypto)
    metadata = {
        "version_id": version.id, "version_number": version.version_number,
        "content_hash": version.content_hash, "field_count": len(fields),
        "selection_origin": "active", "field_metadata_usage": "history_only",
        "field_metadata_sent_to_model": False,
    }
    return metadata, fields, to_json_schema(fields)


def markdown_contract(metadata, fields, json_schema, payload_max_bytes):
    template = files("app").joinpath("data/production_api.md").read_text(encoding="utf-8")
    start, end = "<!-- INPUT_SCHEMA_START -->", "<!-- INPUT_SCHEMA_END -->"
    before, section = template.split(start, 1)
    _, after = section.split(end, 1)
    def cell(value):
        # Definitions are administrator text, not executable HTML/Markdown.
        return str(value).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("|", "&#124;").replace("`", "&#96;").replace("\n", " ")
    lines = [f"적용 입력 스키마: **v{metadata['version_number']}** · `{metadata['version_id']}`",
             f"정의 SHA-256: `{metadata['content_hash']}`", "",
             f"payload UTF-8 최대 크기: **{payload_max_bytes} bytes**. 문자 길이와 별도 검증합니다.", "",
             "| 필드 | 타입 | 필수 | null 허용 | 설명 |",
             "| --- | --- | --- | --- | --- |"]
    for field in fields:
        lines.append(f"| `{field.name}` | {field.type} | {'O' if field.required else 'X'} | {'O' if field.nullable else 'X'} | {cell(field.description)} |")
    # Escaped fence markers prevent a field description from ending the block.
    definition = json.dumps(json_schema, ensure_ascii=False, indent=2).replace("`", "\\u0060").replace("<", "\\u003c")
    lines.extend(["", "필드별 길이·범위·허용값·하위 구조의 현재 제한:", "", "```json", definition, "```", ""])
    return before + "\n".join(lines) + after
