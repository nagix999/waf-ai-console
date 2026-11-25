"""Typed, source-bound core responses, separate from immutable stored results.

Only the response builder can supply a projection proof. A raw v3 dictionary
never obtains relaxed provenance checks merely by claiming to be a core view.
The representative retains its actual writer; issue authorship is unchanged.
"""
from copy import deepcopy
from dataclasses import dataclass
import hashlib
import hmac
import json

from pydantic import PrivateAttr, ValidationInfo, model_validator

from .result_contracts import AnalysisResultV3, validate_new_result


VERSION = "core-evidence-v2"
_CONTEXT_KEY = "_source_bound_core_projection"


def _digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
        allow_nan=False, separators=(",", ":")).encode()).hexdigest()


@dataclass(frozen=True)
class _ProjectionProof:
    expected_hash: str


class CoreAnalysisResultV3(AnalysisResultV3):
    """The same v3 wire fields; validated equivalence may cross issue authors.

    The private seal permits FastAPI/Pydantic to revalidate an already checked
    instance, not an unproven serialized dictionary or a modified model_copy.
    """
    _core_projection_seal: str | None = PrivateAttr(default=None)

    @model_validator(mode="after")
    def references_are_closed(self, info: ValidationInfo):
        # Override the stored result's after-validator, with the same name.
        # Its complete original was strictly validated before projection. The
        # proof binds the exact response whose only edits are checked below.
        content_hash = _digest(self.model_dump(mode="json", exclude_unset=True))
        proof = (info.context or {}).get(_CONTEXT_KEY)
        if isinstance(proof, _ProjectionProof) and hmac.compare_digest(proof.expected_hash, content_hash):
            self._core_projection_seal = content_hash
            return self
        if self._core_projection_seal and hmac.compare_digest(self._core_projection_seal, content_hash):
            return self
        raise ValueError("core_response_projection_unproven")

    @model_validator(mode="wrap")
    @classmethod
    def require_projection_binding(cls, value, handler, info: ValidationInfo):
        # A wrap validator also fences instances that Pydantic would otherwise
        # skip before-validation for. Inherited after-validators must never see
        # an unproven/untyped model_construct or a changed model_copy.
        if isinstance(value, cls):
            seal = value._core_projection_seal
            if not seal:
                raise ValueError("core_response_projection_unproven")
            document = value.model_dump(mode="json", exclude_unset=True)
            if not hmac.compare_digest(seal, _digest(document)):
                raise ValueError("core_response_projection_unproven")
            return cls.model_validate(document, context={_CONTEXT_KEY: _ProjectionProof(seal)})
        if not isinstance((info.context or {}).get(_CONTEXT_KEY), _ProjectionProof):
            raise ValueError("core_response_projection_unproven")
        return handler(value)


def revalidate_core_result(value):
    if not isinstance(value, CoreAnalysisResultV3):
        raise ValueError("core_response_projection_unproven")
    return CoreAnalysisResultV3.model_validate(value)


def _physical_source(item):
    field = item["field"].strip().removeprefix("event.")
    if field == "raw_payload":
        field = "payload"
    spans = tuple(sorted({(span["start"], span["end"]) for span in item.get("source_spans", [])}))
    return field, item["excerpt"], spans


def _closed_references(result):
    indexed = {item["evidence_id"]: item for item in result["evidence"]}
    if len(indexed) != len(result["evidence"]):
        raise ValueError("core_response_reference_invalid")
    roles = [("primary", result["primary"]), ("verifier", result["verifier"].get("output"))]
    refs = [result["policy"]["decision_evidence_ids"],
        *(issue["evidence_ids"] for issue in result["decision_issues"])]
    for name, role in roles:
        contributed = {identifier for identifier, item in indexed.items()
            if any(origin["role"] == name for origin in item["origins"])}
        if contributed != (set(role["evidence_ids"]) if role else set()):
            raise ValueError("core_response_origin_invalid")
        if role:
            refs.extend([role["evidence_ids"], *(issue["evidence_ids"] for issue in role["decision_issues"])])
    if any(len(values) != len(set(values)) or not set(values) <= indexed.keys() for values in refs):
        raise ValueError("core_response_reference_invalid")
    if result["verdict"] != "inconclusive":
        direction = {"true_positive": "attack_request", "false_positive": "normal_request"}[result["verdict"]]
        if not any(indexed[key]["supports"] == direction for key in result["policy"]["decision_evidence_ids"]):
            raise ValueError("core_response_policy_anchor_missing")


