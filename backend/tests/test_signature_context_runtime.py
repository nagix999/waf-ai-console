"""Synthetic, offline checks for pinned reference descriptions and captures."""
from copy import deepcopy
from dataclasses import replace
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app import worker_v223
from app.agent import (
    evidence_pipeline_v222, evidence_pipeline_v223,
    execution_policy_v222, execution_policy_v223,
    input_builder_v223,
    legacy_prompts_v222_capture, prompts,
)
from app.agent.contracts import EvidenceSelectionCorrectionOutput, SecurityAnalysisOutputV220
from app.agent.evidence_source_policy_v223 import (
    catalog_for_source_policy, is_reference_source_field, signature_support_issues,
)
from app.agent.executor import AgentCallResult
from app.agent.policy import VerifierPolicyContext
from app.models import AgentRun, Analysis, VLLMProfile
from app.services.crypto import CryptoService
from app.services.http_parser import parse_http_payload
from app.services.signature_explanations import (
    SignatureExplanationError, _seal, create_version, default_snapshot, event_snapshot,
    snapshot_for_version,
)
from app.services.prompt_policies import get_active_policy
from app.services.prompt_snapshots import _build_snapshot
from test_evidence_relations import wire
from test_prompt_evidence_semantics_preservation import ENGINE_SHA256


NEW = "waf-system-v2.23"
DESCRIPTION = "특정 기능의 입력 처리 취약점을 탐지합니다. 탐지 사실과 실제 요청 내용을 구분하세요."
RAW = "POST /service HTTP/1.1\r\nHost: synthetic.invalid\r\nContent-Length: 10\r\n\r\nvalue=test"


@pytest.fixture
def crypto():
    return CryptoService("AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=", "synthetic-only")


def catalog():
    entries = [{"waf_vendor": "Synthetic", "signature": "Synthetic detection",
                "signature_explanation": DESCRIPTION, "reference_source": "Synthetic reference"}]
    value = default_snapshot("candidate")
    value.update(version_id="synthetic-catalog-version", version_no=1, entry_count=1,
                 content_hash=hashlib.sha256(json.dumps(entries, ensure_ascii=False,
                     sort_keys=True, separators=(",", ":")).encode()).hexdigest(), entries=entries)
    value.pop("snapshot_hash")
    return _seal(value)


def analysis_row(event_payload, crypto, *, value=None):
    data = {**event_payload, "waf_vendor": "Synthetic", "signature": "Synthetic detection",
            "source_system": "synthetic-source", "extra_fields": {"original_extension": "kept"},
            "signature_explanation_version_id": None,
            "signature_explanation_snapshot_ciphertext": None}
    snapshot = event_snapshot(catalog() if value is None else value, data["waf_vendor"], data["signature"])
    data.update(signature_explanation_version_id=snapshot["version_id"],
                signature_explanation_snapshot_ciphertext=crypto.encrypt_text(json.dumps(snapshot)))
    return SimpleNamespace(**data)


def test_previous_prompt_source_is_byte_preserved():
    assert hashlib.sha256(Path(legacy_prompts_v222_capture.__file__).read_bytes()).hexdigest() == (
        "8e56da65635bf1ce1d238bb37c5267929aab4857b0e53626c70a7a96b66666bc")
    assert legacy_prompts_v222_capture.FIXED_RULES_VERSION == "waf-system-v2.22"
    assert prompts.FIXED_RULES_VERSION == NEW
    assert prompts.PRIMARY_ROLE == legacy_prompts_v222_capture.PRIMARY_ROLE
    assert prompts.VERIFIER_ROLE == legacy_prompts_v222_capture.VERIFIER_ROLE


@pytest.mark.parametrize("filename,expected", ENGINE_SHA256.items())
def test_all_forty_historical_engine_files_are_unchanged(filename, expected):
    path = Path(evidence_pipeline_v222.__file__).parent / filename
    assert hashlib.sha256(path.read_bytes()).hexdigest() == expected


def test_manifest_is_additive_and_new_source_policy_is_pinned():
    old, new = evidence_pipeline_v222.pipeline_artifact(), evidence_pipeline_v223.pipeline_artifact()
    assert {key: new["files"][key] for key in old["files"]} == old["files"]
    assert new["version"] == "evidence-pipeline-v4"
    assert new["source_catalog_version"] == "evidence-selection-v5"
    assert {"evidence_source_policy_v223.py", "../services/signature_explanations.py",
            "../services/worker_coordinator_v223.py", "../worker_entrypoint_v223.py"} <= new["files"].keys()
    assert evidence_pipeline_v223.validate_pipeline_snapshot(old, "waf-system-v2.22") == old
    assert evidence_pipeline_v223.validate_pipeline_snapshot(new, NEW) == new
    with pytest.raises(ValueError, match="evidence_pipeline_snapshot_invalid"):
        evidence_pipeline_v223.validate_pipeline_snapshot(old, NEW)


