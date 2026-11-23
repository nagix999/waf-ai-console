"""Versioned wire-only schema and payload-free corrective constraints.

The Pydantic domain model remains the final validator. No model output values
are read, inferred or repaired here; every hint comes from its trusted schema.
"""

from copy import deepcopy
import json
from typing import Any, Mapping

from ..services.provider_options import strict_json_schema


VERSION = "schema-guided-output-v1"
_SCHEMA_MAPS = frozenset({"properties", "$defs", "definitions", "patternProperties"})
_SCHEMA_LISTS = frozenset({"anyOf", "allOf", "oneOf", "prefixItems"})
_SCHEMA_CHILDREN = frozenset({"items", "additionalProperties", "not", "if", "then", "else", "contains", "propertyNames"})
_TYPES = frozenset({"array", "boolean", "integer", "null", "number", "object", "string"})
_HINT_TYPES = frozenset({
    "enum", "literal_error", "missing", "string_type", "bool_type", "bool_parsing",
    "int_type", "int_parsing", "int_from_float", "float_type", "float_parsing",
    "list_type", "dict_type", "model_type", "greater_than", "greater_than_equal",
    "less_than", "less_than_equal", "string_too_short", "string_too_long", "too_short", "too_long",
})


def compact_strict_schema(schema: Mapping[str, Any]) -> dict[str, Any]:
    """Require explicit wire fields and remove only nested title annotations.

    Property/definition names, descriptions, refs, enum values, null branches
    and every validation constraint are preserved. The root title remains the
    stable response_format name. Neither the input nor saved schemas mutate.
    """
    def strip_titles(node: Any, *, root: bool = False) -> Any:
        if not isinstance(node, Mapping):
            return deepcopy(node)
        result: dict[str, Any] = {}
        for key, value in node.items():
            if key == "title" and not root:
                continue
            if key in _SCHEMA_MAPS and isinstance(value, Mapping):
                result[key] = {name: strip_titles(child) for name, child in value.items()}
            elif key in _SCHEMA_LISTS and isinstance(value, list):
                result[key] = [strip_titles(child) for child in value]
            elif key in _SCHEMA_CHILDREN:
                result[key] = strip_titles(value)
            else:
                result[key] = deepcopy(value)
        return result

    return strip_titles(strict_json_schema(schema), root=True)


def _branches(root: Mapping[str, Any], node: Any, depth: int = 0) -> list[Mapping[str, Any]]:
    if not isinstance(node, Mapping) or depth > 12:
        return []
    result: list[Mapping[str, Any]] = [node]
    reference = node.get("$ref")
    if isinstance(reference, str) and reference.startswith("#/"):
        target: Any = root
        for part in reference[2:].split("/"):
            target = target.get(part.replace("~1", "/").replace("~0", "~")) if isinstance(target, Mapping) else None
        result.extend(_branches(root, target, depth + 1))
    for keyword in ("anyOf", "allOf", "oneOf"):
        values = node.get(keyword)
        if isinstance(values, list):
            for child in values[:16]:
                result.extend(_branches(root, child, depth + 1))
    return result


def _field_nodes(schema: Mapping[str, Any], path: Any) -> list[Mapping[str, Any]]:
    if not isinstance(path, str) or not path or len(path) > 256:
        return []
    parts = path.split(".")
    if len(parts) > 16 or any(part in {"<unknown>", ""} for part in parts):
        return []
    nodes: list[Mapping[str, Any]] = [schema]
    if parts == ["$"]:
        return _branches(schema, schema)
    for part in parts:
        children: list[Mapping[str, Any]] = []
        for node in nodes:
            for branch in _branches(schema, node):
                properties = branch.get("properties")
                child = branch.get("items") if part == "[]" else properties.get(part) if isinstance(properties, Mapping) else None
                if isinstance(child, Mapping):
                    children.append(child)
        nodes = children
        if not nodes:
            return []
    return [branch for node in nodes for branch in _branches(schema, node)]


def constraint_hints(schema: Mapping[str, Any], issues: list[dict[str, str]]) -> list[str]:
    """Return bounded field constraints; never echo exception text or values."""
    hints: list[str] = []
    for issue in issues[:12]:
        if not isinstance(issue, Mapping) or issue.get("type") not in _HINT_TYPES:
            continue
        path = issue.get("field")
        nodes = _field_nodes(schema, path)
        if not nodes:
            continue
        constraints: list[str] = []
        types = list(dict.fromkeys(node["type"] for node in nodes
            if isinstance(node.get("type"), str) and node["type"] in _TYPES))
        if types:
            constraints.append("타입 " + "/".join(types))
        allowed: list[Any] = []
        for node in nodes:
            values = node.get("enum", [node["const"]] if "const" in node else [])
            if isinstance(values, list):
                for value in values[:16]:
                    if isinstance(value, (str, int, float, bool)) or value is None:
                        if value not in allowed:
                            allowed.append(value)
        if allowed:
            constraints.append("허용값 " + ", ".join(json.dumps(value, ensure_ascii=False, allow_nan=False) for value in allowed))
        for keyword, label in (
            ("minimum", "최솟값"), ("maximum", "최댓값"),
            ("exclusiveMinimum", "초과"), ("exclusiveMaximum", "미만"),
            ("minLength", "최소 글자 수"), ("maxLength", "최대 글자 수"),
            ("minItems", "최소 항목 수"), ("maxItems", "최대 항목 수"),
        ):
            values = list(dict.fromkeys(node[keyword] for node in nodes
                if keyword in node and type(node[keyword]) in {int, float}))
            if values:
                constraints.append(label + " " + "/".join(str(value) for value in values))
        if constraints:
            hint = f"- {path}: " + "; ".join(constraints)
            if len(hint) <= 640 and hint not in hints:
                hints.append(hint)
    return hints
