"""Provider routing and egress policy using synthetic inputs, never live APIs."""
import json

import pytest

from app.agent.contracts import WAFAnalysisOutput
from app.agent.executor import AgentCallResult
from app.models import Analysis, VLLMProfile, VLLMTestRun
from app.services.crypto import CryptoService
from app.services.vllm_profiles import profile_fingerprint
from app.worker import claim_next, mark_failed, process_moduagent, process_vllm_test
from test_moduagent_worker import primary_output


SYNTHETIC_KEY = "synthetic-openai-worker-key"


def openai_profile(crypto, **overrides):
    values = {
        "name": "synthetic-openai-profile", "provider": "openai",
        "base_url": "https://api.openai.com/v1", "model_name": "synthetic-chat-model",
        "external_data_approved": True, "tls_verify": True,
        "api_key_ciphertext": crypto.encrypt_text(SYNTHETIC_KEY),
        "encryption_key_version": crypto.key_version, "status": "production",
        "context_window": 32768, "max_output_tokens": 3072,
    }
    return VLLMProfile(**{**values, **overrides})


@pytest.mark.parametrize("verifier_outcome", ["agree", "disagree", "fail"])
def test_openai_worker_preserves_independent_verifier_and_provider_snapshot(
    client, settings, event_payload, service_headers, monkeypatch, verifier_outcome,
):
    event_payload["waf_action"] = "A"  # An allowed TP must invoke the verifier.
    created = client.post("/api/v1/analyses", headers=service_headers, json=event_payload).json()
    crypto = CryptoService(settings.data_encryption_key, settings.encryption_key_version)
    with client.app.state.session_factory() as db:
        profile = openai_profile(crypto)
        db.add(profile)
        db.commit()
        expected_fingerprint = profile_fingerprint(profile)

    calls = []

    async def execute(**kwargs):
        calls.append(kwargs)
        assert kwargs["profile"].provider == "openai"
        assert kwargs["profile"].base_url == "https://api.openai.com/v1"
        assert kwargs["api_key"] == SYNTHETIC_KEY
        output = primary_output("q=test")
        if len(calls) == 1:
            output = output.model_copy(update={"summary_ko": "SYNTHETIC_PRIMARY_JUDGMENT_NOT_FOR_VERIFIER"})
        elif verifier_outcome == "fail":
            output = None
        elif verifier_outcome == "disagree":
            data = output.model_dump(mode="json")
            data["verdict"] = "false_positive"
            data["threat_analysis"]["severity"] = "NONE"
            output = WAFAnalysisOutput.model_validate(data)
        return AgentCallResult(
            output=output, framework_run_id=f"synthetic-run-{len(calls)}",
            agent_fingerprint="synthetic-fingerprint", finish_reason="completed" if output else "error",
            failure_id=None if output else "synthetic-failure", error=None if output else "safe failure",
            telemetry={"framework": "moduagent", "framework_version": "0.6.2", "thinking_enabled": None},
        )

    monkeypatch.setattr("app.worker.execute_structured_agent", execute)
    with client.app.state.session_factory() as db:
        analysis = claim_next(db, "synthetic-worker", 300)
        process_moduagent(db, crypto, analysis, "vllm.internal:8000", 0.75)

    assert [call["agent_name"] for call in calls] == ["waf-primary", "waf-verifier"]
    assert calls[0]["user_input"] == calls[1]["user_input"]
    assert "SYNTHETIC_PRIMARY_JUDGMENT_NOT_FOR_VERIFIER" not in calls[1]["user_input"]
    assert "Cookie: session=fixture" in calls[1]["user_input"]
    assert calls[0]["instructions"] != calls[1]["instructions"]
    client.post("/api/v1/auth/login", json={"username": "admin", "password": "test-password"})
    detail = client.get(f"/api/v1/analyses/{created['id']}").json()
    result = detail["result"]
    assert detail["status"] == "completed"
    assert result["verdict"] == ("true_positive" if verifier_outcome == "agree" else "inconclusive")
    assert result["threat_analysis"]["severity"] == ("HIGH" if verifier_outcome == "agree" else "UNKNOWN")
    assert result["verifier"]["executed"] is True
    assert result["agent"]["llm_provider"] == "openai"
    assert result["agent"]["model_name"] == "synthetic-chat-model"
    assert result["agent"]["profile_fingerprint"] == expected_fingerprint
    assert result["agent"]["external_data_approved"] is True
    assert result["agent"]["thinking_enabled"] is None
    assert SYNTHETIC_KEY not in json.dumps(detail)
    runs = client.get(f"/api/v1/analyses/{created['id']}/agent-runs").json()
    for step in runs[0]["steps"]:
        if step["step_type"] in {"input", "llm_primary", "llm_verifier"}:
            assert step["metadata"]["llm_provider"] == "openai"
            assert step["metadata"]["profile_fingerprint"] == expected_fingerprint
        assert SYNTHETIC_KEY not in json.dumps(step)


