"""Admission-only comparison. Never imported by Agent input construction."""
from sqlalchemy import case
from ..models import Analysis

FIELDS = ("initial_verdict", "initial_probability", "initial_model_version")


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
