"""Expanded admissions, without starting 512 threads or contacting a model."""
from contextlib import contextmanager
from datetime import timedelta
from threading import Event
from types import SimpleNamespace

import pytest
from pydantic import ValidationError
from sqlalchemy import func, select, update

from app import worker
from app.api.agent_settings import ConcurrencyUpdate
from app.models import Analysis, VLLMProfile, utcnow
from app.services.concurrency import MAX_ANALYSES_PER_PURPOSE, MAX_SERVER_CALLS
from app.services import worker_coordinator_v239 as coordinator
from test_concurrency import URL, claim, configure, runtime, seed
from test_model_profiles import login_admin


@pytest.mark.parametrize("production,test", [(33, 1), (1, 512), (512, 512)])
def test_api_saves_expanded_limits_without_altering_other_settings(client, production, test):
    login_admin(client)
    before = client.get(URL).json()
    assert before["production"] == before["test"] == 1
    body = {"expected_state_token": before["state_token"], "production": production, "test": test, "servers": []}
    saved = client.put(URL, json=body)
    assert saved.status_code == 200, saved.text
    assert saved.headers["cache-control"] == "no-store"
    assert (saved.json()["production"], saved.json()["test"]) == (production, test)
    assert saved.json()["revision"] == before["revision"] + 1
    assert client.get(URL).json() == saved.json()
    assert client.put(URL, json=body).status_code == 409
    assert client.get(URL).json() == saved.json()


@pytest.mark.parametrize("field", ["production", "test"])
@pytest.mark.parametrize("value", [0, 513, True, False, "512", 1.5, 512.0, None])
def test_update_contract_keeps_strict_integer_bounds(field, value):
    body = {"expected_state_token": "a" * 64, "production": 1, "test": 1, "servers": [], field: value}
    with pytest.raises(ValidationError):
        ConcurrencyUpdate.model_validate(body)


def test_openapi_exposes_512_analysis_limits_but_still_64_server_calls(client):
    definitions = client.app.openapi()["components"]["schemas"]
    props = definitions["ConcurrencyUpdate"]["properties"]
    for field in ("production", "test"):
        assert props[field]["minimum"] == 1 and props[field]["maximum"] == 512
    assert definitions["ServerConcurrency"]["properties"]["max_calls"]["maximum"] == 64
    assert MAX_ANALYSES_PER_PURPOSE == 512 and MAX_SERVER_CALLS == 64


def test_expanded_analysis_limits_do_not_expand_server_call_limit(client):
    login_admin(client)
    with client.app.state.session_factory() as db:
        db.add(VLLMProfile(name="Synthetic limit", model_name="synthetic", base_url="http://10.0.0.10:8000/v1"))
        db.commit()
    before = client.get(URL).json()
    body = {"expected_state_token": before["state_token"], "production": 512, "test": 512,
            "servers": [{"server_key": before["servers"][0]["server_key"], "max_calls": 64}]}
    saved = client.put(URL, json=body)
    assert saved.status_code == 200, saved.text
    assert saved.json()["servers"][0]["max_calls"] == 64
    body["expected_state_token"] = saved.json()["state_token"]
    for value in (65, 512):
        body["servers"][0]["max_calls"] = value
        assert client.put(URL, json=body).status_code == 422
        assert client.get(URL).json() == saved.json()


@pytest.mark.parametrize("purpose", ["production", "test"])
def test_database_admission_accepts_512th_and_rejects_513th_without_parallel_load(runtime, purpose):
    configure(runtime, production=512, test=512)
    identifiers = seed(runtime, 513, purpose)
    other = "test" if purpose == "production" else "production"
    seed(runtime, 1, other)
    with runtime[2]() as db:
        db.execute(update(Analysis).where(Analysis.id.in_(identifiers[:511])).values(
            status="processing", lease_owner="synthetic-existing", attempt_count=1,
            lease_expires_at=utcnow() + timedelta(minutes=10)))
        db.commit()
    assert claim(runtime, "synthetic-512th", purpose) is not None
    assert claim(runtime, "synthetic-513th", purpose) is None
    assert claim(runtime, "synthetic-other-purpose", other) is not None
    with runtime[2]() as db:
        count = db.scalar(select(func.count()).select_from(Analysis).where(
            Analysis.analysis_purpose == purpose, Analysis.status == "processing"))
        assert count == 512
    # Lowering the allowance never cancels current owners or admits more work.
    configure(runtime, production=1, test=1)
    assert claim(runtime, "synthetic-lowered-limit", purpose) is None


def test_current_worker_pool_uses_new_capacity_without_allocating_threads(monkeypatch):
    options = []
    stop = Event()
    stop.set()
    class Pool:
        def __init__(self, **kwargs):
            options.append(kwargs)
        def __enter__(self):
            return self
        def __exit__(self, *_args):
            return False
    @contextmanager
    def sessions():
        pytest.fail("stopped worker must not access the database")
        yield
    monkeypatch.setattr(coordinator, "ThreadPoolExecutor", Pool)
    coordinator.serve(sessions, None, SimpleNamespace(worker_role="analysis"),
        "synthetic-worker", stop=stop, install_signals=False)
    assert options == [{"max_workers": 1025, "thread_name_prefix": "waf-analysis"}]
