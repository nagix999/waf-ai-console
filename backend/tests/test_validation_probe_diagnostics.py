"""Synthetic offline probes: budgets, incomplete output and safe diagnostics."""

import asyncio
import json

import httpx
import pytest

from app.services.vllm_test_runner import chat_payload, run_vllm_test
from test_agent_openai import HTTP_CLIENT, completion, install_http
from test_provider_test_runner import SyntheticCrypto, profile_for, successful_handler


def run(profile, mode="full"):
    return asyncio.run(run_vllm_test(profile, SyntheticCrypto(), mode, egress_check=lambda: None))


@pytest.mark.parametrize("limit,expected", [(4096, 1024), (1024, 1024), (512, 512), (256, 256), (128, 128)])
def test_vllm_probe_cap_respects_profile_without_mutating_it(limit, expected):
    profile = profile_for("vllm")
    profile.max_output_tokens = limit
    before = vars(profile).copy()
    body = chat_payload(profile, [{"role": "user", "content": "synthetic"}])
    assert body["max_tokens"] == expected
    assert body["chat_template_kwargs"] == {"enable_thinking": False}
    assert vars(profile) == before


def test_single_concurrent_probe_no_longer_fails_due_to_eight_token_cap(monkeypatch):
    profile = profile_for("vllm")
    profile.test_concurrency = 1

    def override(request, count):
        if request.method == "POST" and "Concurrency test" in json.loads(request.content)["messages"][0]["content"]:
            limit = json.loads(request.content)["max_tokens"]
            response = completion("synthetic answer with formatting", finish_reason="length" if limit < 16 else "stop")
            response["usage"] = {"prompt_tokens": 35, "completion_tokens": min(16, limit), "total_tokens": 35 + min(16, limit)}
            return httpx.Response(200, json=response)

    requests, _ = install_http(monkeypatch, successful_handler(profile, override))
    result = run(profile)
    assert result.passed
    assert len(requests) == 6  # No added probes or automatic retries.
    assert profile.max_output_tokens == 3072
    assert result.checks[-1]["detail"]["requests"] == 1
    assert result.checks[-1]["response_diagnostics"] == [{
        "request_index": 1, "requested_max_output_tokens": 1024, "http_status": 200, "error_code": None,
        "finish_reason": "stop", "refused": False,
        "prompt_tokens": 35, "completion_tokens": 16, "total_tokens": 51,
    }]
    assert result.checks[0]["response_diagnostics"] == []
    assert all(len(check["response_diagnostics"]) == 1 for check in result.checks[1:])
    assert all(json.loads(request.content)["max_tokens"] == 1024 for request in requests if request.method == "POST")
    assert "synthetic answer with formatting" not in json.dumps(result.__dict__)


@pytest.mark.parametrize("failed_index,failed_check", [
    (2, "basic_chat"), (3, "nested_json_schema"), (4, "system_role"),
    (5, "near_32k_context"), (6, "concurrency"),
])
@pytest.mark.parametrize("failure", ["length", "refusal"])
def test_vllm_still_rejects_incomplete_or_refused_output_at_every_check(monkeypatch, failed_index, failed_check, failure):
    profile = profile_for("vllm")
    profile.test_concurrency = 1

    def override(request, count):
        if count == failed_index:
            body = completion("OK synthetic-never-store", finish_reason="length" if failure == "length" else "stop",
                              refusal="synthetic-refusal-never-store" if failure == "refusal" else None)
            body["usage"]["completion_tokens"] = 1024
            return httpx.Response(200, json=body)

    requests, _ = install_http(monkeypatch, successful_handler(profile, override))
    result = run(profile)
    assert not result.passed
    assert len(requests) == failed_index
    assert result.metrics["failed_check"] == failed_check
    assert result.error_code == ("vllm_output_incomplete" if failure == "length" else "vllm_refusal")
    assert ("토큰 한도" if failure == "length" else "응답을 거절") in result.error_message
    diagnostic = result.checks[-1]["response_diagnostics"][0]
    assert diagnostic["requested_max_output_tokens"] == 1024
    assert diagnostic["completion_tokens"] == 1024
    assert diagnostic["finish_reason"] == ("length" if failure == "length" else "stop")
    assert diagnostic["refused"] is (failure == "refusal")
    assert diagnostic["error_code"] == result.error_code
    assert "never-store" not in json.dumps(result.__dict__)