@pytest.mark.parametrize("field", ["signature", "event.signature", "signature_explanation",
                                  "event.signature_explanation", "extra_fields.signature_explanation"])
def test_only_canonical_reference_sources_are_context_only(field):
    original = {"version": "synthetic-original", "items": [{"source_id": "c1", "field": field,
        "excerpt": DESCRIPTION, "start": None, "end": None}]}
    original_copy = deepcopy(original)
    result = catalog_for_source_policy(original, fixed_rules_version=NEW)
    assert is_reference_source_field(field)
    assert result["items"][0]["allowed_supports"] == ["context"]
    assert original == original_copy
    for supports in ("attack_request", "normal_request"):
        selected = SimpleNamespace(evidence=[SimpleNamespace(source_id="c1", supports=supports)])
        assert signature_support_issues(selected, result, fixed_rules_version=NEW) == [
            {"index": 0, "reason": "signature_support_not_allowed"}]
    selected = SimpleNamespace(evidence=[SimpleNamespace(source_id="c1", supports="context")])
    assert signature_support_issues(selected, result, fixed_rules_version=NEW) == []


@pytest.mark.parametrize("field", ["payload", "payload.body", "payload.query.signature_explanation",
                                  "extra_fields.nested.signature_explanation", "signature_note"])
def test_same_name_inside_request_content_is_not_inferred_to_be_server_reference(field):
    assert not is_reference_source_field(field)


def test_enrichment_uses_pinned_definition_without_mutating_event(event_payload, crypto):
    row = analysis_row(event_payload, crypto)
    original = deepcopy(row.extra_fields)
    event, metadata = worker_v223._inference_event(row, crypto)
    assert event["signature_explanation"] == DESCRIPTION
    assert event["extra_fields"] == original == row.extra_fields
    assert "signature_explanation" not in row.extra_fields
    assert not hasattr(row, "signature_explanation")
    assert metadata["matched"] is True
    assert metadata["version_id"] == "synthetic-catalog-version"
    assert DESCRIPTION not in json.dumps(metadata, ensure_ascii=False)


def test_explicit_empty_snapshot_adds_no_field(event_payload, crypto):
    row = analysis_row(event_payload, crypto, value=default_snapshot("candidate"))
    event, metadata = worker_v223._inference_event(row, crypto)
    assert "signature_explanation" not in event
    assert metadata["matched"] is False


@pytest.mark.parametrize("kind", ["missing", "corrupt", "wrong_match"])
def test_new_execution_never_falls_back_to_current_or_missing_reference(event_payload, crypto, kind):
    row = analysis_row(event_payload, crypto)
    if kind == "missing":
        row.signature_explanation_version_id = None
        row.signature_explanation_snapshot_ciphertext = None
    elif kind == "corrupt":
        row.signature_explanation_snapshot_ciphertext = "synthetic-invalid-ciphertext"
    else:
        row.signature = "different detection"
    with pytest.raises(SignatureExplanationError):
        worker_v223._inference_event(row, crypto)


def test_explanation_is_top_level_in_input_and_cannot_anchor_a_request_verdict(event_payload):
    event = {**event_payload, "signature_explanation": DESCRIPTION}
    original = deepcopy(event)
    built = input_builder_v223.build_agent_input(event, RAW, parse_http_payload(RAW), 32000, 3072,
        evidence_selection=True, fixed_rules_version=NEW)
    value = json.loads(built.text)
    assert event == original
    assert value["event"]["signature_explanation"] == DESCRIPTION
    refs = [item for item in value["evidence_candidates"]["items"] if item["field"] == "signature_explanation"]
    assert len(refs) == 1 and refs[0]["allowed_supports"] == ["context"]
    assert value["evidence_candidates"]["version"] == "evidence-selection-v5"
    assert not built.input_truncated


