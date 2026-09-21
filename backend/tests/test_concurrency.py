"""File-backed WAL and fake model I/O only. No real LLM calls."""
import asyncio
import json
import multiprocessing
import time
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from datetime import timedelta
from threading import Event, Lock, Thread
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select, update

from app import worker
from app.config import Settings
from app.database import Base, build_engine, build_session_factory
from app.models import AccessAudit, AgentRun, Analysis, ConcurrencyConfiguration, LLMCallSlot, VLLMProfile, utcnow
from app.services.concurrency import call_slot, lock_configuration, release, server_key, try_reserve
from app.services.crypto import CryptoService
from app.services.worker_concurrency import AnalysisHeartbeat, run_analysis, serve
from test_model_profiles import login_admin

URL = "/api/v1/admin/agent-settings/concurrency"


@pytest.fixture
def runtime(tmp_path):
    settings = Settings(_env_file=None, database_url=f"sqlite+pysqlite:///{tmp_path / 'parallel.db'}",
        environment="development", public_origin="", agent_mode="stub", worker_role="analysis", worker_poll_seconds=.1,
        admin_username="fixture-admin", admin_password="fixture-password", session_secret="synthetic-session-secret-only",
        data_encryption_key="AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=", encryption_key_version="fixture-v1")
    engine = build_engine(settings.database_url)
    Base.metadata.create_all(engine)
    factory = build_session_factory(engine)
    crypto = CryptoService(settings.data_encryption_key, settings.encryption_key_version)
    yield settings, engine, factory, crypto
    engine.dispose()


def seed(runtime, count, purpose="production"):
    _, _, factory, crypto = runtime
    with factory() as db:
        existing = db.scalar(select(func.count()).select_from(Analysis))
        items = [Analysis(source_system="synthetic-concurrency", event_id=f"fixture-{existing + index}",
            analysis_purpose=purpose, company_name="Fixture", src_ip="192.0.2.1", dest_ip="198.51.100.1",
            waf_vendor="generic", waf_action="D", encryption_key_version=crypto.key_version,
            payload_ciphertext=crypto.encrypt_text("GET /?q=test HTTP/1.1\r\nHost: example.test\r\n\r\n")) for index in range(count)]
        db.add_all(items); db.commit()
        return [item.id for item in items]


def configure(runtime, production=1, test=1, limit=1):
    with runtime[2]() as db:
        config = lock_configuration(db)
        config.production, config.test = production, test
        config.server_limits = {"10.0.0.10:8000": limit}
        db.commit()


def claim(runtime, owner="fixture", purpose=None):
    with runtime[2]() as db:
        item = worker.claim_next(db, owner, 300, purpose=purpose, enforce_limits=True)
        return (item.id, item.lease_owner, item.attempt_count) if item else None


def test_admin_defaults_aliases_atomic_settings_and_audit(client, service_headers):
    assert client.get(URL).status_code == 401
    assert client.get(URL, headers=service_headers).status_code == 403
    assert client.put(URL, json={}, headers=service_headers).status_code == 403
    login_admin(client)
    with client.app.state.session_factory() as db:
        db.add_all([VLLMProfile(name=name, model_name=name, base_url="http://10.0.0.10:8000/v1", status="draft") for name in ("fixture-a", "fixture-b")])
        db.commit()
    response = client.get(URL)
    assert response.headers["cache-control"] == "no-store"
    before = response.json()
    assert before["production"] == before["test"] == 1
    assert len(before["servers"]) == 1 and len(before["servers"][0]["profiles"]) == 2
    payload = {"expected_state_token": before["state_token"], "production": 10, "test": 5,
               "servers": [{"server_key": "10.0.0.10:8000", "max_calls": 10}]}
    result = client.put(URL, json=payload)
    assert result.status_code == 200, result.text
    assert result.json()["production"] == 10
    assert client.put(URL, json=payload).status_code == 409
    with client.app.state.session_factory() as db:
        assert db.scalar(select(func.count()).select_from(AccessAudit).where(AccessAudit.action == "update_concurrency_configuration")) == 1
        assert db.scalar(select(func.count()).select_from(AgentRun)) == 0


