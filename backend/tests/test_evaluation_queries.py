import pytest

from app.models import Analysis
from test_evaluation_labels import confirm, preview, seed
from test_model_profiles import login_admin


@pytest.mark.parametrize("reference,actual,outcome", [
    ("true_positive", "true_positive", "match"),
    ("true_positive", "false_positive", "false_negative"),
    ("true_positive", "inconclusive", "abstained"),
    ("false_positive", "true_positive", "false_positive"),
    ("false_positive", "false_positive", "match"),
    ("false_positive", "inconclusive", "abstained"),
    ("inconclusive", "true_positive", "expected_abstention_mismatch"),
    ("inconclusive", "false_positive", "expected_abstention_mismatch"),
    ("inconclusive", "inconclusive", "expected_abstention_match"),
])
def test_full_three_way_matrix_uses_final_not_primary(client, event_payload, service_headers, reference, actual, outcome):
    result = {"verdict": actual, "agent": {"framework": "moduagent"}, "primary": {"verdict": "false_positive"}}
    analysis_id = seed(client, event_payload, service_headers, verdict=actual, result_json=result)
    login_admin(client)
    assert confirm(client, preview(client, [{"event_id": "label-event", "expected_verdict": reference}])).status_code == 200
    detail = client.get(f"/api/v1/analyses/{analysis_id}").json()
    assert detail["evaluation"]["outcome"] == outcome
    listed = client.get("/api/v1/analyses", params={"evaluation_outcome": outcome}).json()
    assert listed["total"] == 1
    assert listed["items"][0]["evaluation"] == detail["evaluation"]
    summary = listed["evaluation_summary"]
    assert summary["evaluable"] == 1 and summary["outcomes"][outcome] == 1
    assert summary["matches"] == int(outcome in {"match", "expected_abstention_match"})
    cell = {
        ("true_positive", "true_positive"): "tp", ("true_positive", "false_positive"): "fn",
        ("false_positive", "true_positive"): "fp", ("false_positive", "false_positive"): "tn",
        ("true_positive", "inconclusive"): "abstained_positive",
        ("false_positive", "inconclusive"): "abstained_negative",
    }.get((reference, actual))
    expected_matrix = dict.fromkeys(("tp", "fn", "fp", "tn", "abstained_positive", "abstained_negative"), 0)
    if cell:
        expected_matrix[cell] = 1
    assert summary["confusion_matrix"] == expected_matrix
    assert summary["source_groups"][0]["confusion_matrix"] == expected_matrix
    assert summary["metrics"] == summary["source_groups"][0]["metrics"]
    assert summary["binary_evaluable"] == int(reference != "inconclusive")
    assert summary["label_coverage"] == 1


@pytest.mark.parametrize("changes,outcome", [
    ({"status": "pending"}, "pending"),
    ({"status": "processing"}, "pending"),
    ({"status": "failed"}, "failed"),
    ({"model_profile": "stub-no-llm"}, "stub"),
    ({"prompt_version": "stub-v0"}, "stub"),
    ({"result_json": {"verdict": "true_positive", "agent": {"framework": "moduagent"}, "policy": {"prompt_version": "stub-v0"}}}, "stub"),
    ({"result_json": {"verdict": "true_positive", "agent": {"framework": "stub"}}}, "stub"),
    ({"result_json": {"verdict": "true_positive", "agent": {"framework": "moduagent", "execution": "stub"}}}, "stub"),
    ({"result_json": {"verdict": "true_positive", "agent": {"framework": "moduagent", "llm_called": False}}}, "stub"),
    ({"result_json": None}, "unknown_provenance"),
    ({"result_json": []}, "unknown_provenance"),
    ({"result_json": "synthetic-not-object"}, "unknown_provenance"),
    ({"result_json": {"verdict": "true_positive", "agent": None}}, "unknown_provenance"),
    ({"result_json": {"verdict": "true_positive", "agent": []}}, "unknown_provenance"),
    ({"result_json": {"verdict": "true_positive", "agent": {"framework": "other"}}}, "unknown_provenance"),
    ({"result_json": {"verdict": "false_positive", "agent": {"framework": "moduagent"}}}, "unknown_provenance"),
    ({"result_json": {"verdict": "invalid", "agent": {"framework": "moduagent"}}}, "unknown_provenance"),
    ({"completed_at": None}, "unknown_provenance"),
    ({"extra_fields": {"expected_verdict": "true_positive"}}, "input_contaminated"),
    ({"extra_fields": {"label": None}}, "input_contaminated"),
])
def test_noncomparable_provenance_never_counts_as_correct(client, event_payload, service_headers, changes, outcome):
    analysis_id = seed(client, event_payload, service_headers, **changes)
    login_admin(client)
    assert confirm(client, preview(client)).status_code == 200
    detail = client.get(f"/api/v1/analyses/{analysis_id}").json()
    assert detail["evaluation"]["outcome"] == outcome
    if "result_json" in changes and not isinstance(changes["result_json"], dict):
        assert detail["result"] is None
        with client.app.state.session_factory() as db:
            assert db.get(Analysis, analysis_id).result_json == changes["result_json"]
    listed = client.get("/api/v1/analyses", params={"evaluation_outcome": outcome}).json()
    assert listed["total"] == 1
    summary = listed["evaluation_summary"]
    assert summary["labeled"] == 1 and summary["evaluable"] == summary["matches"] == 0
    assert summary["outcomes"][outcome] == 1
    assert summary["binary_evaluable"] == summary["binary_decided"] == summary["binary_correct"] == 0
    assert not any(summary["confusion_matrix"].values())
    assert all(value is None for key, value in summary["metrics"].items() if key != "basis")


