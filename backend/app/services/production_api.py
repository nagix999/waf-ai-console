"""Live API documentation generated from the same definition used at ingestion."""
from importlib.resources import files
from copy import deepcopy
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


def request_contract(event_schema):
    """Admission-only reference metadata must not enter the versioned event schema."""
    from ..schemas import AnalysisRequest
    definition = deepcopy(event_schema)
    definition["title"] = "AnalysisRequest"
    for name in ("expected_verdict", "initial_verdict", "initial_probability", "initial_model_version"):
        definition["properties"][name] = AnalysisRequest.model_json_schema()["properties"][name]
        definition["propertyNames"]["not"]["enum"].remove(name)
    return definition


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
    lines.extend(["", "`expected_verdict`는 선택 요청 항목이며 위 입력 스키마 필드가 아닙니다. Test·Production 모두 참고 답안으로 별도 저장하고 LLM에는 전달하지 않습니다."])
    lines.extend(["", "1차 판정은 Production 서비스 키의 `POST /api/v1/analyses`에서만 받습니다. `initial_verdict`(true_positive/false_positive)와 `initial_probability`(유한수 0~1)는 함께 제공하고, `initial_model_version`은 선택입니다. 확률은 1차 판정 클래스의 신뢰도이며 심층 판정 신뢰도와 같은 척도가 아닙니다. Agent 입력·정답에는 사용하지 않습니다. Test·파일·정답 데이터·사용자 스키마에서는 거부합니다. 동일 이벤트 재접수에서 추가·제거·변경하면 `409 initial_assessment_conflict`입니다."])
    definition = json.dumps(request_contract(json_schema), ensure_ascii=False, indent=2).replace("`", "\\u0060").replace("<", "\\u003c")
    lines.extend(["", "필드별 길이·범위·허용값·하위 구조의 현재 제한:", "", "```json", definition, "```", ""])
    return before + "\n".join(lines) + after