@pytest.mark.parametrize("window", [5200, 8192, 32000])
def test_description_and_policy_flags_are_counted_in_actual_serialized_budget(event_payload, window):
    event = {**event_payload, "signature_explanation": DESCRIPTION * 20}
    built = input_builder_v223.build_agent_input(event, RAW * 100, parse_http_payload(RAW * 100),
        window, 0, evidence_selection=True, fixed_rules_version=NEW)
    assert len(built.text) <= built.estimated_input_token_budget * 3
    assert built.original_payload_chars == len(RAW * 100)


@pytest.mark.parametrize("status", ["success", "partial", "failed"])
def test_parser_status_only_remains_excluded_and_other_verifier_reasons_are_unchanged(status):
    primary = SecurityAnalysisOutputV220.model_validate(wire()).legacy_bridge()
    for truncated in (False, True):
        context = VerifierPolicyContext("D", status, truncated, interpretation_conflict=False,
            fixed_rules_version="waf-system-v2.22")
        expected = execution_policy_v222.verifier_reasons(primary, context)
        assert execution_policy_v223.verifier_reasons(primary, replace(context, fixed_rules_version=NEW)) == expected
        assert "parser_incomplete" not in expected


def test_shared_prompt_separates_capture_loss_from_attack_observation():
    text = prompts.FIXED_INSTRUCTIONS
    assert "WAF가 검사한 전체 요청과 저장한 payload는 다를 수 있다" in text
    assert "장비 저장 한도/절단은 제공된 정보로만 판단" in text
    assert "완전한 공격 코드/취약 제품 사용/침해 성공 확인은 필수가 아니다" in text
    assert "일부 원문이 누락되어도 판단할 수 있다" in text
    assert "시그니처/패딩/절단만으로 자동 확정" in text
    assert "signature_explanation" in text
    assert "2048" not in text and "React2Shell" not in text and "CVE-2025-55182" not in text
    primary, verifier = prompts.build_role_instructions("synthetic instructions")
    assert primary.startswith(text) and verifier.startswith(text)


def test_v223_reuses_exact_v222_structured_output_executor():
    from app.agent.executor_v222 import execute_structured_agent
    assert worker_v223.execute_structured_agent is execute_structured_agent


@pytest.mark.parametrize("directional_reference", [False, True])
def test_grounding_accepts_reference_context_but_rejects_directional_use_after_correction(
        event_payload, crypto, monkeypatch, directional_reference):
    row = analysis_row(event_payload, crypto)
    event, _metadata = worker_v223._inference_event(row, crypto)
    parsed = parse_http_payload(RAW)
    built = input_builder_v223.build_agent_input(event, RAW, parsed, 32000, 3072,
        evidence_selection=True, fixed_rules_version=NEW)
    document = json.loads(built.text)
    items = document["evidence_candidates"]["items"]
    reference = next(item for item in items if item["field"] == "signature_explanation")
    request = next(item for item in items if item["field"] == "payload")
    calls = []

    async def execute(**kwargs):
        calls.append(kwargs)
        if kwargs["agent_name"].endswith("-evidence-repair"):
            assert directional_reference
            # Selecting the same context source again cannot bypass the source
            # policy. Citation-only correction does not change supports.
            output = EvidenceSelectionCorrectionOutput(corrections=[{
                "index": 1, "source_id": reference["source_id"],
            }], requires_reanalysis=False)
        else:
            data = wire()
            data["evidence"] = [
                {"source_id": request["source_id"], "supports": "attack_request",
                 "interpretation_ko": "요청 내용의 위치와 동작을 연결하여 해석합니다."},
                {"source_id": reference["source_id"],
                 "supports": "attack_request" if directional_reference else "context",
                 "interpretation_ko": "등록한 설명은 요청의 의미를 비교하는 참고 정보입니다."},
            ]
            data["evidence_relations"] = []
            output = SecurityAnalysisOutputV220.model_validate(data)
        return AgentCallResult(output, "synthetic-only-run", None, "completed", None, None, {})

    monkeypatch.setattr(worker_v223, "execute_structured_agent", execute)
    call = worker_v223._execute_grounded_agent(
        analysis=row, inference_event=event, raw_payload=RAW, parsed=parsed,
        input_truncated=built.input_truncated, submitted_payload_spans=built.retained_payload_spans,
        assessment_enabled=True, semantic_enabled=True, security_analysis_enabled=True,
        fixed_rules_version=NEW, user_input=built.text, agent_name="waf-primary",
        session_id="synthetic-only-session", instructions="synthetic-only-instructions")
    assert call.succeeded
    if directional_reference:
        assert len(calls) == 2
        assert call.output.verdict.value == "inconclusive"
        assert all(item.field != "signature_explanation" for item in call.output.evidence)
        assert call.telemetry["evidence_grounding_retry"]["requires_review"]
        assert call.grounding_history[-1]["rejected_evidence"] == [
            {"index": 1, "reason": "signature_support_not_allowed"}]
    else:
        assert len(calls) == 1
        assert call.output.verdict.value == "true_positive"
        assert any(item.field == "signature_explanation" for item in call.output.evidence)