@pytest.mark.parametrize("value", [0, 33, True, "3", 1.5, None])
def test_api_rejects_invalid_analysis_limit(client, value):
    login_admin(client)
    before = client.get(URL).json()
    assert client.put(URL, json={"expected_state_token": before["state_token"], "production": value, "test": 1, "servers": []}).status_code == 422
    assert client.get(URL).json()["state_token"] == before["state_token"]


def test_api_rejects_unregistered_server_and_duplicate(client):
    login_admin(client)
    before = client.get(URL).json()
    payload = {"expected_state_token": before["state_token"], "production": 1, "test": 1,
               "servers": [{"server_key": "unregistered:8000", "max_calls": 2}]}
    assert client.put(URL, json=payload).status_code == 422
    payload["servers"] *= 2
    assert client.put(URL, json=payload).status_code == 422


def test_server_identity_ignores_model_path_and_normalizes_ipv6():
    assert server_key(SimpleNamespace(base_url="http://[fd00:0:0::1]:8000/v1")) == "[fd00::1]:8000"
    assert server_key(SimpleNamespace(base_url="https://API.OPENAI.COM/v1")) == "api.openai.com:443"
    assert server_key(SimpleNamespace(base_url="http://10.0.0.10:8000/another")) == "10.0.0.10:8000"
    with pytest.raises(ValueError):
        server_key(SimpleNamespace(base_url="https://secret:secret@example.com/v1"))


def test_parallel_claims_obey_global_lane_limits_and_do_not_duplicate(runtime):
    seed(runtime, 10); seed(runtime, 10, "test")
    configure(runtime, production=4, test=3)
    with ThreadPoolExecutor(max_workers=12) as pool:
        results = list(pool.map(lambda index: claim(runtime, str(index)), range(20)))
    selected = [item for item in results if item]
    assert len(selected) == 7 and len({item[0] for item in selected}) == 7
    assert claim(runtime) is None
    with runtime[2]() as db:
        assert db.scalar(select(func.count()).select_from(Analysis).where(Analysis.status == "processing", Analysis.analysis_purpose == "production")) == 4
    configure(runtime, production=1, test=1)
    assert claim(runtime) is None  # Reducing limits never aborts current work.
    with runtime[2]() as db:
        db.execute(update(Analysis).where(Analysis.status == "processing").values(lease_expires_at=utcnow() - timedelta(seconds=1)))
        db.commit()
    assert claim(runtime, "recovered", "production") is not None
    assert claim(runtime, "other", "production") is None


def test_llm_cap_shared_across_threads_aliases_and_error_release(runtime):
    configure(runtime, limit=3)
    engine = runtime[1]
    active = peak = 0
    guard = Lock()
    first_wave = Event()
    def invoke(index):
        async def call():
            nonlocal active, peak
            profile = SimpleNamespace(base_url="http://10.0.0.10:8000/" + ("v1" if index % 2 else "other"))
            async with call_slot(engine, profile, timeout_seconds=5):
                with guard:
                    active += 1; peak = max(peak, active)
                    if peak == 3:
                        first_wave.set()
                try:
                    deadline = time.monotonic() + 3
                    while not first_wave.is_set():
                        assert time.monotonic() < deadline, "three slots were not admitted"
                        await asyncio.sleep(.01)
                    await asyncio.sleep(.03)
                finally:
                    with guard:
                        active -= 1
        asyncio.run(call())
    with ThreadPoolExecutor(max_workers=10) as pool:
        list(pool.map(invoke, range(10)))
    assert peak == 3 and active == 0
    assert try_reserve(engine, "10.0.0.10:8000", 1)
    with runtime[2]() as db:
        # Simulated expired process; capacity is recovered on next admission.
        db.execute(update(LLMCallSlot).values(expires_at=utcnow() - timedelta(seconds=1))); db.commit()
    token = try_reserve(engine, "10.0.0.10:8000", 1)
    release(engine, token)
    with runtime[2]() as db:
        assert db.scalar(select(func.count()).select_from(LLMCallSlot)) == 0


