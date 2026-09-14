"""Bounded observations about captured HTTP text, not a wire validator.

Never reconstruct missing bytes, decode content, execute input, or infer an
attack. Unicode WAF text is not evidence of its original wire encoding.
"""
import json
import re


INTEGRITY_VERSION = "request-integrity-v2"
LEGACY_INTEGRITY_VERSION = "request-integrity-v1"
INTEGRITY_V2_RULES_VERSIONS = frozenset({"waf-system-v2.12"})
INTEGRITY_RULES_VERSIONS = frozenset({"waf-system-v2.9", "waf-system-v2.10", "waf-system-v2.11"}) | INTEGRITY_V2_RULES_VERSIONS
MAX_PAYLOAD_CHARS = 262144
MAX_HEAD_CHARS = 32768
MAX_JSON_CHARS = 65536
MAX_JSON_DEPTH = 64
MAX_LENGTH_VALUES = 32
MAX_CHUNKS = 1024
_DECIMAL = re.compile(r"[0-9]+\Z")
_HEX = re.compile(r"[0-9a-fA-F]{1,16}\Z")
_TOKEN = re.compile(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+\Z")

# Only these observed problems may restrict a normal verdict, and only when
# its evidence intersects the affected region. Other findings are hints.
NORMAL_GUARD_CODES = frozenset({
    "content_length_conflict", "transfer_encoding_content_length",
    "content_length_invalid", "declared_body_not_captured",
    "body_shorter_than_content_length", "chunked_body_incomplete",
    "json_container_unclosed",
})


def _json_shape(text):
    """Lexical bounds before json.loads; unclosed does not prove log loss."""
    stack, quoted, escaped = [], False, False
    for char in text:
        if quoted:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                quoted = False
        elif char == '"':
            quoted = True
        elif char in "[{":
            stack.append(char)
            if len(stack) > MAX_JSON_DEPTH:
                return "limit"
        elif char in "]}":
            if not stack or stack.pop() != {"]": "[", "}": "{"}[char]:
                return "invalid"
    return "unclosed" if stack or quoted else "closed"


def _chunk_status(body):
    """ASCII-only CRLF chunk framing; unsupported extensions stay unknown."""
    position = 0
    for _ in range(MAX_CHUNKS):
        end = body.find("\r\n", position)
        if end < 0:
            return "incomplete" if "\n" not in body[position:] else "unexamined"
        size_text = body[position:end]
        if ";" in size_text:
            return "unexamined"
        if not _HEX.fullmatch(size_text):
            return "unexamined"
        size = int(size_text, 16)
        position = end + 2
        if size == 0:
            # A zero chunk still needs the final empty line after trailers.
            for _ in range(MAX_LENGTH_VALUES):
                end = body.find("\r\n", position)
                if end < 0:
                    return "incomplete"
                if end == position:
                    return "extra" if end + 2 < len(body) else "closed"
                name, colon, value = body[position:end].partition(":")
                if not colon or not _TOKEN.fullmatch(name) or "\r" in value or "\n" in value:
                    return "unexamined"
                position = end + 2
            return "unexamined"
        if position + size + 2 > len(body):
            return "incomplete"
        if body[position + size:position + size + 2] != "\r\n":
            return "unexamined"
        position += size + 2
    return "unexamined"


def _partial_declared_body(raw, parsed, result):
    """Only compare an explicit declaration with the capture, not HTTP framing.

    The caller established that only the protocol is absent. No reconstruction,
    transfer decoding, JSON parsing or conclusion about collection loss occurs.
    """
    result["limitations"].append("protocol_missing_body_check_only")
    spans, headers = parsed["raw_source_spans"], parsed["headers"]
    ranges = spans.get("body", [])
    if not ranges or ranges[0][0] > MAX_HEAD_CHARS:
        result["limitations"].append("header_inspection_limit")
        return result
    body_start = ranges[0][0]
    body = raw[body_start:]
    if headers.get("transfer-encoding"):
        result["limitations"].append("transfer_coding_not_supported")
        return result
    lengths = headers.get("content-length", [])
    if not lengths:
        result["limitations"].append("content_length_not_declared")
        return result
    values = [value.strip(" \t") for line in lengths for value in line.split(",")]
    if len(values) > MAX_LENGTH_VALUES or any(len(value) > 128 for value in values):
        result["limitations"].append("content_length_inspection_limit")
        return result
    if not all(_DECIMAL.fullmatch(value) for value in values):
        result["limitations"].append("partial_content_length_not_comparable")
        return result
    normalized = {value.lstrip("0") or "0" for value in values}
    if len(normalized) != 1:
        result["limitations"].append("partial_content_length_not_comparable")
        return result
    value = normalized.pop()
    declared = int(value) if len(value) <= 9 else MAX_PAYLOAD_CHARS + 1
    encoded = any(value.strip().lower() != "identity" for value in headers.get("content-encoding", []))
    if body and (encoded or not body.isascii()):
        result["limitations"].append("wire_body_length_not_comparable")
        return result
    code = ("declared_body_not_captured" if not body and declared > 0 else
            "body_shorter_than_content_length" if len(body) < declared else None)
    if code:
        affected = [*spans.get("headers.content-length", []), [body_start, len(raw)]]
        if not body:
            affected += spans.get("headers.content-type", [])
        result["issues"].append({"code": code, "scope": "body",
                                 "source_spans": [[0, len(raw)]], "affected_spans": affected})
    result["status"] = "issues_observed" if code else "no_issue_observed"
    return result


def assess_request_integrity(raw: str, parsed: dict, *, version: str = INTEGRITY_VERSION) -> dict:
    if version not in {INTEGRITY_VERSION, LEGACY_INTEGRITY_VERSION}:
        raise ValueError("request_integrity_version_unsupported")
    result = {"version": version, "status": "not_checked", "issues": [], "limitations": []}

    def limitation(code):
        if code not in result["limitations"]:
            result["limitations"].append(code)

    if len(raw) > MAX_PAYLOAD_CHARS:
        limitation("payload_inspection_limit")
        return result  # Never inspect a clipped prefix as though it were EOF.
    spans = parsed.get("raw_source_spans", {})
    if (version == INTEGRITY_VERSION and parsed.get("parse_status") == "partial"
            and parsed.get("protocol") is None
            and set(parsed.get("warnings", [])) == {"request_protocol_not_recognized"}
            and len(parsed.get("request_line", "").split(" ")) == 2):
        return _partial_declared_body(raw, parsed, result)
    if parsed.get("protocol") not in {"HTTP/1.0", "HTTP/1.1"} or parsed.get("parse_status") != "success":
        limitation("http_structure_not_supported")
        return result
    body_ranges = spans.get("body", [])
    if not body_ranges or body_ranges[0][0] > MAX_HEAD_CHARS:
        limitation("header_inspection_limit")
        return result
    body_start = body_ranges[0][0]
    body = raw[body_start:]
    headers = parsed["headers"]
    head_spans = [[0, body_start]]

    def add(code, scope, affected):
        # Proof includes the full header and body for length/EOF observations.
        # Only visible complete proofs may later enter the model/guard.
        proof = [[0, len(raw)]] if scope == "body" else head_spans
        result["issues"].append({"code": code, "scope": scope,
                                 "source_spans": proof, "affected_spans": affected})

    length_ranges = spans.get("headers.content-length", [])
    transfer_ranges = spans.get("headers.transfer-encoding", [])
    body_affected = [*length_ranges, *transfer_ranges, [body_start, len(raw)]]
    lengths = headers.get("content-length", [])
    transfers = headers.get("transfer-encoding", [])
    declared = None
    if lengths:
        # Header size was bounded above; comma lists and leading zeros are
        # supported. Equal duplicate values are not a conflict.
        values = [value.strip(" \t") for line in lengths for value in line.split(",")]
        if len(values) > MAX_LENGTH_VALUES or any(len(value) > 128 for value in values):
            limitation("content_length_inspection_limit")
        elif not all(_DECIMAL.fullmatch(value) for value in values):
            add("content_length_invalid", "framing", length_ranges)
        else:
            normalized = {value.lstrip("0") or "0" for value in values}
            if len(normalized) != 1:
                add("content_length_conflict", "framing", body_affected)
            else:
                value = normalized.pop()
                # Values greater than the entire permitted capture need not
                # be converted to arbitrary-sized integers.
                declared = int(value) if len(value) <= 9 else MAX_PAYLOAD_CHARS + 1
    if lengths and transfers:
        add("transfer_encoding_content_length", "framing", body_affected)

    encoded = any(value.strip().lower() != "identity" for value in headers.get("content-encoding", []))
    if declared is not None and not transfers:
        if not body and declared > 0:
            affected = body_affected + (spans.get("headers.content-type", []) if version == INTEGRITY_VERSION else [])
            add("declared_body_not_captured", "body", affected)
        elif encoded or not body.isascii():
            limitation("wire_body_length_not_comparable")
        elif len(body) < declared:
            add("body_shorter_than_content_length", "body", body_affected)
        elif len(body) > declared:
            # Extra capture can be pipelined messages or export decoration;
            # neither attack intent nor truncation is inferred.
            add("body_exceeds_content_length", "body", body_affected)
    if transfers:
        if len(transfers) == 1 and transfers[0].strip().lower() == "chunked" and body.isascii():
            chunk_status = _chunk_status(body)
            if chunk_status == "incomplete":
                add("chunked_body_incomplete", "body", body_affected)
            elif chunk_status == "extra":
                add("data_after_chunked_body", "body", body_affected)
            elif chunk_status == "unexamined":
                limitation("chunk_framing_not_supported")
        else:
            limitation("transfer_coding_not_supported")

    content_types = [value.partition(";")[0].strip().lower() for value in headers.get("content-type", [])]
    is_json = len(content_types) == 1 and (
        content_types[0] == "application/json" or
        (content_types[0].startswith("application/") and content_types[0].endswith("+json")))
    if is_json and not transfers and not encoded and body.lstrip().startswith(("{", "[")):
        if len(body) > MAX_JSON_CHARS:
            limitation("json_inspection_limit")
        else:
            shape = _json_shape(body)
            if shape == "limit":
                limitation("json_inspection_limit")
            elif shape == "unclosed":
                add("json_container_unclosed", "body", [[body_start, len(raw)]])
            elif shape == "closed":
                duplicate = False

                def pairs(items):
                    nonlocal duplicate
                    keys = set()
                    for key, _ in items:
                        duplicate |= key in keys
                        keys.add(key)
                    return None  # No need to retain or compare attacker values.

                try:
                    json.loads(body, object_pairs_hook=pairs,
                               parse_int=lambda _: None, parse_float=lambda _: None,
                               parse_constant=lambda _: None)
                except (ValueError, RecursionError):
                    limitation("json_syntax_not_validated")
                else:
                    if duplicate:
                        add("json_duplicate_keys", "body", [[body_start, len(raw)]])
            else:
                limitation("json_syntax_not_validated")
    result["status"] = "issues_observed" if result["issues"] else "no_issue_observed"
    return result


def submitted_integrity(assessment: dict, retained_spans, budget: int) -> dict:
    """Keep whole fixed-code findings with fully submitted proof, never values."""
    result = {"version": assessment["version"], "status": assessment["status"], "issues": [],
              "limitations": list(assessment["limitations"]), "omitted_issues": False,
              "meaning": "capture_observations_not_attack_or_wire_validation"}
    for finding in assessment["issues"]:
        visible = all(any(a <= start <= end <= b for a, b in retained_spans)
                      for start, end in finding["source_spans"])
        candidate = {**result, "issues": [*result["issues"], finding]}
        if visible and len(json.dumps(candidate, ensure_ascii=False, separators=(",", ":"))) <= budget:
            result["issues"].append(finding)
        else:
            result["omitted_issues"] = True
    return result
