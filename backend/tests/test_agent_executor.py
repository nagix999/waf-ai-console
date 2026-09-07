import asyncio
import json
from types import SimpleNamespace

import moduagent
import pytest
from moduagent.output import PydanticOutputCodec
from pydantic import ValidationError

from app.agent.contracts import WAFAnalysisOutput
from app.agent.executor import _TrackingOutputCodec, _safe_validation_issues, execute_structured_agent


def valid_output() -> WAFAnalysisOutput:
    return WAFAnalysisOutput.model_validate(
        {
            "verdict": "true_positive",
            "confidence_score": 0.91,
            "summary_ko": "원문 근거에 따라 SQL Injection 시도로 판단합니다.",
            "threat_analysis": {
                "category": "sql_injection",
                "target": "query:q",
                "severity": "HIGH",
                "technique_ko": "항상 참인 SQL 조건을 삽입했습니다.",
                "obfuscations": ["url_encoding"],
                "potential_impact_ko": "조회 조건 우회로 데이터가 노출될 수 있습니다.",
            },
            "signature_assessment": {
                "relation": "exact",
                "explanation_ko": "시그니처와 요청 구문이 일치합니다.",
            },
            "evidence": [
                {
                    "field": "payload.query",
                    "excerpt": "%27+OR+1%3D1--",
                    "interpretation_ko": (
                        "원문에 URL 인코딩된 작은따옴표와 항상 참인 SQL 조건이 함께 있습니다. "
                        "이는 조회 조건을 변경하려는 전형적인 SQL Injection 구문이며 최종 정탐 판정을 직접 지지합니다."
                    ),
                }
            ],
            "recommended_checks": ["대상 애플리케이션 로그를 확인하세요."],
            "tuning_recommendation": {"recommended": False},
            "conflicting_evidence": [],
            "input_truncated": False,
        }
    )


def fake_result(
    *,
    output: WAFAnalysisOutput | None,
    run_id: str,
    failure_id: str | None = None,
    error_code: str | None = None,
):
    completed = output is not None
    return SimpleNamespace(
        output=output,
        run_id=run_id,
        finish_reason="completed" if completed else "failed",
        failure_id=failure_id,
        error=None if completed else "run failed",
        usage={},
        run_usage={},
        metadata={},
        tool_trace=[],
        error_summary=(
            None
            if error_code is None
            else {"category": "output_validation", "code": error_code, "retryable": False}
        ),
    )


def profile():
    return SimpleNamespace(
        base_url="http://vllm.internal:8000/v1",
        model_name="google/gemma-4-26B-A4B-it",
        max_output_tokens=3072,
        timeout_seconds=120,
        tls_verify=True,
    )


class FakeVLLMClient:
    def __init__(self, **_kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, _exc_type, _exc, _traceback):
        return None


def install_fake_agents(monkeypatch, results):
    created = []
    sessions = []

    class FakeAgent:
        def __init__(self, result, fingerprint, output_codec):
            self.result = result
            self.fingerprint = fingerprint
            self.output_codec = output_codec

        def inspect(self):
            return SimpleNamespace(agent_fingerprint=self.fingerprint)

        async def run(self, _user_input, *, session_id):
            sessions.append(session_id)
            if self.result.error_summary and self.result.error_summary.get("code") == "output_validation_failed":
                self.output_codec.validation_issues = [
                    {"field": "threat_analysis.severity", "type": "missing"}
                ]
            return self.result

    queued = iter(results)

    def create(**kwargs):
        created.append(kwargs)
        return FakeAgent(next(queued), f"fingerprint-{len(created)}", kwargs["output"])

    monkeypatch.setattr(moduagent, "VLLMClient", FakeVLLMClient)
    monkeypatch.setattr(moduagent.Agent, "create", staticmethod(create))
    return created, sessions


