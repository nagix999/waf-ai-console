"""Admission-only comparison. Never imported by Agent input construction."""
from sqlalchemy import case
from ..models import Analysis

FIELDS = ("initial_verdict", "initial_probability", "initial_model_version")


def split_admission(event, *, allowed=False):
    """Strip only explicit admission fields; never search or rewrite payloads."""
    from pydantic import ValidationError
    from ..initial_assessment_schemas import InitialAssessmentInput
    from .analysis import AnalysisIngestError
    if isinstance(event.get("extra_fields"), dict) and set(FIELDS).intersection(event["extra_fields"]):
        raise AnalysisIngestError("initial_assessment_location_invalid", 422)
    values = {key: event[key] for key in FIELDS if key in event}
    if not values:
        return event, None
    if not allowed:
        raise AnalysisIngestError("initial_assessment_single_request_only", 422)
    try:
        metadata = InitialAssessmentInput.model_validate(values).model_dump(mode="json")
    except ValidationError as exc:
        field = next(iter(exc.errors(include_input=False)[0]["loc"]), None)
        code = {"initial_verdict": "initial_verdict_invalid",
                "initial_probability": "initial_probability_invalid",
                "initial_model_version": "initial_model_version_invalid"}.get(field, "initial_assessment_pair_required")
        raise AnalysisIngestError(code, 422) from None
    return {key: value for key, value in event.items() if key not in FIELDS}, metadata


def comparison(row):
    if row.initial_verdict is None:
        return "unavailable"
    if row.status != "completed" or row.verdict is None:
        return "pending"
    if row.verdict == "inconclusive":
        return "final_inconclusive"
    return "match" if row.verdict == row.initial_verdict else "different"


def describe(row):
    return {"verdict": row.initial_verdict, "probability": row.initial_probability,
            "model_version": row.initial_model_version, "comparison": comparison(row)}


def sql_comparison():
    return case((Analysis.initial_verdict.is_(None), "unavailable"),
                ((Analysis.status != "completed") | Analysis.verdict.is_(None), "pending"),
                (Analysis.verdict == "inconclusive", "final_inconclusive"),
                (Analysis.verdict == Analysis.initial_verdict, "match"), else_="different")


def check_duplicate(row, value):
    from .analysis import AnalysisIngestError
    if any(getattr(row, key) != (value or {}).get(key) for key in FIELDS):
        raise AnalysisIngestError("initial_assessment_conflict", 409)
