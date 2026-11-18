"""Reuse lossless edge repair without discarding the private capture assessment."""
from copy import deepcopy

from pydantic import ValidationError

from .contracts_v224 import SecurityAnalysisOutputV224
from .relation_recovery import (
    NORMALIZATION_CODES, recover_relation_duplicates as _recover_relations,
)


def recover_relation_duplicates(value):
    if type(value) is not dict or "capture_assessment" not in value:
        return deepcopy(value), []
    original = deepcopy(value)
    legacy = {key: item for key, item in original.items() if key != "capture_assessment"}
    repaired, codes = _recover_relations(legacy)
    if not codes:
        return original, []
    repaired["capture_assessment"] = original["capture_assessment"]
    try:
        SecurityAnalysisOutputV224.model_validate(repaired)
    except (ValidationError, ValueError, TypeError, RecursionError):
        return original, []
    return repaired, codes
