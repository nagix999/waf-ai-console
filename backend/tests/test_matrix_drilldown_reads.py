"""Synthetic confusion-matrix paging: exact meaning and bounded SQL work."""
import re

from app.models import Analysis, utcnow
from test_test_runs import login, upload
from test_sqlite_read_paths import reads, forbid_decrypt

CELLS = [
    ("tp", "true_positive", "true_positive", "match"), ("fn", "true_positive", "false_positive", "false_negative"),
    ("fp", "false_positive", "true_positive", "false_positive"), ("tn", "false_positive", "false_positive", "match"),
    ("abstained_positive", "true_positive", "inconclusive", "abstained"), ("abstained_negative", "false_positive", "inconclusive", "abstained"),
    ("expected_hold_positive", "inconclusive", "true_positive", "expected_abstention_mismatch"),
    ("expected_hold_negative", "inconclusive", "false_positive", "expected_abstention_mismatch"),
    ("expected_hold_match", "inconclusive", "inconclusive", "expected_abstention_match"),
]


def matrix_run(client, event_payload):
    login(client)
    pairs = [(reference, prediction, "completed") for _, reference, prediction, _ in CELLS]
    pairs += [("true_positive", "true_positive", "stub"), (None, "true_positive", "completed"),
              ("true_positive", None, "failed"), ("false_positive", None, "pending")]
    events = [{**event_payload, "event_id": f"matrix-synthetic-{i}", "expected_verdict": reference,
        "difficulty": "hard" if i % 2 == 0 else "easy", "case_name": f"Case {i:02d}"} for i, (reference, _, _) in enumerate(pairs)]
    run = upload(client, [*events, events[0], {**event_payload, "event_id": "invalid", "src_ip": ""}])
    with client.app.state.session_factory() as db:
        for item, (_, prediction, status) in zip(run["items"], pairs):
            row = db.get(Analysis, item["analysis_id"])
            row.status = "completed" if status == "stub" else status
            row.verdict, row.model_profile, row.prompt_version = prediction, "synthetic-offline", "fixture-instructions"
            row.completed_at = utcnow() if row.status == "completed" else None
            row.result_json = {"verdict": prediction, "agent": {"framework": "stub" if status == "stub" else "moduagent"}, "synthetic_padding": "x" * 8192}
        db.commit()
    return run


def test_all_nine_cells_return_only_their_cases_without_changing_metrics(client, event_payload, monkeypatch):
    run = matrix_run(client, event_payload)
    path = f"/api/v1/test-runs/{run['id']}"
    baseline = client.get(path, params={"reference_basis": "latest", "limit": 200}).json()
    forbid_decrypt(monkeypatch, client)
    for key, reference, prediction, outcome in CELLS:
        response = client.get(path, params={"reference_basis": "latest", "reference_verdict": reference,
            "verdict": prediction, "evaluation_outcome": outcome, "limit": 25})
        assert response.status_code == 200, response.text
        result = response.json()
        assert result["total_items"] == baseline["evaluation_summary"]["confusion_matrix"][key] == 1
        assert result["evaluation_summary"] == baseline["evaluation_summary"]
        assert result["items"][0]["verdict"] == prediction
        assert result["items"][0]["evaluation"]["reference_label"]["verdict"] == reference
        assert result["items"][0]["evaluation"]["outcome"] == outcome
    assert baseline["duplicates"] == baseline["rejected"] == 1


def test_empty_and_out_of_range_pages_keep_exact_total_and_sort(client, event_payload):
    run = matrix_run(client, event_payload)
    path = f"/api/v1/test-runs/{run['id']}"
    base = client.get(path, params={"sort_by": "case_name", "sort_order": "desc", "limit": 200}).json()
    pages = [client.get(path, params={"sort_by": "case_name", "sort_order": "desc", "limit": 1, "offset": i}).json()
             for i in range(base["total_items"])]
    assert [page["items"][0] for page in pages] == base["items"]
    empty = client.get(path, params={"evaluation_outcome": "false_negative", "reference_verdict": "false_positive"}).json()
    assert empty["items"] == [] and empty["total_items"] == 0
    beyond = client.get(path, params={"offset": 9999}).json()
    assert beyond["items"] == [] and beyond["total_items"] == base["total_items"]
    assert beyond["evaluation_summary"] == base["evaluation_summary"]


def test_matrix_count_and_page_share_one_thin_materialized_query(client, event_payload, monkeypatch):
    run = matrix_run(client, event_payload)
    forbid_decrypt(monkeypatch, client)
    with reads(client) as work:
        response = client.get(f"/api/v1/test-runs/{run['id']}", params={"reference_basis": "latest",
            "reference_verdict": "true_positive", "verdict": "false_positive", "evaluation_outcome": "false_negative"})
    assert response.status_code == 200
    page_statements = [sql for sql in work["sql"] if "test_read_matches AS MATERIALIZED" in sql]
    assert len(page_statements) == 1  # count + page, not two comparison evaluations
    statement = page_statements[0]
    assert statement.count("test_read_evaluation AS MATERIALIZED") == 1
    assert "test_read_current AS MATERIALIZED" in statement and "filtered_total" in statement
    projection = re.search(r"test_read_matches AS MATERIALIZED\s*\(\s*(SELECT .*?)\nFROM", statement, re.S)[1]
    assert not any(name in projection for name in ("payload_ciphertext", "result_json", "extra_fields", "prompt_snapshot_ciphertext", "input_schema_snapshot_ciphertext"))
    assert work["objects"]["Analysis"] == work["objects"]["TestRunItem"] == 1
    assert work["objects"]["ValidationDatasetItem"] == work["objects"]["AnalysisLabel"] == 0
