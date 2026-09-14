"""Adapt explicit synthetic model stubs to the requested internal wire contract.

Invalid legacy citations deliberately select c999, so correction/fail-closed
tests still exercise an invalid source instead of accidentally choosing one.
"""
import json

from app.agent.contracts import EvidenceSelectionOutput, EvidenceSelectionCorrectionOutput, EvidenceAssessmentOutput
from app.agent.evidence import EvidenceSourceResolver


def model_output(output, kwargs):
    if output is None:
        return None
    contract = kwargs.get("output_model")
    if contract not in (EvidenceSelectionOutput, EvidenceSelectionCorrectionOutput, EvidenceAssessmentOutput):
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
        if contract is EvidenceAssessmentOutput:
            for item in value["evidence"]:
                item["supports"] = "context"  # Explicit synthetic stub value, never inferred from verdict.
            value["decision_issue"] = None
    return contract.model_validate(value)
