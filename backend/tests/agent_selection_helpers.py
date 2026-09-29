"""Adapt explicit synthetic model stubs to the requested internal wire contract.

Invalid legacy citations deliberately select c999, so correction/fail-closed
tests still exercise an invalid source instead of accidentally choosing one.
"""
import json

from app.agent.contracts import EvidenceSelectionOutput, EvidenceSelectionCorrectionOutput, EvidenceAssessmentOutput, SemanticAssessmentOutput
from app.agent.evidence import EvidenceSourceResolver


def model_output(output, kwargs):
    if output is None:
        return None
    contract = kwargs.get("output_model")
    if contract not in (EvidenceSelectionOutput, EvidenceSelectionCorrectionOutput, EvidenceAssessmentOutput, SemanticAssessmentOutput):
        return output
    document = json.loads(kwargs["user_input"])
    event = document["event"]
    sources = EvidenceSourceResolver(event["payload"], event)
    candidates = document["evidence_candidates"]["items"]

    def choose(item):
        if not sources.matches(item.field, item.excerpt):
            return "c999"
        options = [source for source in candidates if item.excerpt in source["excerpt"]]
        return min(options, key=lambda source: len(source["excerpt"]))["source_id"] if options else "c999"

    value = output.model_dump(mode="json")
    if contract is EvidenceSelectionCorrectionOutput:
        value["corrections"] = [{"index": item.index, "source_id": choose(item)} for item in output.corrections]
    else:
        value["evidence"] = [{"source_id": choose(item), "interpretation_ko": item.interpretation_ko} for item in output.evidence]
        if contract in (EvidenceAssessmentOutput, SemanticAssessmentOutput):
            for item in value["evidence"]:
                item["supports"] = "context"  # Explicit synthetic stub value, never inferred from verdict.
            value["decision_issue"] = None
        if contract is SemanticAssessmentOutput:
            # Explicit synthetic contract adapter, not production backfill.
            relation = value["signature_assessment"]["relation"]
            value["signature_assessment"] = {
                "version": "signature-assessment-v2", "relation": relation, "explanation_ko": None,
                "matched_points": ["가상 사례의 동작 유형이 관련됩니다."] if relation in {"exact", "partial"} else [],
                "mismatched_points": ["가상 사례의 대상 또는 유형이 다릅니다."] if relation in {"partial", "mismatch"} else [],
                "uncertainty_ko": "가상 사례에 탐지 설명이 없습니다." if relation == "unknown" else None,
            }
            value["analyst_checks"] = [{**check, "purpose": check.get("purpose") or (
                "decision_condition" if value["verdict"] == "inconclusive" else "impact_followup")} for check in value["analyst_checks"]]
            value["recommended_checks"] = []
    return contract.model_validate(value)
