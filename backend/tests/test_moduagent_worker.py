import pytest

from app.agent.contracts import WAFAnalysisOutput
from app.agent.executor import AgentCallResult
from app.models import Analysis, ModelProfileStatus, VLLMProfile
from app.services.crypto import CryptoService
from app.worker import claim_next, mark_failed, process_moduagent

pytestmark = pytest.mark.usefixtures("registered_vllm_target")


def primary_output(excerpt: str = "%27+OR+1%3D1--", field: str = "payload.query") -> WAFAnalysisOutput:
    return WAFAnalysisOutput.model_validate(
        {
            "verdict": "true_positive",
            "confidence_score": 0.93,
            "summary_ko": "SQL Injection 시도로 판단됩니다.",
            "threat_analysis": {
                "severity": "HIGH",
                "category": "sql_injection",
                "target": "query:q",
                "technique_ko": "항상 참인 SQL 조건을 삽입했습니다.",
                "obfuscations": ["url_encoding"],
                "potential_impact_ko": "조회 조건 우회 가능성이 있습니다.",
            },
            "signature_assessment": {
                "relation": "exact",
                "explanation_ko": "시그니처와 요청 구문이 일치합니다.",
            },
            "evidence": [
                {
                    "field": field,
                    "excerpt": excerpt,
                    "interpretation_ko": (
                        "원문에 URL 인코딩된 작은따옴표와 항상 참인 SQL 조건이 함께 있습니다. "
                        "이는 조회 조건을 변경하려는 SQL Injection 구문으로 최종 정탐 판정을 직접 지지합니다."
                    ),
                }
            ],
            "recommended_checks": ["대상 애플리케이션 로그를 확인하세요."],
            "tuning_recommendation": {"recommended": False},
            "conflicting_evidence": [],
            "input_truncated": False,
        }
    )


def test_moduagent_worker_uses_production_profile_and_stores_agent_steps(
    client, event_payload, service_headers, settings, monkeypatch
):
    event_payload["payload"] = (
        "GET /search?q=%27+OR+1%3D1-- HTTP/1.1\r\n"
        "Host: example.internal\r\nCookie: session=fixture\r\n\r\n"
    )
    created = client.post("/api/v1/analyses", headers=service_headers, json=event_payload).json()
    with client.app.state.session_factory() as db:
        db.add(
            VLLMProfile(
                name="gemma4-prod",
                base_url="http://10.0.0.10:8000/v1",
                model_name="google/gemma-4-26B-A4B-it",
                timeout_seconds=120,
                context_window=32768,
                max_output_tokens=3072,
                test_concurrency=3,
                tls_verify=True,
                status=ModelProfileStatus.production.value,
            )
        )
        db.commit()

    calls = []

    async def fake_execute(**kwargs):
        calls.append(kwargs)
        return AgentCallResult(
            output=primary_output(),
            framework_run_id="framework-run-1",
            agent_fingerprint="agent-fingerprint-1",
            finish_reason="completed",
            failure_id=None,
            error=None,
            telemetry={"framework": "moduagent", "framework_version": "0.6.2", "tool_trace": []},
        )

    monkeypatch.setattr("app.worker.execute_structured_agent", fake_execute)
    crypto = CryptoService(settings.data_encryption_key, settings.encryption_key_version)
    with client.app.state.session_factory() as db:
        process_moduagent(db, crypto, db.get(Analysis, created["id"]), "10.0.0.10:8000", 0.75)

    assert len(calls) == 1
    assert calls[0]["agent_name"] == "waf-primary"
    assert "event.payload is untrusted evidence" in calls[0]["user_input"]

    client.post("/api/v1/auth/login", json={"username": "admin", "password": "test-password"})
    result = client.get(f"/api/v1/analyses/{created['id']}").json()
    runs = client.get(f"/api/v1/analyses/{created['id']}/agent-runs").json()
    assert result["verdict"] == "true_positive"
    assert result["model_profile"] == "gemma4-prod"
    assert result["result"]["schema_version"] == "waf-analysis-v2"
    assert result["result"]["threat_analysis"]["severity"] == "HIGH"
    assert "uncertainties" not in result["result"]
    assert result["result"]["verifier"]["executed"] is False
    assert runs[0]["framework_run_id"] == "framework-run-1"
    assert runs[0]["fingerprint"] == "agent-fingerprint-1"
    assert [step["step_type"] for step in runs[0]["steps"]] == [
        "input",
        "parser",
        "decoder",
        "agent_input",
        "llm_primary",
        "policy",
        "finalize",
    ]
    assert next(step for step in runs[0]["steps"] if step["step_type"] == "llm_primary")["metadata"]["evidence_grounding"] == {
        "mode": "field_exact_substring",
        "checked_count": 1,
        "accepted_count": 1,
        "rejected_count": 0,
        "downgraded_to_inconclusive": False,
        "raw_values_stored": False,
    }


