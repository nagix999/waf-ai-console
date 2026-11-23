"""Canonical aliases preserve all source origins without creating fake links."""
from copy import deepcopy

import pytest

from app.agent.contracts import WAFAnalysisOutput
from app.agent.contracts_v228 import SecurityAnalysisOutputV228
from app.agent.evidence_relations_v228 import EvidenceRelationsError, build_evidence_relations
from app.agent.executor import AgentCallResult
from app.agent.security_analysis import canonical_evidence
from test_flat_evidence_v228 import wire


def item(identifier, source, text, supports="context", related=()):
    return {"evidence_id": identifier, "source_id": source, "interpretation_ko": text,
            "supports": supports, "related_evidence_ids": list(related)}


def call(items):
    value = wire()
    value.update(evidence=items, verdict="inconclusive", decision_issue=None)
    value["threat_analysis"]["severity"] = "UNKNOWN"
    raw = SecurityAnalysisOutputV228.model_validate(value)
    expanded = raw.to_selection(input_truncated=False)
    assessment = expanded.legacy_bridge().model_dump(mode="json")
    resolved = {key: value for key, value in assessment.items() if key != "decision_issue"}
    resolved["evidence"] = [{"field": "payload", "excerpt": "synthetic request",
                            "interpretation_ko": entry["interpretation_ko"]}
                           for entry in assessment["evidence"]]
    output = WAFAnalysisOutput.model_validate(resolved)
    history = {"output": raw.model_dump(mode="json"),
               "expanded_output": expanded.model_dump(mode="json"),
               "assessment": assessment, "resolved_output": output.model_dump(mode="json"),
               "source_resolutions": [{"index": index, "source_id": entry["source_id"],
                                       "start": int(entry["source_id"][1:]) * 100,
                                       "end": int(entry["source_id"][1:]) * 100 + len("synthetic request")}
                                      for index, entry in enumerate(assessment["evidence"])]}
    return AgentCallResult(output, None, None, "completed", None, None, {}, [history])


def document(primary, verifier=None):
    evidence, _, _ = canonical_evidence(primary, verifier)
    before = deepcopy((primary.grounding_history, verifier.grounding_history if verifier else None))
    result = build_evidence_relations(primary, verifier, evidence=evidence)
    assert (primary.grounding_history, verifier.grounding_history if verifier else None) == before
    return evidence, result


def test_exact_canonical_alias_self_edge_has_no_association_and_preserves_origins():
    primary = call([
        item("e1", "c1", "동일한 원문의 공격 의미입니다.", "attack_request", ["e2"]),
        item("e2", "c1", "동일한 원문의 공격 의미입니다.", "attack_request"),
    ])
    evidence, result = document(primary)
    assert len(evidence) == 1
    assert [origin["index"] for origin in evidence[0]["origins"]] == [0, 1]
    assert result == {"version": "evidence-relations-v1", "groups": []}


def test_related_canonical_aliases_keep_one_edge_and_both_source_origins():
    primary = call([
        item("e1", "c1", "보조 입력의 문맥과 결합된 공격 해석입니다.", "attack_request", ["e2", "e3"]),
        item("e2", "c2", "입력이 전달되는 공통 문맥입니다."),
        item("e3", "c2", "입력이 전달되는 공통 문맥입니다."),
    ])
    evidence, result = document(primary)
    assert len(evidence) == 2
    assert [origin["index"] for origin in evidence[1]["origins"]] == [1, 2]
    assert result["groups"] == [{"role": "primary", "representative_evidence_id": "e1",
                                 "related_evidence_ids": ["e2"]}]


def test_canonical_representative_aliases_union_every_dependency():
    primary = call([
        item("e1", "c1", "여러 원문을 연결한 공격 해석입니다.", "attack_request", ["e3"]),
        item("e2", "c4", "여러 원문을 연결한 공격 해석입니다.", "attack_request", ["e4"]),
        item("e3", "c2", "첫 번째 관련 입력의 문맥입니다."),
        item("e4", "c3", "두 번째 관련 입력의 문맥입니다."),
    ])
    evidence, result = document(primary)
    assert len(evidence) == 3
    assert result["groups"] == [{"role": "primary", "representative_evidence_id": "e1",
                                 "related_evidence_ids": ["e2", "e3"]}]
    assert {origin["index"] for origin in evidence[0]["origins"]} == {0, 1}
    assert evidence[0]["source_spans"] == [{"start": 100, "end": 117}, {"start": 400, "end": 417}]


def test_same_source_with_distinct_interpretations_stays_distinct_and_linked():
    primary = call([
        item("e1", "c1", "동작과 값을 연결하면 공격 시도를 뒷받침합니다.", "attack_request", ["e2"]),
        item("e2", "c1", "같은 원문의 입력 위치가 해석에 필요한 문맥을 제공합니다."),
    ])
    evidence, result = document(primary)
    assert len(evidence) == 2
    assert evidence[0]["interpretation_ko"] != evidence[1]["interpretation_ko"]
    assert result["groups"] == [{"role": "primary", "representative_evidence_id": "e1",
                                 "related_evidence_ids": ["e2"]}]


def test_missing_original_source_is_not_hidden_by_canonical_alias_normalization():
    primary = call([
        item("e1", "c1", "원문의 공격 의미입니다.", "attack_request", ["e2", "e3"]),
        item("e2", "c1", "원문의 공격 의미입니다.", "attack_request"),
        item("e3", "c2", "관련 원문의 별도 문맥입니다."),
    ])
    evidence, _, _ = canonical_evidence(primary, None)
    primary.grounding_history[0]["source_resolutions"].pop()
    before = deepcopy(primary.grounding_history)
    with pytest.raises(EvidenceRelationsError, match="evidence_relation_source_missing"):
        build_evidence_relations(primary, None, evidence=evidence)
    assert primary.grounding_history == before


def test_same_canonical_representative_keeps_roles_and_their_own_dependencies():
    primary = call([
        item("e1", "c1", "동일한 요청의 공격 의미입니다.", "attack_request", ["e2"]),
        item("e2", "c2", "첫 번째 역할이 사용한 입력 문맥입니다."),
    ])
    verifier = call([
        item("e3", "c1", "동일한 요청의 공격 의미입니다.", "attack_request", ["e4"]),
        item("e4", "c3", "두 번째 역할이 사용한 입력 문맥입니다."),
    ])
    evidence, result = document(primary, verifier)
    assert len(evidence) == 3
    assert result["groups"] == [
        {"role": "primary", "representative_evidence_id": "e1", "related_evidence_ids": ["e2"]},
        {"role": "verifier", "representative_evidence_id": "e1", "related_evidence_ids": ["e3"]},
    ]
