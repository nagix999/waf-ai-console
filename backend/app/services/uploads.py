import csv
import io
import json
from typing import Any


class UploadFormatError(ValueError):
    pass


def _unique_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result = {}
    for key, value in pairs:
        if key in result:
            raise UploadFormatError("duplicate_json_field")
        result[key] = value
    return result


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
        if len(set(reader.fieldnames)) != len(reader.fieldnames):
            raise UploadFormatError("duplicate_csv_header")
        return [dict(row) for row in reader]

    if lowered.endswith(".json"):
        try:
            parsed = json.loads(text, object_pairs_hook=_unique_json_object)
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


def extract_test_upload_row(row: dict[str, Any]) -> tuple[dict[str, Any], str | None]:
    """Detach only the optional test-file answer, before event validation/storage.

    Other reserved evaluation metadata deliberately remains for AnalysisInput to
    reject. This is not a recursive payload sanitizer or a label inference step.
    """
    event = dict(row)
    expected = event.pop("expected_verdict", None)
    if expected is None or expected == "":
        return event, None
    if not isinstance(expected, str) or expected not in {
        "true_positive", "false_positive", "inconclusive",
    }:
        raise UploadFormatError("invalid_expected_verdict")
    return event, expected


def normalize_upload_row(row: dict[str, Any]) -> dict[str, Any]:
    result = dict(row)
    for key in ("src_port", "dest_port"):
        if key not in result:
            continue  # Preserve absence for versioned required-field checks.
        value = result.get(key)
        if value in (None, ""):
            result[key] = None
        elif isinstance(value, str):
            result[key] = int(value)
    return result
