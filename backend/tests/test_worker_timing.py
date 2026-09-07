from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import moduagent
import pytest
from sqlalchemy import select

from app import worker
from app.models import AgentRun, AgentStep, Analysis, VLLMProfile
from app.services.crypto import CryptoService
from app.services.timing import analysis_timings, run_duration_ms, step_duration_ms
from test_agent_executor import FakeVLLMClient, fake_result, valid_output

pytestmark = pytest.mark.usefixtures("registered_vllm_target")


class FakeClock:
    def __init__(self):
        self.now = datetime(2026, 9, 5, tzinfo=UTC)
        self.ns = 0

    def advance(self, milliseconds):
        self.now += timedelta(milliseconds=milliseconds)
        self.ns += milliseconds * 1_000_000


@pytest.fixture
def fake_clock(monkeypatch):
    clock = FakeClock()
    monkeypatch.setattr(worker, "utcnow", lambda: clock.now)
    monkeypatch.setattr(worker.time, "perf_counter_ns", lambda: clock.ns)
    return clock


def test_analysis_elapsed_includes_queue_and_recovered_processing_span():
    created = datetime(2026, 9, 5)
    item = Analysis(created_at=created, status="pending")
    now = created.replace(tzinfo=UTC) + timedelta(seconds=10)
    assert analysis_timings(item, now) == {
        "total_elapsed_ms": 10000, "queue_wait_ms": 10000, "processing_duration_ms": None,
    }
    item.started_at = created + timedelta(seconds=2)
    item.status = "processing"
    assert analysis_timings(item, now) == {
        "total_elapsed_ms": 10000, "queue_wait_ms": 2000, "processing_duration_ms": 8000,
    }
    item.completed_at = created + timedelta(seconds=7)
    item.status = "completed"
    assert analysis_timings(item, now) == {
        "total_elapsed_ms": 7000, "queue_wait_ms": 2000, "processing_duration_ms": 5000,
    }
    item.started_at = None
    item.status = "failed"
    assert analysis_timings(item, now) == {
        "total_elapsed_ms": 7000, "queue_wait_ms": 7000, "processing_duration_ms": None,
    }


def test_legacy_and_interrupted_steps_never_claim_measured_duration():
    now = datetime(2026, 9, 5, tzinfo=UTC)
    step = AgentStep(started_at=now, completed_at=now, status="completed", metadata_json={})
    assert step_duration_ms(step, now) is None
    step.metadata_json = {"timing_measured": True, "duration_ms": 1234}
    assert step_duration_ms(step, now) == 1234
    step.metadata_json["timing_incomplete"] = True
    assert step_duration_ms(step, now) is None
    step.metadata_json = {"timing_measured": True}
    step.status = "running"
    step.completed_at = None
    assert step_duration_ms(step, now + timedelta(seconds=3)) == 3000
    assert step_duration_ms(step, now - timedelta(seconds=3)) == 0


def test_stub_parser_is_visible_while_running_and_measures_actual_work(
    client, event_payload, service_headers, settings, monkeypatch, fake_clock,
):
    item_id = client.post("/api/v1/analyses", headers=service_headers, json=event_payload).json()["id"]
    original_parser = worker.parse_http_payload

    def parser(payload):
        with client.app.state.session_factory() as observer:
            running = observer.scalar(select(AgentStep).where(AgentStep.step_type == "parser"))
            assert running.status == "running"
            assert running.completed_at is None
        fake_clock.advance(250)
        return original_parser(payload)

    monkeypatch.setattr(worker, "parse_http_payload", parser)
    with client.app.state.session_factory() as db:
        analysis = db.get(Analysis, item_id)
        analysis.created_at = fake_clock.now
        db.commit()
        fake_clock.advance(2000)
        analysis = worker.claim_next(db, "timing-worker", 300)
        worker.process_stub(db, CryptoService(settings.data_encryption_key, settings.encryption_key_version), analysis)
        run = db.scalar(select(AgentRun).where(AgentRun.analysis_id == item_id))
        assert [step.status for step in run.steps] == ["completed"] * 4
        assert step_duration_ms(run.steps[1]) == 250
        assert run_duration_ms(run) == 250
        assert analysis_timings(analysis) == {
            "total_elapsed_ms": 2250, "queue_wait_ms": 2000, "processing_duration_ms": 250,
        }
        assert analysis.severity == "UNKNOWN"
        assert analysis.threat_category == "not_analyzed"


