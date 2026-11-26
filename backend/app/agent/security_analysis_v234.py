"""Use validated review state while preserving canonical evidence and v3."""
from . import security_analysis_v230 as legacy
from .security_analysis_v230 import canonical_evidence, decision_evidence_ids, guidance


def conflict_observed(call):
    if call and call.succeeded and call.telemetry.get("evidence_decision_review", {}).get("recovered"):
        return call.grounding_history[-1].get("assessment", {}).get("interpretation_conflict") is True
    return legacy.conflict_observed(call)


def role_result(call, role, evidence, issues):
    value = legacy.role_result(call, role, evidence, issues)
    if value is not None:
        value["interpretation_conflict"] = conflict_observed(call)
    return value


def observational_summary(primary, verifier, finalization, integrity_metadata=None):
    grounding = primary.telemetry.get("evidence_grounding", {})
    if grounding.get("downgraded_to_inconclusive") or grounding.get("correction_requires_review"):
        return primary.output.summary_ko
    return legacy.observational_summary(primary, verifier, finalization, integrity_metadata)