@pytest.mark.parametrize("usage", [None, [], "synthetic-never-store", {},
    {"prompt_tokens": True, "completion_tokens": -1, "total_tokens": 1.5},
    {"prompt_tokens": "synthetic-never-store", "completion_tokens": 2**53, "total_tokens": False},
])
def test_missing_or_invalid_counters_are_unknown_not_zero(monkeypatch, usage):
    profile = profile_for("vllm")

    def override(request, count):
        if count == 2:
            body = completion("OK")
            body["usage"] = usage
            return httpx.Response(200, json=body)

    install_http(monkeypatch, successful_handler(profile, override))
    result = run(profile, "quick")
    assert result.passed  # Usage is required only by the near-context check.
    record = result.checks[1]["response_diagnostics"][0]
    assert [record[name] for name in ("prompt_tokens", "completion_tokens", "total_tokens")] == [None] * 3
    assert "never-store" not in json.dumps(result.__dict__)


def test_zero_usage_is_preserved_and_private_nested_fields_are_dropped(monkeypatch):
    profile = profile_for("openai")

    def override(request, count):
        if count == 2:
            body = completion("synthetic-content-never-store")
            body["usage"] = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0,
                             "completion_tokens_details": {"private": "synthetic-nested-never-store"}}
            body["choices"][0]["message"]["reasoning_content"] = "synthetic-reasoning-never-store"
            return httpx.Response(200, json=body)

    install_http(monkeypatch, successful_handler(profile, override))
    result = run(profile, "quick")
    assert result.passed
    record = result.checks[1]["response_diagnostics"][0]
    assert record["requested_max_output_tokens"] == 3072
    assert [record[name] for name in ("prompt_tokens", "completion_tokens", "total_tokens")] == [0] * 3
    assert "never-store" not in json.dumps(result.__dict__)


@pytest.mark.parametrize("reason", [None, "synthetic-never-store", {"secret": "synthetic-never-store"}, ["synthetic-never-store"]])
def test_unexpected_finish_reasons_never_copy_arbitrary_provider_data(monkeypatch, reason):
    profile = profile_for("vllm")
    install_http(monkeypatch, successful_handler(profile, lambda request, count:
        httpx.Response(200, json=completion("synthetic-never-store", finish_reason=reason)) if count == 2 else None))
    result = run(profile, "quick")
    assert not result.passed
    assert result.error_code == "vllm_completion_not_finished"
    assert result.checks[-1]["response_diagnostics"][0]["finish_reason"] == (None if reason is None else "unknown")
    assert "never-store" not in json.dumps(result.__dict__)


@pytest.mark.parametrize("failure", ["timeout", "connection", "http", "json", "empty"])
def test_request_failures_keep_requested_budget_without_inventing_usage(monkeypatch, failure):
    profile = profile_for("vllm")

    def override(request, count):
        if count != 2:
            return None
        if failure == "timeout":
            raise httpx.ReadTimeout("synthetic-never-store", request=request)
        if failure == "connection":
            raise httpx.ConnectError("synthetic-never-store", request=request)
        if failure == "http":
            return httpx.Response(503, json={"error": "synthetic-never-store", "usage": {"completion_tokens": 123}})
        if failure == "json":
            return httpx.Response(200, text="synthetic-never-store")
        return httpx.Response(200, json={"choices": [], "private": "synthetic-never-store"})

    requests, _ = install_http(monkeypatch, successful_handler(profile, override))
    result = run(profile, "quick")
    assert not result.passed
    assert len(requests) == 2
    record = result.checks[-1]["response_diagnostics"][0]
    assert record["requested_max_output_tokens"] == 1024
    assert record["http_status"] == (None if failure in {"timeout", "connection"} else 503 if failure == "http" else 200)
    assert record["finish_reason"] is None
    assert record["refused"] is None
    assert record["completion_tokens"] is None
    assert record["error_code"] == result.error_code
    assert "never-store" not in json.dumps(result.__dict__)