def test_slot_cancellation_timeout_and_revocation_while_waiting(runtime):
    engine = runtime[1]
    profile = SimpleNamespace(base_url="http://10.0.0.10:8000/v1")
    async def scenario():
        token = try_reserve(engine, server_key(profile), 10)
        revoked = False
        def check():
            if revoked:
                raise RuntimeError("revoked")
        async def queued():
            async with call_slot(engine, profile, check, timeout_seconds=.02):
                pytest.fail("revoked request was dispatched")
        task = asyncio.create_task(queued())
        await asyncio.sleep(.04)  # Queue wait must outlive the model timeout.
        assert not task.done()
        revoked = True
        with pytest.raises(RuntimeError, match="revoked"):
            await task
        release(engine, token)
        with pytest.raises(TimeoutError):
            async with call_slot(engine, profile, timeout_seconds=.01):
                await asyncio.sleep(1)
        async def cancel_active():
            async with call_slot(engine, profile, timeout_seconds=1):
                raise asyncio.CancelledError()
        with pytest.raises(asyncio.CancelledError):
            await cancel_active()
    asyncio.run(scenario())
    with runtime[2]() as db:
        assert db.scalar(select(func.count()).select_from(LLMCallSlot)) == 0


def test_heartbeat_and_fencing_prevent_old_worker_writes(runtime):
    identifier = seed(runtime, 1)[0]
    original = claim(runtime)
    monitor = AnalysisHeartbeat(runtime[1], *original, 300)
    monitor.renew(); monitor.check()
    with runtime[2]() as db:
        db.execute(update(Analysis).where(Analysis.id == identifier).values(lease_expires_at=utcnow() - timedelta(seconds=1)))
        db.commit()
    recovered = claim(runtime, "replacement")
    assert recovered[2] == original[2] + 1
    with pytest.raises(worker.WorkerExecutionError, match="analysis_lease_lost"):
        monitor.check()
    with runtime[2]() as db:
        db.info["analysis_claim"] = original
        item = db.get(Analysis, identifier)
        worker.mark_failed(db, item, RuntimeError("old worker failure"))
    with runtime[2]() as db:
        assert db.get(Analysis, identifier).lease_owner == "replacement"


def test_single_worker_process_runs_ten_real_pipelines_concurrently(runtime, monkeypatch):
    seed(runtime, 10)
    configure(runtime, production=10, limit=10)
    entered, guard, release_work, stop = [], Lock(), Event(), Event()
    original_parser = worker.parse_http_payload
    def paused_parser(payload):
        with guard:
            entered.append(True)
            if len(entered) == 10:
                release_work.set(); stop.set()
        assert release_work.wait(10), "worker did not execute ten analyses concurrently"
        return original_parser(payload)
    monkeypatch.setattr(worker, "parse_http_payload", paused_parser)
    runner = Thread(target=serve, args=(runtime[2], runtime[3], runtime[0], "fixture-worker"),
                    kwargs={"stop": stop, "install_signals": False})
    runner.start()
    try:
        runner.join(15)
    finally:
        stop.set(); release_work.set(); runner.join(5)
    assert not runner.is_alive() and len(entered) == 10
    with runtime[2]() as db:
        items = db.scalars(select(Analysis)).all()
        assert all(item.status == "completed" and item.attempt_count == 1 for item in items)
        assert db.scalar(select(func.count()).select_from(AgentRun)) == 10


def _reserve_from_process(url):
    engine = build_engine(url)
    try:
        return try_reserve(engine, "10.0.0.10:8000", 60)
    finally:
        engine.dispose()


def _claim_from_process(url):
    engine = build_engine(url)
    try:
        with build_session_factory(engine)() as db:
            item = worker.claim_next(db, "child-fixture", 300, purpose="production", enforce_limits=True)
            return item.id if item else None
    finally:
        engine.dispose()


