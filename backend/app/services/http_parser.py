import re
from typing import Any
from urllib.parse import parse_qsl, urlsplit


HTTP_PARSER_VERSION = "generic-http-v2"
_TOKEN = re.compile(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+\Z")
_PROTOCOL = re.compile(r"HTTP/[0-9]\.[0-9]\Z")
_HEADER_SEPARATOR = re.compile(r"\r?\n\r?\n")
_CONTROL = re.compile(r"[\x00-\x08\x0a-\x1f\x7f]")


def parse_http_payload(payload: str) -> dict[str, Any]:
    """Produce best-effort hints without rewriting the source or decoding escapes.

    Success concerns the request line and header structure, not HTTP framing,
    payload completeness, or attack validity. Malformed hints never prevent the
    caller from analysing the original payload.
    """
    separator = _HEADER_SEPARATOR.search(payload)
    if separator is not None:
        head, body = payload[:separator.start()], payload[separator.end():]
    else:
        head, body = payload, ""
    lines = head.replace("\r\n", "\n").split("\n")
    first_line = lines[0]
    parts = first_line.split(" ", 2)
    method = target = protocol = None
    warnings: list[str] = []

    def warn(code: str) -> None:
        # Codes are fixed and deduplicated: malformed input cannot fill a
        # warning array with payload-bearing messages or repeated entries.
        if code not in warnings:
            warnings.append(code)

    # A recognizable request must have a token method and a request-target
    # shape. Two arbitrary words (or an HTTP response) are not a request.
    recognizable = (
        len(parts) >= 2
        and _TOKEN.fullmatch(parts[0]) is not None
        and bool(parts[1])
        and (
            parts[1].startswith(("/", "http://", "https://"))
            or (parts[0] == "OPTIONS" and parts[1] == "*")
            or (parts[0] == "CONNECT" and ":" in parts[1])
        )
    )
    if recognizable:
        method, target = parts[0], parts[1]
        if len(parts) == 3 and _PROTOCOL.fullmatch(parts[2]):
            protocol = parts[2]
        else:
            warn("request_protocol_not_recognized")
        if re.search(r"[\x00-\x20\x7f]", target) or "#" in target:
            warn("request_target_not_recognized")
        if separator is None:
            warn("headers_terminator_missing")
    else:
        warn("request_line_not_recognized")
    if "\\r\\n" in head or "\\n" in head:
        # WAF exports sometimes contain literal escapes. They are ambiguous
        # with literal header/body data, so do not silently unescape them.
        warn("escaped_line_endings_not_decoded")

    headers: dict[str, list[str]] = {}
    if recognizable:
        for line in lines[1:]:
            name, colon, value = line.partition(":")
            if not colon or not _TOKEN.fullmatch(name) or _CONTROL.search(value):
                warn("header_line_not_recognized")
                continue
            headers.setdefault(name.lower(), []).append(value.strip(" \t"))

    uri = target
    query_pairs = []
    if target is not None and "request_target_not_recognized" not in warnings:
        try:
            if target.startswith("/"):
                # Origin-form paths beginning // or containing brackets are
                # paths, not authorities for urllib to reinterpret.
                uri, question, query = target.partition("?")
                query_pairs = parse_qsl(query, keep_blank_values=True) if question else []
            elif target.startswith(("http://", "https://")):
                split_target = urlsplit(target)
                if not split_target.hostname:
                    warn("request_target_not_recognized")
                # Accessing port also validates malformed/out-of-range ports.
                _ = split_target.port
                uri = split_target.path or target
                query_pairs = parse_qsl(split_target.query, keep_blank_values=True)
            elif method == "CONNECT":
                authority = urlsplit("//" + target)
                if not authority.hostname or authority.port is None or authority.path or authority.query:
                    warn("request_target_not_recognized")
        except ValueError:
            # urllib errors may contain an input value; never expose them.
            warn("request_target_not_recognized")
    return {
        "parse_status": ("partial" if warnings else "success") if recognizable else "failed",
        "request_line": first_line,
        "method": method,
        "uri": uri,
        "query": query_pairs,
        "protocol": protocol,
        "headers": headers,
        "body": body,
        "warnings": warnings,
    }