@pytest.mark.usefixtures("registered_vllm_target")
def test_complete_worker_shares_pinned_description_but_keeps_received_event_unchanged(
        client, event_payload, service_headers, monkeypatch):
    event = {**event_payload, "payload": RAW, "waf_vendor": "Synthetic", "signature": "Synthetic detection"}
    response = client.post("/api/v1/analyses?wait_seconds=0", headers=service_headers, json=event)
    assert response.status_code == 202, response.text
    crypto = client.app.state.crypto
    with client.app.state.session_factory() as db:
        row = db.get(Analysis, response.json()["id"])
        prompt = _build_snapshot(get_active_policy(db, crypto), crypto)
        row.prompt_version, row.prompt_policy_version_id = prompt.prompt_version, prompt.policy_version_id
        row.prompt_snapshot_ciphertext = crypto.encrypt_text(prompt.model_dump_json())
        version = create_version(db, crypto, name="Synthetic runtime reference",
            change_reason="Synthetic offline test", entries=catalog()["entries"])
        value = event_snapshot(snapshot_for_version(db, crypto, version.id), row.waf_vendor, row.signature)
        row.signature_explanation_version_id = value["version_id"]
        row.signature_explanation_snapshot_ciphertext = crypto.encrypt_text(json.dumps(value))
        db.add(VLLMProfile(name="synthetic-v223-only", base_url="http://10.0.0.10:8000/v1",
            model_name="synthetic-model", status="production"))
        db.commit()

    calls = []
    async def execute(**kwargs):
        calls.append(kwargs)
        document = json.loads(kwargs["user_input"])
        assert document["event"]["signature_explanation"] == DESCRIPTION
        assert "primary_output" not in document and "primary" not in document
        items = document["evidence_candidates"]["items"]
        reference = next(item for item in items if item["field"] == "signature_explanation")
        request = next(item for item in items if item["field"] == "payload")
        data = wire()
        data["confidence_score"] = .65  # Exercise the independent Verifier too.
        data["evidence"] = [
            {"source_id": request["source_id"], "supports": "attack_request",
             "interpretation_ko": "원문에 있는 요청 동작과 입력의 의미를 함께 해석했습니다."},
            {"source_id": reference["source_id"], "supports": "context",
             "interpretation_ko": "탐지 의도를 설명하는 등록된 참고 자료입니다."},
        ]
        data["evidence_relations"] = [{"representative_index": 0, "related_indexes": [1]}]
        return AgentCallResult(SecurityAnalysisOutputV220.model_validate(data),
            "synthetic-v223-run", None, "completed", None, None, {"framework_version": "0.6.2"})
    monkeypatch.setattr(worker_v223, "execute_structured_agent", execute)
    with client.app.state.session_factory() as db:
        row = db.get(Analysis, response.json()["id"])
        original_extra = deepcopy(row.extra_fields)
        worker_v223.process_moduagent(db, crypto, row, "", .75)
        db.commit()
        assert row.status == "completed"
        assert row.result_json["schema_version"] == "waf-analysis-v3"
        assert row.verdict == "true_positive"
        assert row.extra_fields == original_extra
        assert "signature_explanation" not in row.extra_fields
        run = db.query(AgentRun).filter_by(analysis_id=row.id).one()
        input_step = next(step for step in run.steps if step.step_type == "input")
        original_document = json.loads(crypto.decrypt_text(input_step.input_ciphertext))
        assert "signature_explanation" not in original_document
        input_history = next(step for step in run.steps if step.step_type == "agent_input")
        assert input_history.metadata_json["signature_explanation"]["matched"] is True
        assert input_history.metadata_json["signature_explanation"]["submitted_complete"] is True
        assert DESCRIPTION not in json.dumps(input_history.metadata_json, ensure_ascii=False)
    assert [call["agent_name"] for call in calls] == ["waf-primary", "waf-verifier"]
    assert calls[0]["user_input"] == calls[1]["user_input"]