def test_limits_are_db_wide_across_separate_processes(runtime):
    seed(runtime, 10)
    configure(runtime, production=2, limit=3)
    with ProcessPoolExecutor(max_workers=4, mp_context=multiprocessing.get_context("spawn")) as pool:
        slots = [value for value in pool.map(_reserve_from_process, [runtime[0].database_url] * 8) if value]
        claims = [value for value in pool.map(_claim_from_process, [runtime[0].database_url] * 8) if value]
    assert len(slots) == 3 and len(set(slots)) == 3
    assert len(claims) == 2 and len(set(claims)) == 2
    for token in slots:
        release(runtime[1], token)


def test_lower_server_limit_drains_and_higher_limit_admits_without_restart(runtime):
    configure(runtime, limit=3)
    key, engine = "10.0.0.10:8000", runtime[1]
    slots = [try_reserve(engine, key, 30) for _ in range(3)]
    configure(runtime, limit=1)
    assert try_reserve(engine, key, 30) is None
    for token in slots[:-1]:
        release(engine, token)
        assert try_reserve(engine, key, 30) is None
    release(engine, slots[-1])
    first = try_reserve(engine, key, 30)
    assert first
    configure(runtime, limit=2)
    second = try_reserve(engine, key, 30)
    assert second
    release(engine, first); release(engine, second)


def test_output_corrections_share_one_slot_and_release_it(runtime, monkeypatch):
    import httpx
    from app.agent.executor import execute_structured_agent
    from test_agent_openai import completion, openai_profile

    configure(runtime, limit=1)
    profile = openai_profile(provider="vllm", base_url="http://10.0.0.10:8000/v1")
    calls, order = {}, []
    client_type = httpx.AsyncClient

    async def receive(request):
        body = json.loads(request.content)
        key = next(m["content"] for m in body["messages"] if m["role"] == "user")
        calls[key] = calls.get(key, 0) + 1
        order.append(key)
        with runtime[2]() as db:
            assert db.scalar(select(func.count()).select_from(LLMCallSlot)) == 1
        await asyncio.sleep(.01)
        return httpx.Response(200, json=completion(finish_reason="stop" if calls[key] == 4 else "length"))

    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: client_type(transport=httpx.MockTransport(receive), **kwargs))

    async def run(index):
        return await execute_structured_agent(profile=profile, api_key=None, instructions="fixture",
            user_input=f"fixture-{index}", session_id=f"fixture-{index}", agent_name="waf-primary",
            egress_check=lambda: None, concurrency_engine=runtime[1])

    async def both():
        return await asyncio.gather(run(0), run(1))

    results = asyncio.run(both())
    assert all(result.succeeded for result in results)
    assert sorted(calls.values()) == [4, 4]
    assert len(set(order[:4])) == len(set(order[4:])) == 1
    assert any(result.telemetry["concurrency"]["wait_ms"] > 0 for result in results)
    with runtime[2]() as db:
        assert db.scalar(select(func.count()).select_from(LLMCallSlot)) == 0


def test_actual_moduagent_and_connection_checks_share_server_cap(runtime, monkeypatch):
    import httpx
    from app.agent.executor import execute_structured_agent
    from app.services.vllm_test_runner import run_vllm_test
    from test_agent_openai import completion, synthetic_output
    from test_provider_test_runner import SyntheticCrypto, profile_for, successful_handler

    configure(runtime, limit=3)
    profile = profile_for("vllm")
    profile.base_url = "http://10.0.0.10:8000/v1"
    active = peak = calls = 0
    guard = Lock()
    first_wave = Event()
    client_type = httpx.AsyncClient
    test_handler = successful_handler(profile)
    async def receive(request):
        nonlocal active, peak, calls
        with guard:
            active += 1; peak = max(peak, active); calls += 1
            if peak == 3:
                first_wave.set()
        try:
            deadline = time.monotonic() + 5
            while not first_wave.is_set():
                assert time.monotonic() < deadline, "three HTTP requests did not overlap"
                await asyncio.sleep(.01)
            await asyncio.sleep(.04)
            if request.method == "POST" and json.loads(request.content)["messages"][-1]["content"] == "fixture-agent-input":
                return httpx.Response(200, json=completion(json.dumps(synthetic_output())))
            return test_handler(request, 0)
        finally:
            with guard:
                active -= 1
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: client_type(transport=httpx.MockTransport(receive), **kwargs))
    def agent(index):
        return asyncio.run(execute_structured_agent(profile=profile, api_key="synthetic-key", instructions="fixture",
            user_input="fixture-agent-input", session_id=f"fixture-{index}", agent_name="waf-primary",
            egress_check=lambda: None, concurrency_engine=runtime[1]))
    def connection_check():
        return asyncio.run(run_vllm_test(profile, SyntheticCrypto(), "quick", egress_check=lambda: None, concurrency_engine=runtime[1]))
    with ThreadPoolExecutor(max_workers=11) as pool:
        check = pool.submit(connection_check)
        results = list(pool.map(agent, range(10)))
        assert check.result().passed
    assert all(result.succeeded for result in results)
    assert all(result.telemetry["concurrency"]["limited"] for result in results)
    assert any(result.telemetry["concurrency"]["wait_ms"] > 0 for result in results)
    assert peak == 3 and active == 0 and calls == 13


