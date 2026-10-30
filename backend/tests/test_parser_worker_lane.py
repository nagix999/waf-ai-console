from threading import Event
from types import SimpleNamespace

from app import worker
from app.models import ParserJob
from app.services import worker_concurrency as pool
from test_concurrency import runtime  # File-backed synthetic DB; never live services.


def test_parser_suggestion_uses_existing_administrative_worker_lane(runtime, monkeypatch):
    settings, _, factory, crypto = runtime
    settings.worker_role = "model_test"
    stop, calls = Event(), []
    with factory() as db:
        db.add(ParserJob(kind="suggestion", request_ciphertext=crypto.encrypt_text("{}"),
            encryption_key_version=crypto.key_version, created_by="fixture", summary_json={}))
        db.commit()
    def run_parser(session_factory, supplied_crypto, supplied_settings, worker_id):
        calls.append(worker_id)
        assert supplied_crypto is crypto and supplied_settings is settings
        stop.set()
    monkeypatch.setattr(pool, "run_parser_job", run_parser)
    pool.serve(factory, crypto, settings, "fixture-parser", stop=stop, install_signals=False)
    assert calls == ["fixture-parser"]


def test_parser_jobs_do_not_starve_when_model_validation_queue_is_populated(runtime, monkeypatch):
    settings, _, factory, crypto = runtime
    settings.worker_role = "model_test"
    stop, calls = Event(), []
    with factory() as db:
        db.add(ParserJob(kind="suggestion", request_ciphertext=crypto.encrypt_text("{}"),
            encryption_key_version=crypto.key_version, created_by="fixture", summary_json={}))
        db.commit()
    monkeypatch.setattr(worker, "claim_next_vllm_test", lambda *args: SimpleNamespace(id="fixture", lease_owner="owner"))
    monkeypatch.setattr(pool, "run_model_test", lambda *args: calls.append("model"))
    def parser(*args):
        calls.append("parser")
        stop.set()
    monkeypatch.setattr(pool, "run_parser_job", parser)
    pool.serve(factory, crypto, settings, "fixture-fairness", stop=stop, install_signals=False)
    assert calls[:2] == ["model", "parser"]