def test_absence_of_label_is_not_an_error_and_legacy_data_is_not_rewritten(client, event_payload, service_headers):
    changes = {"extra_fields": {"label": "synthetic-legacy"}, "model_profile": "stub-no-llm"}
    analysis_id = seed(client, event_payload, service_headers, **changes)
    response = client.get(f"/api/v1/analyses/{analysis_id}", headers=service_headers)
    assert response.json()["evaluation"] == {"outcome": "unlabeled", "reference_label": None}
    with client.app.state.session_factory() as db:
        assert db.get(Analysis, analysis_id).extra_fields == changes["extra_fields"]


def test_aggregate_uses_full_filtered_scope_and_separates_reference_sources(client, event_payload, service_headers):
    specifications = [
        ("m", "true_positive", "true_positive", {}),
        ("fn", "false_positive", "true_positive", {}),
        ("fp", "true_positive", "false_positive", {}),
        ("abs", "inconclusive", "true_positive", {}),
        ("em", "inconclusive", "inconclusive", {}),
        ("ex", "true_positive", "inconclusive", {}),
        ("stub", "inconclusive", "true_positive", {"model_profile": "stub-no-llm"}),
        ("pending", "true_positive", "true_positive", {"status": "pending"}),
        ("failed", "true_positive", "true_positive", {"status": "failed"}),
        ("unknown", "true_positive", "true_positive", {"result_json": None}),
        ("contaminated", "true_positive", "true_positive", {"extra_fields": {"label": "synthetic"}}),
    ]
    ids = {}
    for event_id, actual, reference, changes in specifications:
        ids[event_id] = seed(client, event_payload, service_headers, event_id=event_id, verdict=actual, **changes)
    seed(client, event_payload, service_headers, event_id="unlabeled")
    other_id = seed(client, event_payload, service_headers, event_id="other")
    with client.app.state.session_factory() as db:
        db.get(Analysis, other_id).source_system = "other-source"
        db.commit()
    login_admin(client)
    for source_kind, names, visibility in (
        ("synthetic_expected", [row[0] for row in specifications if row[0] not in {"fp", "abs"}], "unknown"),
        ("reference", ["fp", "abs"], "false"),
    ):
        answers = [{"event_id": event_id, "expected_verdict": reference} for event_id, _, reference, _ in specifications if event_id in names]
        assert confirm(client, preview(client, answers, source_kind=source_kind, ai_visible=visibility)).status_code == 200
    client.post("/api/v1/auth/logout")
    body = client.get("/api/v1/analyses", params={"limit": 1, "offset": 2}, headers=service_headers).json()
    assert body["total"] == 12 and len(body["items"]) == 1
    summary = body["evaluation_summary"]
    assert (summary["total"], summary["labeled"], summary["evaluable"], summary["matches"]) == (12, 11, 6, 2)
    assert sum(summary["outcomes"].values()) == 12
    assert sum(row["binary_evaluable"] for row in summary["source_groups"]) == 4
    assert sum(row["binary_decided"] for row in summary["source_groups"]) == 3
    assert sum(row["binary_correct"] for row in summary["source_groups"]) == 1
    assert summary["confusion_matrix"] == {"tp": 1, "fn": 1, "fp": 1, "tn": 0, "abstained_positive": 1, "abstained_negative": 0}
    assert (summary["binary_evaluable"], summary["binary_decided"], summary["binary_correct"]) == (4, 3, 1)
    assert (summary["support_positive"], summary["support_negative"]) == (3, 1)
    assert (summary["decided_support_positive"], summary["decided_support_negative"]) == (2, 1)
    assert summary["label_coverage"] == pytest.approx(11 / 12)
    assert summary["metrics"]["accuracy"] == pytest.approx(1 / 3)
    assert summary["metrics"]["coverage"] == .75
    assert summary["metrics"]["overall_binary_correct_rate"] == .25
    groups = {(row["source_kind"], row["ai_visible"]): row for row in summary["source_groups"]}
    assert groups[("synthetic_expected", None)]["evaluable"] == 4
    assert groups[("reference", False)]["evaluable"] == 2
    for params, total in [
        ({"label_presence": "unlabeled"}, 1), ({"label_presence": "labeled"}, 11),
        ({"label_source_kind": "reference"}, 2), ({"label_ai_visible": "unknown"}, 9),
        ({"label_ai_visible": "false"}, 2), ({"label_ai_visible": "true"}, 0),
        ({"reference_label": "inconclusive"}, 2), ({"label_source_ref": "synthetic-v1"}, 11),
        ({"label_source_ref": "synthetic"}, 0), ({"evaluation_outcome": "abstained"}, 1),
        ({"company_name": "missing"}, 0),
    ]:
        filtered = client.get("/api/v1/analyses", params={**params, "limit": 1}, headers=service_headers).json()
        assert filtered["total"] == filtered["evaluation_summary"]["total"] == total
        filtered_summary = filtered["evaluation_summary"]
        assert sum(filtered_summary["confusion_matrix"].values()) == filtered_summary["binary_evaluable"]
    assert client.get(f"/api/v1/analyses/{other_id}", headers=service_headers).status_code == 404


@pytest.mark.parametrize("params", [
    {"label_presence": "yes"}, {"evaluation_outcome": "correct"}, {"reference_label": "deferred"},
    {"label_source_kind": "gold"}, {"label_ai_visible": "no"},
])
def test_invalid_evaluation_filters_are_rejected(client, service_headers, params):
    assert client.get("/api/v1/analyses", params=params, headers=service_headers).status_code == 422