def test_full_profile_check_cannot_silently_ignore_server_limit(runtime, monkeypatch):
    from app.services.vllm_test_runner import run_vllm_test
    from test_agent_openai import install_http
    from test_provider_test_runner import SyntheticCrypto, profile_for, successful_handler
    profile = profile_for("vllm"); profile.base_url = "http://10.0.0.10:8000/v1"
    profile.test_concurrency = 2
    requests, _ = install_http(monkeypatch, successful_handler(profile))
    result = asyncio.run(run_vllm_test(profile, SyntheticCrypto(), "full", egress_check=lambda: None, concurrency_engine=runtime[1]))
    assert not result.passed and result.error_code == "concurrency_limit_below_test"
    assert not any(b"Concurrency test" in request.content for request in requests)


def test_running_worker_picks_up_increase_then_stops_admission_and_drains(runtime, monkeypatch):
    seed(runtime, 8)
    configure(runtime, production=1)
    stop, first, second, finish = Event(), Event(), Event(), Event()
    guard, entered = Lock(), []
    parse = worker.parse_http_payload
    def paused(payload):
        with guard:
            entered.append(True)
            (first if len(entered) == 1 else second).set()
        assert finish.wait(10)
        return parse(payload)
    monkeypatch.setattr(worker, "parse_http_payload", paused)
    runner = Thread(target=serve, args=(runtime[2], runtime[3], runtime[0], "live-config-fixture"),
                    kwargs={"stop": stop, "install_signals": False})
    runner.start()
    try:
        assert first.wait(5)
        assert not second.wait(.15)
        configure(runtime, production=2)
        assert second.wait(5), "increased limit required a restart"
    finally:
        stop.set(); finish.set(); runner.join(10)
    assert not runner.is_alive()
    with runtime[2]() as db:
        states = list(db.scalars(select(Analysis.status)))
        assert states.count("completed") == 2 and states.count("pending") == 6


def test_evaluation_storage_failure_does_not_change_completed_analysis(runtime, monkeypatch, caplog):
    # In-process Alembic tests can disable existing application loggers.
    # Restore this logger for the redaction assertion without leaking state.
    monkeypatch.setattr("app.services.worker_concurrency.logger.disabled", False)
    caplog.set_level("ERROR", logger="app.services.worker_concurrency")
    identifier = seed(runtime, 1)[0]
    claimed = claim(runtime)
    def fail(*args):
        raise ValueError("PRIVATE-EVENT-MUST-NOT-BE-LOGGED")
    monkeypatch.setattr("app.services.official_evaluations.finalize_for_analysis", fail)
    run_analysis(runtime[2], runtime[3], runtime[0], *claimed)
    with runtime[2]() as db:
        assert db.get(Analysis, identifier).status == "completed"
    assert "evaluation save failed error_type=ValueError" in caplog.text
    assert "PRIVATE-EVENT-MUST-NOT-BE-LOGGED" not in caplog.text