def test_concurrent_failure_waits_for_all_submitted_requests_and_records_each(monkeypatch):
    profile = profile_for("vllm")
    profile.test_concurrency = 3
    started, completed = [], []
    ready = asyncio.Event()
    normal = successful_handler(profile)

    async def receive(request):
        if request.method == "GET":
            return normal(request, 0)
        text = json.loads(request.content)["messages"][0]["content"]
        if not text.startswith("Concurrency test"):
            return normal(request, 0)
        index = int(text.split()[2].rstrip("."))
        started.append(index)
        if len(started) == 3:
            ready.set()
        try:
            await asyncio.wait_for(ready.wait(), 1)
            await asyncio.sleep(index * 0.01)
            if index == 1:
                raise httpx.ReadTimeout("synthetic-never-store", request=request)
            return httpx.Response(200, json=completion("synthetic-never-store", finish_reason="length" if index == 0 else "stop"))
        finally:
            completed.append(index)

    class CheckedClient(HTTP_CLIENT):
        async def __aexit__(self, *args):
            assert sorted(completed) == [0, 1, 2], "Client closed while requests were pending"
            return await super().__aexit__(*args)

    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: CheckedClient(transport=httpx.MockTransport(receive), **kwargs))
    result = run(profile)
    assert not result.passed
    assert result.error_code == "vllm_output_incomplete"
    assert sorted(started) == sorted(completed) == [0, 1, 2]
    records = result.checks[-1]["response_diagnostics"]
    assert [record["request_index"] for record in records] == [1, 2, 3]
    assert [record["finish_reason"] for record in records] == ["length", None, "stop"]
    assert [record["http_status"] for record in records] == [200, None, 200]
    assert [record["error_code"] for record in records] == ["vllm_output_incomplete", "vllm_timeout", None]
    assert all(record["requested_max_output_tokens"] == 1024 for record in records)
    assert all(len(check["response_diagnostics"]) == 1 for check in result.checks[1:-1])
    assert "never-store" not in json.dumps(result.__dict__)


@pytest.mark.usefixtures("registered_vllm_target")
@pytest.mark.parametrize("include_dataset", [False, True])
@pytest.mark.parametrize("failed_index,failed_check", [(3, "nested_json_schema"), (6, "concurrency")])
def test_failed_probe_metadata_survives_worker_storage_and_admin_api(client, monkeypatch, include_dataset, failed_index, failed_check):
    from app import worker
    from test_model_profiles import login_admin, profile_payload

    login_admin(client)
    payload = profile_payload()
    payload["test_concurrency"] = 1
    created = client.post("/api/v1/model-profiles", json=payload)
    assert created.status_code == 201
    profile_id = created.json()["id"]
    queued = client.post(f"/api/v1/model-profiles/{profile_id}/tests", json={
        "mode": "full", "include_dataset": include_dataset,
        "name": "출력 중단 검증", "idempotency_key": "synthetic-output-limit-check",
    })
    assert queued.status_code == 202
    run_id = queued.json()["id"]

    def override(request, count):
        if count == failed_index:
            return httpx.Response(200, json=completion('{"test":"synthetic-never-store"}' + " " * 60, finish_reason="length"))

    requests, _ = install_http(monkeypatch, successful_handler(profile_for("vllm"), override))
    monkeypatch.setattr(worker, "execute_structured_agent", lambda **kwargs: pytest.fail("A failed technical check must not start dataset evaluation"))
    with client.app.state.session_factory() as db:
        claimed = worker.claim_next_vllm_test(db, "synthetic-validation-worker", 300)
        assert claimed.id == run_id
        worker.process_vllm_test(db, client.app.state.crypto, claimed, "")

    response = client.get(f"/api/v1/model-profiles/{profile_id}/tests/{run_id}")
    assert response.status_code == 200
    report = response.json()
    assert report["status"] == "failed"
    failed = report["checks"][-1]
    assert failed["name"] == failed_check
    assert failed["error_code"] == "vllm_output_incomplete"
    record = failed["response_diagnostics"][0]
    assert record["requested_max_output_tokens"] == 1024
    assert record["finish_reason"] == "length"
    assert record["completion_tokens"] == 250
    if failed_check == "nested_json_schema":
        assert record["json_output"]["status"] == "complete"
        assert record["json_output"]["trailing_whitespace_chars"] == 60
    assert "never-store" not in response.text and "profile-secret" not in response.text
    assert len(requests) == failed_index
