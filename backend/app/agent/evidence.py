"""Resolve evidence against its claimed raw source, without decoding payloads."""

import re
from collections.abc import Mapping


_REQUEST_LINE = re.compile(r"([!#$%&'*+.^_`|~0-9A-Za-z-]+)[ \t]+([^ \t\r\n]+)[ \t]+(HTTP/\d+(?:\.\d+)?)")
_HEADER_NAME = re.compile(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+")


class EvidenceSourceResolver:
    """Validate location and exact text only; this does not validate its meaning."""

    def __init__(self, raw_payload: str, event: Mapping[str, object]) -> None:
        self.raw_payload = raw_payload
        self.event = event
        self.payload_parts: dict[str, str] = {}
        self.header_lines: list[str] = []
        separator = re.search(r"\r?\n\r?\n", raw_payload)
        head = raw_payload[:separator.start()] if separator else raw_payload
        first_break = re.search(r"\r?\n", head)
        request_line = head[:first_break.start()] if first_break else head
        request = _REQUEST_LINE.fullmatch(request_line)
        if request is None:
            # Unrecognized formats can still cite the full raw payload. Do not
            # invent a body/query/header boundary from an ambiguous payload.
            return
        method, target, protocol = request.groups()
        headers = head[first_break.end():] if first_break else ""
        self.header_lines = re.split(r"\r?\n", headers) if headers else []
        self.payload_parts = {
            "request_line": request_line,
            "method": method,
            "uri": target,
            "request_target": target,
            "protocol": protocol,
            "headers": headers,
        }
        target_without_fragment = target.split("#", 1)[0]
        path, query_separator, query = target_without_fragment.partition("?")
        # Preserve raw absolute-form request targets; remove only the authority
        # prefix for the path view. URL parsing/decoding could alter evidence.
        absolute_prefix = re.match(r"[A-Za-z][A-Za-z0-9+.-]*://[^/]*", path)
        has_path = path.startswith("/") or absolute_prefix is not None
        if absolute_prefix:
            path = path[absolute_prefix.end():]
        if has_path:
            self.payload_parts["path"] = path
        if has_path and query_separator:
            self.payload_parts["query"] = query
        if separator:
            self.payload_parts["body"] = raw_payload[separator.end():]

    def matches(self, field: str, excerpt: str) -> bool:
        if not excerpt.strip():
            return False
        field = field.strip().removeprefix("event.")
        if field in {"payload", "raw_payload"}:
            return excerpt in self.raw_payload
        if field.startswith("payload."):
            return any(excerpt in source for source in self._payload_sources(field[8:]))
        value: object = self.event
        # Both extra_fields.items.0.value and items[0].value identify the same
        # concrete scalar. Container fields never search all their descendants.
        path = re.sub(r"\[(0|[1-9][0-9]*)\]", r".\1", field)
        for part in path.split("."):
            if isinstance(value, Mapping) and part in value:
                value = value[part]
            elif isinstance(value, (list, tuple)) and part.isascii() and part.isdecimal():
                index = int(part)
                if index >= len(value):
                    return False
                value = value[index]
            else:
                return False
        if isinstance(value, bool) or value is None:
            return False
        if isinstance(value, (str, int, float)):
            return excerpt in str(value)
        return False

    def _payload_sources(self, field: str) -> list[str]:
        if field in self.payload_parts:
            return [self.payload_parts[field]]
        if field.startswith("headers."):
            requested_name = field[len("headers."):]
            if not _HEADER_NAME.fullmatch(requested_name):
                return []
            return [
                line for line in self.header_lines
                if line.partition(":")[1]
                and line.partition(":")[0].lower() == requested_name.lower()
            ]
        if field.startswith("query."):
            requested_name = field[len("query."):]
            return [
                pair for pair in self.payload_parts.get("query", "").split("&")
                if pair.partition("=")[0] == requested_name
            ]
        return []