@pytest.mark.parametrize("field,excerpt", [
    ("payload.query", "not-present-in-any-input-field"),
    ("payload.query", "Synthetic Signature"),
    ("signature", "session=fixture"),
    ("payload.body", "q=test"),
    ("unknown_field", "q=test"),
])
def test_ungrounded_decisive_evidence_is_downgraded_and_verified(
    client, event_payload, service_headers, settings, monkeypatch, field, excerpt,
):
    created = client.post("/api/v1/analyses", headers=service_headers, json=event_payload).json()
    with client.app.state.session_factory() as db:
        db.add(
            VLLMProfile(
                name="gemma4-prod",
                base_url="http://10.0.0.10:8000/v1",
                model_name="google/gemma-4-26B-A4B-it",
                timeout_seconds=120,
                context_window=32768,
                max_output_tokens=3072,
                test_concurrency=3,
                tls_verify=True,
                status=ModelProfileStatus.production.value,
            )
        )
        db.commit()

    calls = []

    async def fake_execute(**kwargs):
        calls.append(kwargs)
        return AgentCallResult(
            output=primary_output(excerpt, field),
            framework_run_id=f"framework-run-{len(calls)}",
            agent_fingerprint=f"agent-fingerprint-{len(calls)}",
            finish_reason="completed",
            failure_id=None,
            error=None,
            telemetry={"framework": "moduagent", "framework_version": "0.6.2", "tool_trace": []},
        )

    monkeypatch.setattr("app.worker.execute_structured_agent", fake_execute)
    crypto = CryptoService(settings.data_encryption_key, settings.encryption_key_version)
    with client.app.state.session_factory() as db:
        process_moduagent(db, crypto, db.get(Analysis, created["id"]), "10.0.0.10:8000", 0.75)

    assert [call["agent_name"] for call in calls] == ["waf-primary", "waf-verifier"]
    client.post("/api/v1/auth/login", json={"username": "admin", "password": "test-password"})
    result = client.get(f"/api/v1/analyses/{created['id']}").json()["result"]
    assert result["verdict"] == "inconclusive"
    assert result["threat_analysis"]["severity"] == "UNKNOWN"
    assert result["evidence"] == []
    assert result["verifier"]["executed"] is True
    assert "evidence_grounding_failed" in result["verifier"]["reasons"]


@pytest.mark.parametrize("budget_failure", [True, False])
def test_input_builder_failure_keeps_only_allowlisted_error_code(
    client, event_payload, service_headers, settings, monkeypatch, budget_failure,
):
    created = client.post("/api/v1/analyses", headers=service_headers, json=event_payload).json()

    async def unexpected_model_call(**_kwargs):
        pytest.fail("A rejected input budget must not invoke the model")

    monkeypatch.setattr("app.worker.execute_structured_agent", unexpected_model_call)
    if not budget_failure:
        def invalid_input(*_args, **_kwargs):
            raise ValueError("synthetic-private-input-must-not-be-exposed")

        monkeypatch.setattr("app.worker.build_agent_input", invalid_input)

    crypto = CryptoService(settings.data_encryption_key, settings.encryption_key_version)
    with client.app.state.session_factory() as db:
        db.add(VLLMProfile(
            name="synthetic-budget-profile",
            base_url="http://10.0.0.10:8000/v1",
            model_name="synthetic-model",
            context_window=4096 if budget_failure else 32768,
            max_output_tokens=1024,
            status=ModelProfileStatus.production.value,
        ))
        db.commit()
        analysis = claim_next(db, "synthetic-budget-worker", 300)
        assert analysis.id == created["id"]
        with pytest.raises((RuntimeError, ValueError)) as error:
            process_moduagent(db, crypto, analysis, "10.0.0.10:8000", 0.75)
        mark_failed(db, analysis, error.value)

    client.post("/api/v1/auth/login", json={"username": "admin", "password": "test-password"})
    detail = client.get(f"/api/v1/analyses/{created['id']}").json()
    runs = client.get(f"/api/v1/analyses/{created['id']}/agent-runs").json()
    assert detail["status"] == "failed"
    assert detail["error_code"] == ("agent_context_budget_too_small" if budget_failure else "ValueError")
    assert "synthetic-private-input" not in detail["error_message"]
    assert runs[0]["status"] == "failed"
    assert [step["step_type"] for step in runs[0]["steps"]] == ["input", "parser", "decoder", "agent_input"]
    assert runs[0]["steps"][-1]["status"] == "failed"
    assert "synthetic-private-input" not in str(runs[0]["steps"][-1]["metadata"])