def validate_core_projection(original, *, mapping, selected_ids, plan, relations):
    """Build, rather than trust, a response from an original and a saved plan.

    Mapping integrity is deterministic; semantic equivalence remains the
    Editor's judgment. No count/role/confidence heuristic creates a meaning
    group. The plan and source binding are checked again at this boundary.
    """
    from .services.evidence_display_plan_v2 import validate_display_plan
    from .agent.evidence_relations import validate_evidence_relations
    canonical = validate_new_result(deepcopy(original))
    links = validate_evidence_relations(relations, canonical["evidence"])
    checked = validate_display_plan(canonical, links, plan)
    if checked["status"] not in {"completed", "skipped"}:
        raise ValueError("core_response_plan_invalid")
    indexed = {item["evidence_id"]: item for item in canonical["evidence"]}
    if (not isinstance(mapping, dict) or set(mapping) != indexed.keys()
            or len(selected_ids) != len(set(selected_ids)) or not set(selected_ids) <= indexed.keys()):
        raise ValueError("core_response_mapping_invalid")
    selected = set(selected_ids)
    required_directions = {item["supports"] for item in canonical["evidence"] if item["supports"] != "context"}
    if not required_directions <= {indexed[key]["supports"] for key in selected}:
        raise ValueError("core_response_counter_evidence_missing")
    policy_ids = set(canonical["policy"]["decision_evidence_ids"])
    for group in checked["groups"]:
        members = group["member_ids"]
        targets = {mapping[identifier] for identifier in members}
        if len(targets) != 1:
            raise ValueError("core_response_mapping_invalid")
        target = next(iter(targets))
        if target not in members or mapping.get(target) != target:
            raise ValueError("core_response_mapping_invalid")
        if len(members) > 1:
            source = _physical_source(indexed[target])
            if not source[2] or any(_physical_source(indexed[key]) != source
                    or indexed[key]["supports"] != indexed[target]["supports"] for key in members):
                raise ValueError("core_response_mapping_invalid")
            if checked["status"] == "skipped" and any(indexed[key]["interpretation_ko"] != indexed[target]["interpretation_ko"]
                    for key in members):
                raise ValueError("core_response_mapping_invalid")
        if selected.intersection(members) and selected.intersection(members) != {target}:
            raise ValueError("core_response_mapping_invalid")
        if target in selected and policy_ids.intersection(members) and target not in policy_ids:
            raise ValueError("core_response_policy_anchor_changed")
    for relation in links["groups"]:
        if mapping[relation["representative_evidence_id"]] in selected:
            if not {mapping[key] for key in relation["related_evidence_ids"]} <= selected:
                raise ValueError("core_response_dependency_missing")

    projected = deepcopy(canonical)
    projected["evidence"] = [deepcopy(indexed[key]) for key in selected_ids]
    projected["policy"]["decision_evidence_ids"] = [key for key in canonical["policy"]["decision_evidence_ids"] if key in selected]

    def issues(values):
        output = deepcopy(values)
        for issue in output:
            issue["evidence_ids"] = list(dict.fromkeys(mapping[key] for key in issue["evidence_ids"]))
        return output

    projected["decision_issues"] = issues(canonical["decision_issues"])
    for name, role in (("primary", projected["primary"]), ("verifier", projected["verifier"].get("output"))):
        if role is not None:
            source = canonical["primary"] if name == "primary" else canonical["verifier"]["output"]
            role["evidence_ids"] = [key for key in source["evidence_ids"] if key in selected]
            role["decision_issues"] = issues(source["decision_issues"])
    projected.pop("evidence_presentation", None)
    _closed_references(projected)
    proof = _ProjectionProof(_digest(projected))
    return CoreAnalysisResultV3.model_validate(projected, context={_CONTEXT_KEY: proof})


def core_legacy_result_projection(value):
    """Explicit v2 conversion of an already proven core response, never stored."""
    checked = revalidate_core_result(value)
    data = checked.model_dump(mode="json", exclude_unset=True)
    indexed = {item["evidence_id"]: item for item in data["evidence"]}

    def core(record, ids):
        return {**{key: deepcopy(record[key]) for key in ("verdict", "confidence_score", "summary_ko",
                    "threat_analysis", "signature_assessment", "input_truncated")},
                "evidence": [{key: indexed[identifier][key] for key in ("field", "excerpt", "interpretation_ko")}
                             for identifier in ids[:5]],
                "recommended_checks": [], "analyst_checks": [], "conflicting_evidence": [],
                "tuning_recommendation": {"recommended": False, "scope": None, "proposal_ko": None,
                                          "risk_ko": None, "validation_ko": None}}

    projected = core(data, data["policy"]["decision_evidence_ids"])
    projected.update(schema_version="waf-analysis-v2", diagnostics=data["diagnostics"], agent=data["agent"],
        policy={key: val for key, val in data["policy"].items() if key not in {
            "decision_evidence_ids", "verifier_policy_version", "retired_verifier_triggers"}},
        analyst_guidance={**data["analyst_guidance"], "checks": []}, primary=core(data["primary"], data["primary"]["evidence_ids"]),
        verifier={**data["verifier"], "output": core(data["verifier"]["output"], data["verifier"]["output"]["evidence_ids"])
                  if data["verifier"].get("output") else None})
    directions = {"attack_request": "true_positive", "normal_request": "false_positive", "context": "context"}
    projected["analyst_assessment"] = {"version": "analyst-assessment-v1",
        "evidence": [{**item, "supports": directions[item["supports"]]} for item in data["evidence"]],
        "decision_issues": data["decision_issues"], "omitted_issue_count": data["omitted_issue_count"]}
    return projected
