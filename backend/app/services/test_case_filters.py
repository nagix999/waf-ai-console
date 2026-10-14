"""Bounded, read-only predicates over Test metadata and the selected reference.

No raw input/result JSON hydration, arbitrary field paths, SQL or regex input.
Boolean groups have explicit parentheses and at most two levels.
"""
import json
import math

from sqlalchemy import and_, or_

from ..evaluation_schemas import OUTCOMES
from ..models import Analysis, TestRunItem
from .analysis import AnalysisIngestError
from .analysis_query import contains_text

VERDICTS = ("true_positive", "false_positive", "inconclusive")
TEXT_FIELDS = ("company_name", "signature", "waf_vendor", "event_name", "event_id", "src_ip", "dest_ip", "threat_category")
ENUM_FIELDS = {"verdict": VERDICTS, "reference_verdict": VERDICTS, "evaluation_outcome": OUTCOMES,
    "waf_action": ("D", "A"), "status": ("pending", "processing", "completed", "failed", "canceled"),
    "severity": ("CRITICAL", "HIGH", "MEDIUM", "LOW", "NONE", "UNKNOWN")}
NUMBER_FIELDS = {"src_port": (0, 65535, True), "dest_port": (0, 65535, True), "confidence_score": (0, 1, False)}
ITEM_FIELDS = ("difficulty", "test_category", "case_name")
EMPTY_OPS = {"is_empty", "is_not_empty"}
MAX_BYTES, MAX_RULES, MAX_GROUPS = 32768, 20, 6  # root + five condition groups


def case_condition(expression, relation):
    if expression is None:
        return None
    def invalid():
        raise AnalysisIngestError("invalid_test_case_filter", 422)
    try:
        if len(json.dumps(expression, ensure_ascii=False, allow_nan=False).encode("utf-8")) > MAX_BYTES:
            invalid()
    except (ValueError, TypeError, RecursionError, UnicodeError):
        invalid()
    counts = {"rules": 0, "groups": 0}
    columns = {name: getattr(Analysis, name) for name in (*TEXT_FIELDS, *NUMBER_FIELDS, *ENUM_FIELDS)
               if name not in {"reference_verdict", "evaluation_outcome"}}
    columns.update({name: getattr(TestRunItem, name) for name in ITEM_FIELDS})
    columns.update(reference_verdict=relation.c.reference_verdict, evaluation_outcome=relation.c.outcome)

    def build(node, depth=0):
        if not isinstance(node, dict):
            invalid()
        if "op" in node:
            counts["groups"] += 1
            if (set(node) != {"op", "rules"} or node["op"] not in ("and", "or") or depth >= 2 or
                    counts["groups"] > MAX_GROUPS or not isinstance(node["rules"], list) or not 1 <= len(node["rules"]) <= MAX_RULES):
                invalid()
            predicates = [build(rule, depth + 1) for rule in node["rules"]]
            return (and_ if node["op"] == "and" else or_)(*predicates)
        counts["rules"] += 1
        field, operator = node.get("field"), node.get("operator")
        if (counts["rules"] > MAX_RULES or not isinstance(field, str) or field not in columns or
                not isinstance(operator, str)):
            invalid()
        column = columns[field]
        if operator in EMPTY_OPS:
            if set(node) != {"field", "operator"}:
                invalid()
            empty = column.is_(None) if field in NUMBER_FIELDS else (column.is_(None) | (column == ""))
            return empty if operator == "is_empty" else ~empty
        if set(node) != {"field", "operator", "value"}:
            invalid()
        value = node["value"]
        if field in NUMBER_FIELDS:
            lo, hi, integer = NUMBER_FIELDS[field]
            if (type(value) not in (int, float) or not lo <= value <= hi or not math.isfinite(value) or
                    (integer and type(value) is not int) or operator not in ("eq", "ne", "gt", "gte", "lt", "lte")):
                invalid()
        else:
            if not isinstance(value, str) or not 1 <= len(value) <= 4096:
                invalid()
            if field in ENUM_FIELDS:
                if value not in ENUM_FIELDS[field] or operator not in ("eq", "ne"):
                    invalid()
            elif operator not in ("eq", "ne", "contains", "not_contains", "starts_with", "ends_with"):
                invalid()
        if operator in ("contains", "not_contains"):
            condition = contains_text(column, value)
            return condition if operator == "contains" else ~condition
        if operator in ("starts_with", "ends_with"):
            # SQLAlchemy escapes user-supplied wildcard characters too.
            return column.istartswith(value, autoescape=True) if operator == "starts_with" else column.iendswith(value, autoescape=True)
        return {"eq": lambda: column == value, "ne": lambda: column != value,
                "gt": lambda: column > value, "gte": lambda: column >= value,
                "lt": lambda: column < value, "lte": lambda: column <= value}[operator]()

    if not isinstance(expression, dict) or "op" not in expression:
        invalid()
    return build(expression)