@pytest.mark.parametrize("repair_succeeds", [True, False])
def test_primary_duration_includes_real_corrective_retry_and_grounding(
    client, event_payload, service_headers, settings, monkeypatch, fake_clock, repair_succeeds,
):
    event_payload["payload"] = "GET /?q=%27+OR+1%3D1-- HTTP/1.1\r\nHost: synthetic.internal\r\n\r\n"
    item_id = client.post("/api/v1/analyses", headers=service_headers, json=event_payload).json()["id"]
    with client.app.state.session_factory() as db:
        db.add(VLLMProfile(
            name="timing-profile", base_url="http://10.0.0.10:8000/v1", model_name="synthetic-model",
            status="production",
        ))
        db.commit()

    calls = []

    class FakeAgent:
        def __init__(self, kwargs):
            self.codec = kwargs["output"]

        def inspect(self):
            return SimpleNamespace(agent_fingerprint="synthetic-fingerprint")

        async def run(self, _input, *, session_id):
            calls.append(session_id)
            fake_clock.advance(1000 if len(calls) == 1 else 2000)
            with client.app.state.session_factory() as observer:
                step = observer.scalar(select(AgentStep).where(AgentStep.step_type == "llm_primary"))
                assert step.status == "running"
                assert step.completed_at is None
            if len(calls) == 2 and repair_succeeds:
                return fake_result(output=valid_output(), run_id="repaired")
            self.codec.validation_issues = [{"field": "threat_analysis.severity", "type": "missing"}]
            return fake_result(
                output=None, run_id=f"failed-{len(calls)}", failure_id="synthetic-failure",
                error_code="output_validation_failed",
            )

    monkeypatch.setattr(moduagent, "VLLMClient", FakeVLLMClient)
    monkeypatch.setattr(moduagent.Agent, "create", staticmethod(lambda **kwargs: FakeAgent(kwargs)))
    original_grounding = worker._ground_agent_call

    def grounding(*args):
        fake_clock.advance(250)
        return original_grounding(*args)

    monkeypatch.setattr(worker, "_ground_agent_call", grounding)
    with client.app.state.session_factory() as db:
        analysis = db.get(Analysis, item_id)
        analysis.created_at = fake_clock.now
        db.commit()
        worker.claim_next(db, "timing-worker", 300)
        crypto = CryptoService(settings.data_encryption_key, settings.encryption_key_version)
        if repair_succeeds:
            worker.process_moduagent(db, crypto, analysis, "10.0.0.10:8000", 0.75)
        else:
            with pytest.raises(worker.WorkerExecutionError) as error:
                worker.process_moduagent(db, crypto, analysis, "10.0.0.10:8000", 0.75)
            worker.mark_failed(db, analysis, error.value)
        run = db.scalar(select(AgentRun).where(AgentRun.analysis_id == item_id))
        primary_step = next(step for step in run.steps if step.step_type == "llm_primary")
        assert primary_step.metadata_json["output_validation_retry"]["attempt_count"] == 2
        assert step_duration_ms(primary_step) == (3250 if repair_succeeds else 3000)
        assert primary_step.status == ("completed" if repair_succeeds else "failed")
        assert run.status == analysis.status == ("completed" if repair_succeeds else "failed")
        assert run.completed_at is not None
        assert all(step.completed_at is not None for step in run.steps)
        if repair_succeeds:
            assert analysis.severity == "HIGH"
            assert analysis.threat_category == "sql_injection"
        else:
            assert analysis.error_code == "primary_agent_failed"
    assert len(calls) == 2