def test_output_validation_failure_gets_one_corrective_retry(monkeypatch):
    created, sessions = install_fake_agents(
        monkeypatch,
        [
            fake_result(
                output=None,
                run_id="failed-run",
                failure_id="failure-1",
                error_code="output_validation_failed",
            ),
            fake_result(output=valid_output(), run_id="repaired-run"),
        ],
    )

    result = asyncio.run(
        execute_structured_agent(
            profile=profile(),
            egress_check=lambda: None,  # No transport: the fake agent returns synthetic outputs.
            api_key=None,
            instructions="primary instructions",
            user_input="synthetic event",
            session_id="analysis-1:primary",
            agent_name="waf-primary",
        )
    )

    assert result.succeeded is True
    assert result.framework_run_id == "repaired-run"
    assert len(created) == 2
    assert created[0]["name"] == "waf-primary"
    assert created[1]["name"] == "waf-primary-output-repair"
    assert "output_validation_failed" in created[1]["instructions"]
    assert created[0]["instructions"] == "primary instructions"
    assert created[1]["instructions"].startswith(created[0]["instructions"] + "\n\n")
    assert "threat_analysis.severity: missing" in created[1]["instructions"]
    assert sessions == ["analysis-1:primary", "analysis-1:primary:output-repair-1"]
    retry = result.telemetry["output_validation_retry"]
    assert retry["attempted"] is True
    assert retry["attempt_count"] == 2
    assert retry["recovered"] is True
    assert retry["attempts"][0]["error_code"] == "output_validation_failed"
    assert retry["attempts"][0]["validation_issues"] == [
        {"field": "threat_analysis.severity", "type": "missing"}
    ]


def test_non_validation_failure_is_not_retried(monkeypatch):
    created, sessions = install_fake_agents(
        monkeypatch,
        [
            fake_result(
                output=None,
                run_id="failed-run",
                failure_id="failure-1",
                error_code="vllm_http_400",
            )
        ],
    )

    result = asyncio.run(
        execute_structured_agent(
            profile=profile(),
            egress_check=lambda: None,
            api_key=None,
            instructions="primary instructions",
            user_input="synthetic event",
            session_id="analysis-1:primary",
            agent_name="waf-primary",
        )
    )

    assert result.succeeded is False
    assert len(created) == 1
    assert sessions == ["analysis-1:primary"]
    assert result.telemetry["output_validation_retry"]["attempted"] is False


def test_output_validation_retry_stops_after_one_repair_attempt(monkeypatch):
    created, sessions = install_fake_agents(
        monkeypatch,
        [
            fake_result(
                output=None,
                run_id="failed-run-1",
                failure_id="failure-1",
                error_code="output_validation_failed",
            ),
            fake_result(
                output=None,
                run_id="failed-run-2",
                failure_id="failure-2",
                error_code="output_validation_failed",
            ),
        ],
    )

    result = asyncio.run(
        execute_structured_agent(
            profile=profile(),
            egress_check=lambda: None,
            api_key=None,
            instructions="primary instructions",
            user_input="synthetic event",
            session_id="analysis-1:primary",
            agent_name="waf-primary",
        )
    )

    assert result.succeeded is False
    assert result.failure_id == "failure-2"
    assert len(created) == 2
    assert sessions == ["analysis-1:primary", "analysis-1:primary:output-repair-1"]
    retry = result.telemetry["output_validation_retry"]
    assert retry["attempt_count"] == 2
    assert retry["recovered"] is False


def test_validation_issue_metadata_does_not_include_model_generated_field_names():
    class UnsafeValidationError(ValueError):
        def errors(self, **_kwargs):
            return [
                {
                    "loc": ("threat_analysis", "payload-content-must-not-leak"),
                    "type": "extra_forbidden",
                    "input": "sensitive model output",
                }
            ]

    assert _safe_validation_issues(UnsafeValidationError()) == [
        {"field": "threat_analysis.<unknown>", "type": "extra_forbidden"}
    ]


def test_tracking_codec_captures_safe_field_and_error_type():
    document = valid_output().model_dump(mode="json")
    del document["threat_analysis"]["severity"]
    codec = _TrackingOutputCodec(PydanticOutputCodec(WAFAnalysisOutput))

    with pytest.raises(ValidationError):
        codec.decode({"message": {"content": json.dumps(document)}})

    assert codec.validation_issues == [
        {"field": "threat_analysis.severity", "type": "missing"}
    ]
