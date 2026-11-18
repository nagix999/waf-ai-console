"""Workbench-only match explanations using the unchanged, pinned rule engine.

Do not add this code to runtime selection or modify its frozen artifact to add
diagnostics. Results contain condition positions and fixed codes, never values.
"""
from pydantic import ValidationError

from ..parser_rule_schemas import RuleProposalV1
from .parser_rule_engine import MAX_SOURCE_BYTES, ParserRuleError, _Budget, _Context, _matches
from .parser_workbench_diagnostics import RULE_CODES

_FALSE_CODES = {"starts_with": "parser_prefix_mismatch", "regex_match": "parser_regex_no_match",
                "json_equals": "parser_json_value_mismatch", "json_string_exists": "parser_json_string_missing"}


def diagnose_rule_match(raw, definition):
    """Explain up to eight conditions without changing all/any or parser output."""
    if not isinstance(raw, str):
        raise ParserRuleError("parser_source_invalid")
    try:
        if len(raw) > MAX_SOURCE_BYTES or len(raw.encode("utf-8")) > MAX_SOURCE_BYTES:
            raise ParserRuleError("parser_source_invalid")
        proposal = RuleProposalV1.model_validate(definition).model_dump(mode="json")
    except (ValidationError, UnicodeError):
        raise ParserRuleError("parser_rule_bundle_invalid") from None
    mode, predicates = next(iter(proposal["match"].items()))
    context, result = _Context(raw, _Budget()), []
    for index, predicate in enumerate(predicates):
        matched = False
        try:
            context.budget.check()
            matched = _matches(predicate, context)
            code = "ok" if matched else _FALSE_CODES.get(predicate["kind"], "parser_match_condition_failed")
        except ParserRuleError as exc:
            code = exc.code if exc.code in RULE_CODES else "parser_match_condition_failed"
        except Exception:
            # An explanatory pass is not allowed to change the job outcome or
            # expose an implementation exception's text.
            code = "parser_match_condition_failed"
        result.append({"condition_index": index, "field": f"match.{mode}.{index}",
                       "kind": predicate["kind"], "matched": matched, "code": code})
    return result
