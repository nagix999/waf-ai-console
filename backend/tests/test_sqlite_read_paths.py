"""Read-path regression: synthetic data, no network, raw logs or model calls."""
from collections import Counter
from contextlib import contextmanager

import pytest
from sqlalchemy import event, inspect, select
from sqlalchemy.orm import Session

from app.models import Analysis, Review, ValidationDatasetWorkingItem, ValidationDatasetWorkingState
from app.services.analysis import to_summary
from app.services.analysis_query import AnalysisFilters, find_analyses
from app.services import ground_truth_working as working
from app.validation_data_schemas import WorkingSearch
from test_validation_data import login, create_dataset, add_item
from test_ground_truth_working import save, publish, draft
from test_candidate_configurations import candidate

pytestmark = pytest.mark.usefixtures("registered_vllm_target")


@contextmanager
def reads(client):
    stats = {"sql": [], "objects": Counter()}
    def statement(_conn, _cursor, sql, _parameters, _context, _many):
        stats["sql"].append(sql)  # SQL templates only, never bindings.
    def loaded(_session, instance):
        stats["objects"][type(instance).__name__] += 1
    event.listen(client.app.state.engine, "before_cursor_execute", statement)
    event.listen(Session, "loaded_as_persistent", loaded)
    try:
        yield stats
    finally:
        event.remove(client.app.state.engine, "before_cursor_execute", statement)
        event.remove(Session, "loaded_as_persistent", loaded)


def forbid_decrypt(monkeypatch, client):
    monkeypatch.setattr(client.app.state.crypto, "decrypt_text", lambda *_: pytest.fail("list decrypted an input"))


def test_analysis_projection_does_not_load_blobs_or_review_collection(client, event_payload):
    login(client)
    from test_test_runs import direct
    run = direct(client, event_payload)
    identifier = run["items"][0]["analysis_id"]
    with client.app.state.session_factory() as db:
        db.add_all([Review(id=f"review-{i}", analysis_id=identifier, source_system="synthetic",
            external_review_id=str(i), event_id=event_payload["event_id"], decision="deferred" if i == 2 else "confirmed") for i in range(3)])
        db.commit()
    with reads(client) as stats, client.app.state.session_factory() as db:
        rows, total = find_analyses(db, AnalysisFilters())
        assert total == 1
        assert {"payload_ciphertext", "result_json", "extra_fields", "prompt_snapshot_ciphertext",
            "input_schema_snapshot_ciphertext", "execution_snapshot_ciphertext", "error_message", "reviews"} <= inspect(rows[0]).unloaded
        before = len(stats["sql"])
        assert to_summary(rows[0]).review_state == "deferred"
        assert len(stats["sql"]) == before
        assert stats["objects"]["Review"] == 0
    assert client.get(f"/api/v1/analyses/{identifier}").status_code == 200


def test_working_page_counts_and_search_match_previous_meaning(client, event_payload, monkeypatch):
    login(client)
    dataset = create_dataset(client)
    first = save(client, dataset, event_payload, case_name="Straße 'A_%'", tags=["한글", "A_%"]).json()["item"]["item_id"]
    removed = save(client, dataset, {**event_payload, "signature": "removed"}, case_name="Removed").json()["item"]["item_id"]
    publish(client, dataset)
    save(client, dataset, {**event_payload, "signature": "changed"}, item=first, case_name="Straße 'A_%'", tags=["한글", "A_%"])
    save(client, dataset, {**event_payload, "signature": "no-reference"}, verdict=None)
    save(client, dataset, {**event_payload, "signature": "excluded"}, excluded=True)
    assert client.post(f"/api/v1/validation-datasets/{dataset['id']}/working/bulk", json={
        "expected_working_revision": draft(client, dataset)["working_revision"], "action": "delete", "case_ids": [removed]}).status_code == 200
    # Independent full-read oracle retains the original semantics for all cases.
    with client.app.state.session_factory() as db:
        _, _, current = working.rows(db, dataset["id"], client.app.state.crypto)
        version = working.latest_published(db, dataset["id"])
        baseline = version.publish_metadata["cases"]
        entries = [working.item_summary(item, "added" if item.item_id not in baseline else
            "unchanged" if baseline[item.item_id]["content_hash"] == item.content_hash else "changed") for item in current]
        entries.extend(working.item_summary(working.from_snapshot(old, client.app.state.crypto, baseline[old.item_id]), "removed")
            for old in working.legacy.version_items(db, version) if old.item_id not in {item.item_id for item in current})
    forbid_decrypt(monkeypatch, client)
    for filters in ({}, {"change": "removed"}, {"state": "needs_attention"}, {"state": "excluded"},
                    {"query": "STRASSE"}, {"query": "'A_%'"}, {"query": "한글"}, {"reference_verdict": "true_positive"}, {"offset": 2, "limit": 1}):
        with reads(client) as stats:
            response = client.post(f"/api/v1/validation-datasets/{dataset['id']}/working/search", json=filters)
        assert response.status_code == 200, response.text
        result = response.json()
        query = WorkingSearch(**filters)
        expected = [e for e in entries if all(not getattr(query, k) or e[k] == getattr(query, k) for k in ("state", "change", "reference_verdict", "source_kind"))
            and (not query.query.strip() or query.query.strip().casefold() in " ".join(str(e.get(k) or "") for k in ("case_name", "test_category", "difficulty", "tags")).casefold())]
        assert result["filtered_total"] == len(expected)
        # API datetime serialization is tested elsewhere; compare all other metadata.
        for actual, wanted in zip(result["items"], expected[query.offset:query.offset + query.limit]):
            assert {k: v for k, v in actual.items() if k != "updated_at"} == {k: v for k, v in wanted.items() if k != "updated_at"}
        assert result["counts"] == {"ready": 1, "needs_attention": 1, "excluded": 1}
        assert result["changes"] == {"added": 2, "changed": 1, "removed": 1, "unchanged": 0}
        assert stats["objects"]["ValidationDatasetWorkingItem"] == stats["objects"]["ValidationDatasetItem"] == 0
        assert not any("event_ciphertext" in sql or "schema_snapshot_ciphertext" in sql or "comment_ciphertext" in sql for sql in stats["sql"])