@pytest.mark.parametrize("case", ["approval", "target", "key", "empty_key", "tls"])
def test_worker_rejects_invalid_openai_profiles_before_any_model_call(
    client, settings, event_payload, service_headers, monkeypatch, case,
):
    created = client.post("/api/v1/analyses", headers=service_headers, json=event_payload).json()
    crypto = CryptoService(settings.data_encryption_key, settings.encryption_key_version)
    overrides = {
        "approval": {"external_data_approved": False},
        "target": {"base_url": "https://synthetic-invalid.example/v1"},
        "key": {"api_key_ciphertext": None},
        "empty_key": {"api_key_ciphertext": crypto.encrypt_text("   ")},
        "tls": {"tls_verify": False},
    }[case]

    async def forbidden_call(**kwargs):
        pytest.fail("Rejected OpenAI profile must not send any input")

    monkeypatch.setattr("app.worker.execute_structured_agent", forbidden_call)
    with client.app.state.session_factory() as db:
        db.add(openai_profile(crypto, **overrides))
        db.commit()
        analysis = claim_next(db, "synthetic-worker", 300)
        with pytest.raises((ValueError, RuntimeError)) as error:
            process_moduagent(db, crypto, analysis, "synthetic-invalid.example:443", 0.75)
        mark_failed(db, analysis, error.value)
    detail = client.get(f"/api/v1/analyses/{created['id']}", headers=service_headers).json()
    assert detail["status"] == "failed"
    assert detail["result"] is None
    assert SYNTHETIC_KEY not in json.dumps(detail)
    assert "session=fixture" not in json.dumps(detail)


def test_model_test_worker_checks_openai_approval_before_dispatch(client, settings, monkeypatch):
    crypto = CryptoService(settings.data_encryption_key, settings.encryption_key_version)

    async def forbidden_test(*args, **kwargs):
        pytest.fail("Unapproved OpenAI target must not receive even profile tests")

    monkeypatch.setattr("app.worker.run_vllm_test", forbidden_test)
    with client.app.state.session_factory() as db:
        profile = openai_profile(crypto, status="draft", external_data_approved=False)
        db.add(profile)
        db.flush()
        run = VLLMTestRun(profile_id=profile.id, profile_fingerprint=profile_fingerprint(profile), mode="full", status="running")
        db.add(run)
        db.commit()
        process_vllm_test(db, crypto, run, "vllm.internal:8000")
        assert run.status == "failed"
        assert profile.status == "draft"
        assert run.error_code
        assert SYNTHETIC_KEY not in run.error_message


def test_failed_openai_call_still_records_selected_model_profile(
    client, settings, event_payload, service_headers, monkeypatch,
):
    created = client.post("/api/v1/analyses", headers=service_headers, json=event_payload).json()
    crypto = CryptoService(settings.data_encryption_key, settings.encryption_key_version)

    async def failure(**kwargs):
        raise RuntimeError("SYNTHETIC_UNSAFE_UPSTREAM_BODY")

    monkeypatch.setattr("app.worker.execute_structured_agent", failure)
    with client.app.state.session_factory() as db:
        db.add(openai_profile(crypto))
        db.commit()
        analysis = claim_next(db, "synthetic-worker", 300)
        with pytest.raises(RuntimeError) as error:
            process_moduagent(db, crypto, analysis, "vllm.internal:8000", 0.75)
        mark_failed(db, analysis, error.value)
    detail = client.get(f"/api/v1/analyses/{created['id']}", headers=service_headers).json()
    assert detail["model_profile"] == "synthetic-openai-profile"
    assert detail["error_code"] == "primary_agent_failed"
    assert "SYNTHETIC_UNSAFE_UPSTREAM_BODY" not in json.dumps(detail)
