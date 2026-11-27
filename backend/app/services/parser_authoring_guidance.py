"""Optional authoring-only observations, never rules or runtime selection.

Only the current group's already-admitted authoring payloads enter this helper.
It reuses the pinned JSON reader, does not infer stable values from examples,
and returns no request text. JSON paths remain untrusted, in-memory input to
the same approved model; they must never enter plaintext job diagnostics.
"""
from collections import Counter
import json

from .parser_engine_v1 import _REQUEST, _capture_line_endings
from .parser_rule_engine import MAX_SOURCE_BYTES, ParserRuleError, _Budget, _Context

MAX_SAMPLES = 100
MAX_PATHS = 8
MAX_STRING_CHECKS = 32
MAX_GUIDANCE_BYTES = 4096


def _request_mode(text):
    mode = _capture_line_endings(text)
    delimiter = {"actual": "\n", "escaped_crlf": "\\r\\n", "escaped_lf": "\\n"}[mode]
    first = text.split(delimiter, 1)[0].removesuffix("\r")
    match = _REQUEST.fullmatch(first)
    if match is None:
        return None
    method, target = match.group(1), match.group(3)
    if (target.startswith(("/", "http://", "https://"))
            or method == "OPTIONS" and target == "*" or method == "CONNECT" and ":" in target):
        return mode
    return None


def _pointer(path):
    if len(path) > 16:
        return None
    pointer = "/" + "/".join(part.replace("~", "~0").replace("/", "~1") for part in path) if path else ""
    return pointer if len(pointer) <= 512 else None


def authoring_structure_hints(samples):
    """Describe bounded observations without assuming a group is homogeneous.

    No literal-prefix, scalar-value, method or URI predicate is inferred. An
    observed JSON path is only an example for the model to consider, not an
    extractor qualified on unseen data or an automatically selected rule.
    """
    if not isinstance(samples, list) or not 1 <= len(samples) <= MAX_SAMPLES:
        return None
    budget, inspected, json_count = _Budget(), 0, 0
    raw_modes, paths, truncated = Counter(), {}, False
    for sample in samples:
        try:
            budget.check()
            raw = sample["raw_payload"]
            if not isinstance(raw, str) or len(raw) > MAX_SOURCE_BYTES or len(raw.encode("utf-8")) > MAX_SOURCE_BYTES:
                return None
            mode = _request_mode(raw)
            if mode is not None:
                raw_modes[mode] += 1
                inspected += 1
                continue
            try:
                document = _Context(raw, budget).json()
            except ParserRuleError as error:
                if error.code == "parser_rule_time_limit":
                    raise
                inspected += 1
                continue
            local_paths = []
            # A capped traversal avoids scanning arbitrary JSON string values.
            for index, (path, extracted) in enumerate(document.strings.items()):
                budget.check()
                if index >= MAX_STRING_CHECKS:
                    truncated = True
                    break
                pointer = _pointer(path)
                mode = _request_mode(extracted.text) if pointer is not None else None
                if mode is not None:
                    local_paths.append((pointer, mode))
            # Do not count a sample whose inspection was interrupted by time.
            json_count += 1
            for pointer, mode in local_paths:
                if pointer not in paths and len(paths) >= MAX_PATHS:
                    truncated = True
                    continue
                paths.setdefault(pointer, Counter())[mode] += 1
            inspected += 1
        except ParserRuleError:
            truncated = True
            break
        except (KeyError, TypeError, ValueError, UnicodeError):
            return None
    value = {
        "version": "parser-authoring-structure-v1", "sample_count": len(samples),
        "inspected_sample_count": inspected, "truncated": truncated or inspected < len(samples),
        "raw_http_line_endings": dict(raw_modes), "json_document_count": json_count,
        "json_request_paths": [
            {"json_pointer": pointer, "observed_sample_count": sum(modes.values()),
             "line_endings": dict(modes),
             "match_example": {"all": [{"kind": "json_string_exists", "json_pointer": pointer}]},
             "extract_example": {"kind": "json_string", "json_pointer": pointer}}
            for pointer, modes in paths.items()
        ],
    }
    while len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")) > MAX_GUIDANCE_BYTES:
        value["json_request_paths"].pop()
        value["truncated"] = True
    return value
