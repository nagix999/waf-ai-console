import csv
import io
import json
from typing import Any


class UploadFormatError(ValueError):
    pass


def parse_upload(filename: str, content: bytes) -> list[dict[str, Any]]:
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise UploadFormatError("file_must_be_utf8") from exc

    lowered = filename.lower()
    if lowered.endswith(".csv"):
        reader = csv.DictReader(io.StringIO(text))
        if not reader.fieldnames:
            raise UploadFormatError("csv_header_required")
        return [dict(row) for row in reader]

    if lowered.endswith(".json"):
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError as exc:
            raise UploadFormatError(f"invalid_json:{exc.lineno}") from exc
        if isinstance(parsed, dict) and "events" in parsed:
            parsed = parsed["events"]
        elif isinstance(parsed, dict):
            parsed = [parsed]
        if not isinstance(parsed, list) or any(not isinstance(item, dict) for item in parsed):
            raise UploadFormatError("json_must_be_event_object_or_array")
        return parsed

    raise UploadFormatError("only_csv_or_json_supported")


def normalize_upload_row(row: dict[str, Any]) -> dict[str, Any]:
    result = dict(row)
    for key in ("src_port", "dest_port"):
        value = result.get(key)
        if value in (None, ""):
            result[key] = None
        elif isinstance(value, str):
            result[key] = int(value)
    return result