@pytest.mark.parametrize("operation_raises", [False, True])
def test_lease_recovery_preserves_first_start_and_marks_abandoned_timing_unknown(
    client, event_payload, service_headers, settings, fake_clock, operation_raises,
):
    item_id = client.post("/api/v1/analyses", headers=service_headers, json=event_payload).json()["id"]
    crypto = CryptoService(settings.data_encryption_key, settings.encryption_key_version)
    with client.app.state.session_factory() as db:
        analysis = db.get(Analysis, item_id)
        analysis.created_at = fake_clock.now
        db.commit()
        fake_clock.advance(1000)
        analysis = worker.claim_next(db, "old-worker", 1)
        with pytest.raises(worker.WorkerExecutionError) as error:
            with worker.measured_run(db, analysis) as run:
                with worker.measured_step(db, crypto, run, 1, "llm_primary", "synthetic step"):
                    fake_clock.advance(2000)
                    with client.app.state.session_factory() as recovery_db:
                        recovered = worker.claim_next(recovery_db, "recovered-worker", 300)
                        assert recovered.attempt_count == 2
                        assert analysis_timings(recovered, fake_clock.now)["queue_wait_ms"] == 1000
                    if operation_raises:
                        raise RuntimeError("synthetic request failed after lease recovery")
        assert error.value.code == "analysis_lease_lost"
        db.refresh(analysis)
        worker.mark_failed(db, analysis, error.value)
        assert analysis.status == "processing"
        assert analysis.lease_owner == "recovered-worker"
        db.refresh(run)
        assert run.status == "failed"
        assert run.completed_at is None
        assert run_duration_ms(run, fake_clock.now) is None
        assert len(run.steps) == 1
        assert run.steps[0].status == "failed"
        assert run.steps[0].completed_at is None
        assert run.steps[0].metadata_json["timing_incomplete"] is True
        assert step_duration_ms(run.steps[0], fake_clock.now) is None


def test_parser_exception_closes_step_and_its_run_without_raw_error_content(
    client, event_payload, service_headers, settings, monkeypatch, fake_clock,
):
    item_id = client.post("/api/v1/analyses", headers=service_headers, json=event_payload).json()["id"]

    def parser(_payload):
        fake_clock.advance(700)
        raise RuntimeError("synthetic secret must not appear in metadata")

    monkeypatch.setattr(worker, "parse_http_payload", parser)
    with client.app.state.session_factory() as db:
        analysis = worker.claim_next(db, "timing-worker", 300)
        with pytest.raises(RuntimeError) as error:
            worker.process_stub(db, CryptoService(settings.data_encryption_key, settings.encryption_key_version), analysis)
        worker.mark_failed(db, analysis, error.value)
        run = db.scalar(select(AgentRun).where(AgentRun.analysis_id == item_id))
        assert run.status == "failed"
        assert run.steps[-1].status == "failed"
        assert step_duration_ms(run.steps[-1]) == 700
        assert run.steps[-1].metadata_json["error_type"] == "RuntimeError"
        assert "synthetic secret" not in str(run.steps[-1].metadata_json)
        assert "synthetic secret" not in analysis.error_message


@pytest.mark.parametrize("replacement_completed", [False, True])
def test_delayed_old_claim_cannot_create_run_after_replacement_claim(
    client, event_payload, service_headers, settings, fake_clock, replacement_completed,
):
    item_id = client.post("/api/v1/analyses", headers=service_headers, json=event_payload).json()["id"]
    crypto = CryptoService(settings.data_encryption_key, settings.encryption_key_version)
    with client.app.state.session_factory() as old_db:
        old_analysis = worker.claim_next(old_db, "same-worker-id", 1)
        assert old_analysis.attempt_count == 1
        fake_clock.advance(2000)
        with client.app.state.session_factory() as new_db:
            replacement = worker.claim_next(new_db, "same-worker-id", 300)
            assert replacement.attempt_count == 2
            if replacement_completed:
                worker.process_stub(new_db, crypto, replacement)
        with pytest.raises(worker.WorkerExecutionError) as error:
            worker.process_stub(old_db, crypto, old_analysis)
        assert error.value.code == "analysis_lease_lost"
        worker.mark_failed(old_db, old_analysis, error.value)
        old_db.refresh(old_analysis)
        assert old_analysis.status == ("completed" if replacement_completed else "processing")
        assert old_analysis.attempt_count == 2
        runs = old_db.scalars(select(AgentRun).where(AgentRun.analysis_id == item_id)).all()
        assert len(runs) == (1 if replacement_completed else 0)
        if replacement_completed:
            assert runs[0].status == "completed"