def test_legacy_reads_cache_validation_not_plaintext_or_working_state(client, event_payload, monkeypatch):
    login(client)
    dataset = create_dataset(client)
    add_item(client, dataset, event_payload)
    first = draft(client, dataset)
    with client.app.state.session_factory() as db:
        assert db.get(ValidationDatasetWorkingState, dataset["id"]) is None
    cached = str(client.app.state.crypto._legacy_gt_validation)
    assert event_payload["payload"] not in cached and "event_ciphertext" not in cached
    forbid_decrypt(monkeypatch, client)
    with reads(client) as stats:
        second = draft(client, dataset)
    assert first == second
    assert stats["objects"]["ValidationDatasetItem"] == 0
    assert not any("event_ciphertext" in sql for sql in stats["sql"])


def test_catalog_and_home_do_not_call_full_working_documents_or_preflight(client, event_payload, monkeypatch):
    login(client)
    dataset = create_dataset(client)
    save(client, dataset, event_payload, verdict=None)
    monkeypatch.setattr(working, "document", lambda *_: pytest.fail("full working document called"))
    monkeypatch.setattr("app.services.production_configurations.preflight", lambda *_: pytest.fail("home ran promotion preflight"))
    with reads(client) as stats:
        catalog = client.post("/api/v1/validation-datasets/search", json={})
        overview = client.get("/api/v1/admin/production-configurations/overview")
    assert catalog.status_code == overview.status_code == 200
    assert catalog.json()["items"][0]["needs_attention_count"] == 1
    assert overview.json()["ground_truth_needs_attention_total"] == 1
    assert stats["objects"]["ValidationDatasetWorkingItem"] == stats["objects"]["ValidationDatasetItem"] == 0


def test_page_size_does_not_add_queries_or_materialize_whole_ground_truth(client, monkeypatch):
    import importlib.util
    from pathlib import Path
    spec = importlib.util.spec_from_file_location("read_benchmark", Path(__file__).resolve().parents[1] / "scripts/benchmark_sqlite_reads.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    manifest = module.seed(client.app, 300, [150])
    login(client)
    forbid_decrypt(monkeypatch, client)
    counts = []
    for size in (25, 50, 100):
        with reads(client) as stats:
            result = client.get(f"/api/v1/test-runs/{manifest['runs']['150']}?limit={size}")
        assert result.status_code == 200, result.text
        assert len(result.json()["items"]) == size
        assert result.json()["evaluation_summary"]["matches"] == 150
        assert result.json()["items"][0]["ground_truth_source"]["stable_case_id"] == "case-150-0"
        assert stats["objects"]["ValidationDatasetItem"] == 0
        counts.append(len(stats["sql"]))
    assert len(set(counts)) == 1
    with reads(client) as stats:
        result = client.post("/api/v1/validation-datasets/dataset-150/working/search", json={"limit": 50})
    assert result.status_code == 200 and len(result.json()["items"]) == 50
    assert stats["objects"]["ValidationDatasetWorkingItem"] == stats["objects"]["ValidationDatasetItem"] == 0
    counts = []
    for size in (25, 100):
        with reads(client) as stats:
            result = client.get(f"/api/v1/analyses?limit={size}")
        assert result.status_code == 200
        assert stats["objects"]["Review"] == 0
        counts.append(len(stats["sql"]))
    assert len(set(counts)) == 1


def test_home_hint_never_replaces_full_promotion_gate(client, event_payload, candidate, monkeypatch, registered_vllm_target):
    from app.models import VLLMProfile
    from app.services import production_configurations
    from test_production_promotion import qualified, BASE
    run = qualified(client, event_payload, candidate)
    original = production_configurations.preflight
    monkeypatch.setattr(production_configurations, "preflight", lambda *_: pytest.fail("Home called full preflight"))
    overview = client.get(BASE + "/overview")
    assert overview.status_code == 200
    assert any(row["kind"] == "promotion" for row in overview.json()["actions"])
    monkeypatch.setattr(production_configurations, "preflight", original)
    before = client.get(BASE).json()
    with client.app.state.session_factory() as db:
        db.get(VLLMProfile, candidate["primary_profile_id"]).status = "disabled"
        db.commit()
    assert not client.get(BASE + f"/preflight/{run['id']}").json()["eligible"]
    response = client.post(BASE + "/promote", json={"candidate_test_run_id": run["id"],
        "expected_production_configuration_hash": before["configuration_hash"], "acknowledge_schema_change": True})
    assert response.status_code == 409
    assert client.get(BASE).json()["snapshot"] == before["snapshot"]


def test_activity_revision_changes_on_draft_save_and_is_optional(client, event_payload):
    login(client)
    path = "/api/v1/admin/activity?include_home_revision=true"
    original = client.get(path).json()["home_revision"]
    assert client.get(path).json()["home_revision"] == original
    assert "home_revision" not in client.get("/api/v1/admin/activity").json()
    dataset = create_dataset(client)
    save(client, dataset, event_payload)
    changed = client.get(path).json()["home_revision"]
    assert changed != original and len(changed) == 64
    assert event_payload["payload"] not in client.get(path).text
