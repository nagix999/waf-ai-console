from typing import Any
from urllib.parse import parse_qsl, urlsplit


def parse_http_payload(payload: str) -> dict[str, Any]:
    separator = "\r\n\r\n" if "\r\n\r\n" in payload else "\n\n"
    if separator in payload:
        head, body = payload.split(separator, 1)
    else:
        head, body = payload, ""
    lines = head.replace("\r\n", "\n").split("\n")
    first_line = lines[0].strip() if lines else ""
    parts = first_line.split(" ", 2)
    method = target = protocol = None
    warnings: list[str] = []
    if len(parts) >= 2:
        method, target = parts[0], parts[1]
        protocol = parts[2] if len(parts) == 3 else None
    else:
        warnings.append("request_line_not_recognized")

    headers: dict[str, list[str]] = {}
    for line in lines[1:]:
        if not line:
            continue
        if ":" not in line:
            warnings.append("header_line_not_recognized")
            continue
        name, value = line.split(":", 1)
        headers.setdefault(name.strip().lower(), []).append(value.strip())

    split_target = urlsplit(target or "")
    query_pairs = parse_qsl(split_target.query, keep_blank_values=True)
    return {
        "parse_status": "partial" if warnings else "success",
        "request_line": first_line,
        "method": method,
        "uri": split_target.path or target,
        "query": query_pairs,
        "protocol": protocol,
        "headers": headers,
        "body": body,
        "warnings": warnings,
    }
