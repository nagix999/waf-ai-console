"""Bounded static Log4j-style lookup hints, never a lookup evaluator.

Only ASCII lower/upper and the explicit empty/colon-key default candidate forms
``${:-text}`` / ``${::-text}`` are simplified. Defaults are conditional candidates,
not knowledge that a property is absent. Unknown lookups, escaped ``$${...}``,
and unresolved case arguments stay unchanged. A resulting ``${jndi:...}`` is
kept as text: no naming service, DNS, Java, environment, property or network
lookup is performed. Backslash is literal, not Log4j's dollar escape character.

This is intentionally narrower than Log4j's version/configuration-dependent
substitution system. Malformed or over-budget input is returned intact with a
static warning. Returned text is as sensitive/untrusted as the original.

Syntax reference: https://logging.apache.org/log4j/2.x/manual/lookups.html
Default/escape reference:
https://logging.apache.org/log4j/2.x/manual/configuration.html#property-substitution
"""

from __future__ import annotations

from dataclasses import dataclass, field


MAX_LOG4J_CHARS = 4_096
MAX_LOG4J_OUTPUT_CHARS = 4_096
MAX_LOG4J_DEPTH = 8
MAX_LOG4J_NODES = 128
MAX_LOG4J_OPERATIONS = 65_536


class _Stopped(Exception):
    """Only fixed internal codes; never includes an input fragment."""


@dataclass
class _Budget:
    operations: int = 0
    nodes: int = 0

    def spend(self, count: int = 1) -> None:
        self.operations += count
        if self.operations > MAX_LOG4J_OPERATIONS:
            raise _Stopped("log4j_operation_limit_reached")

    def node(self, depth: int) -> None:
        self.nodes += 1
        if depth > MAX_LOG4J_DEPTH:
            raise _Stopped("log4j_depth_limit_reached")
        if self.nodes > MAX_LOG4J_NODES:
            raise _Stopped("log4j_node_limit_reached")


@dataclass(frozen=True)
class _Lookup:
    start: int
    end: int
    escaped: bool
    parts: list[str | _Lookup]


@dataclass
class _Normalized:
    text: str
    warnings: list[str] = field(default_factory=list)
    resolved: bool = True


def _warn(warnings: list[str], code: str) -> None:
    if code not in warnings:
        warnings.append(code)


def _parse(
    value: str, index: int, depth: int, budget: _Budget, *, closing: bool = False,
) -> tuple[list[str | _Lookup], int]:
    """Single bounded parse; no repeated end-of-string brace searches."""
    parts: list[str | _Lookup] = []
    literal_start = index
    while index < len(value):
        budget.spend()
        if value[index] == "}" and closing:
            if literal_start < index:
                parts.append(value[literal_start:index])
            return parts, index + 1
        if value[index] != "$":
            index += 1
            continue
        dollar_end = index + 1
        while dollar_end < len(value) and value[dollar_end] == "$":
            budget.spend()
            dollar_end += 1
        if dollar_end == len(value) or value[dollar_end] != "{":
            index = dollar_end
            continue
        if literal_start < index:
            parts.append(value[literal_start:index])
        budget.node(depth + 1)
        nested, end = _parse(value, dollar_end + 1, depth + 1, budget, closing=True)
        parts.append(_Lookup(index, end, dollar_end - index > 1, nested))
        index = literal_start = end
    if closing:
        raise _Stopped("malformed_log4j_lookup")
    if literal_start < index:
        parts.append(value[literal_start:index])
    return parts, index


def _join(parts: list[str | _Lookup], value: str, budget: _Budget) -> _Normalized:
    output = []
    warnings: list[str] = []
    resolved = True
    for part in parts:
        if isinstance(part, str):
            budget.spend(len(part))
            output.append(part)
            continue
        normalized = _normalize(part, value, budget)
        output.append(normalized.text)
        resolved = resolved and normalized.resolved
        for warning in normalized.warnings:
            _warn(warnings, warning)
    text = "".join(output)
    if len(text) > MAX_LOG4J_OUTPUT_CHARS:
        raise _Stopped("log4j_output_too_long")
    return _Normalized(text, warnings, resolved)


def _normalize(lookup: _Lookup, value: str, budget: _Budget) -> _Normalized:
    original = value[lookup.start:lookup.end]
    budget.spend(len(original))
    if lookup.escaped:
        return _Normalized(original, ["escaped_log4j_lookup"], False)

    # A literal unknown namespace protects its complete subtree. In particular,
    # ${env:NAME:-fallback} is neither queried nor replaced by its default, and
    # literal transformations inside its property name remain untouched too.
    first = lookup.parts[0] if lookup.parts and isinstance(lookup.parts[0], str) else ""
    prefix, separator, _ = first.partition(":")
    if separator and prefix not in ("", "lower", "upper") and prefix.lower() != "jndi":
        return _Normalized(original, ["unresolved_lookup"], False)

    body = _join(lookup.parts, value, budget)
    prefix, separator, key = body.text.partition(":")

    if body.text.startswith(("::-", ":-")):
        if not body.resolved:
            _warn(body.warnings, "unresolved_lookup")
            return _Normalized(original, body.warnings, False)
        default = body.text[3:] if body.text.startswith("::-") else body.text[2:]
        if not default:
            return _Normalized(original, ["unresolved_lookup"], False)
        _warn(body.warnings, "default_lookup_candidate")
        return _Normalized(default, body.warnings)

    if separator and prefix in ("lower", "upper"):
        # Do not change unresolved lookup syntax or assume how a lookup's
        # default separator/escape rules interact with an argument.
        if not body.resolved or ":-" in key:
            _warn(body.warnings, "unresolved_lookup")
            return _Normalized(original, body.warnings, False)
        if not key.isascii():
            _warn(body.warnings, "log4j_unicode_case_unsupported")
            return _Normalized(original, body.warnings, False)
        # Historical Java Lookups use the JVM default locale. Even ASCII I/i
        # can vary in Turkish/Azeri locales; this remains an ASCII-rule hint.
        if (prefix == "lower" and "I" in key) or (prefix == "upper" and "i" in key):
            _warn(body.warnings, "log4j_case_locale_unverified")
        budget.spend(len(key))
        return _Normalized(key.lower() if prefix == "lower" else key.upper(), body.warnings)

    if separator and prefix.lower() == "jndi":
        _warn(body.warnings, "jndi_lookup_not_executed")
        return _Normalized("${" + body.text + "}", body.warnings, False)

    # Never consult properties, infer absent values, or evaluate a plugin name
    # that is not explicitly supported. The entire original expression stays.
    return _Normalized(original, ["unresolved_lookup"], False)


def normalize_log4j(value: str) -> tuple[str, list[str]]:
    """Return static normalization candidates and deduplicated fixed warnings.

    Unchanged return values may still have warnings (plain JNDI, unresolved or
    escaped expressions). No warning means no supported marker was interpreted,
    not that the text is safe or free of other obfuscation. Resource/parse errors
    never return a partially transformed fragment or truncate the original.
    """
    if not isinstance(value, str):
        raise ValueError("log4j_value_must_be_text")
    if len(value) > MAX_LOG4J_CHARS:
        return value, ["log4j_input_too_long"]
    budget = _Budget()
    try:
        parts, _ = _parse(value, 0, 0, budget)
        normalized = _join(parts, value, budget)
    except _Stopped as stopped:
        return value, [str(stopped)]
    return normalized.text, normalized.warnings
