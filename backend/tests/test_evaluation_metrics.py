"""Deterministic synthetic confusion counts; no worker, network or LLM calls."""
import math

import pytest
from sqlalchemy import select

from app.evaluation_schemas import EvaluationConfusionMatrix, EvaluationSummary
from app.models import Analysis, AnalysisLabel
from app.services.evaluation import calculate_binary_metrics, evaluation_relation, summarize_evaluations
from test_evaluation_labels import confirm, preview, seed
from test_model_profiles import login_admin


def metrics(**counts):
    return calculate_binary_metrics(EvaluationConfusionMatrix(**counts)).model_dump()


def test_all_formulas_use_binary_decisions_and_keep_abstentions_in_coverage():
    result = metrics(tp=18, fn=2, fp=3, tn=27, abstained_positive=4, abstained_negative=6)
    expected = {
        "accuracy": 45 / 50, "precision": 18 / 21, "recall": 18 / 20,
        "f1": 36 / 41, "specificity": 27 / 30,
        "false_positive_rate": 3 / 30, "false_negative_rate": 2 / 20,
        "balanced_accuracy": .9, "macro_f1": ((36 / 41) + (54 / 59)) / 2,
        "mcc": (18 * 27 - 3 * 2) / math.sqrt(21 * 20 * 30 * 29),
        "coverage": 50 / 60, "abstention_rate": 10 / 60,
        "overall_binary_correct_rate": 45 / 60,
    }
    assert result.pop("basis") == "decided_binary"
    assert result == pytest.approx(expected)


@pytest.mark.parametrize("counts,expected", [
    ({"tp": 2, "tn": 3}, {"accuracy": 1, "precision": 1, "recall": 1, "f1": 1,
                           "specificity": 1, "balanced_accuracy": 1, "macro_f1": 1, "mcc": 1}),
    ({"fn": 2, "fp": 3}, {"accuracy": 0, "precision": 0, "recall": 0, "f1": 0,
                           "specificity": 0, "balanced_accuracy": 0, "macro_f1": 0, "mcc": -1}),
    ({"tp": 2}, {"accuracy": 1, "precision": 1, "recall": 1, "f1": 1,
                 "specificity": None, "false_positive_rate": None, "false_negative_rate": 0,
                 "balanced_accuracy": None, "macro_f1": None, "mcc": None}),
    ({"tn": 2}, {"accuracy": 1, "precision": None, "recall": None, "f1": None,
                 "specificity": 1, "false_positive_rate": 0, "false_negative_rate": None,
                 "balanced_accuracy": None, "macro_f1": None, "mcc": None}),
    ({"tp": 1, "fn": 1}, {"accuracy": .5, "recall": .5, "f1": 2 / 3,
                           "specificity": None, "balanced_accuracy": None, "macro_f1": None, "mcc": None}),
    ({"fn": 2, "tn": 3}, {"accuracy": .6, "precision": None, "recall": 0, "f1": 0,
                           "specificity": 1, "balanced_accuracy": .5, "macro_f1": .375, "mcc": None}),
    ({"fp": 2, "tn": 3}, {"accuracy": .6, "precision": 0, "recall": None, "f1": 0,
                           "specificity": .6, "false_positive_rate": .4,
                           "balanced_accuracy": None, "macro_f1": None, "mcc": None}),
])
def test_class_support_and_zero_denominator_policy(counts, expected):
    result = metrics(**counts)
    for name, value in expected.items():
        if value is None:
            assert result[name] is None
        else:
            assert result[name] == pytest.approx(value)
    assert result["coverage"] == 1 and result["abstention_rate"] == 0


def test_empty_metrics_are_null_not_zero_or_a_perfect_score():
    result = metrics()
    assert result.pop("basis") == "decided_binary"
    assert all(value is None for value in result.values())
    assert EvaluationSummary().label_coverage is None


def test_all_abstained_has_no_decision_scores_but_reports_zero_coverage():
    result = metrics(abstained_positive=3, abstained_negative=7)
    assert result.pop("basis") == "decided_binary"
    assert result.pop("coverage") == 0
    assert result.pop("abstention_rate") == 1
    assert result.pop("overall_binary_correct_rate") == 0
    assert all(value is None for value in result.values())


def test_aggregate_pools_counts_not_source_percentages_and_keeps_ai_visibility(client, event_payload, service_headers):
    specifications = [
        ("independent-tp", "true_positive", "true_positive", "reference", False),
        ("independent-fn", "false_positive", "true_positive", "reference", False),
        ("assisted-tp", "true_positive", "true_positive", "reference", True),
        ("unknown-fp", "true_positive", "false_positive", "reference", None),
        ("synthetic-tn", "false_positive", "false_positive", "synthetic_expected", False),
        ("synthetic-negative-hold", "inconclusive", "false_positive", "synthetic_expected", False),
        ("expected-hold", "inconclusive", "inconclusive", "synthetic_expected", False),
    ]
    for name, actual, _reference, _kind, _visible in specifications:
        seed(client, event_payload, service_headers, event_id=name, verdict=actual)
    login_admin(client)
    for name, _actual, reference, kind, visible in specifications:
        attached = confirm(client, preview(client, [{"event_id": name, "expected_verdict": reference}],
            source_kind=kind, ai_visible="unknown" if visible is None else str(visible).lower()))
        assert attached.status_code == 200
    summary = client.get("/api/v1/analyses", params={"limit": 1}).json()["evaluation_summary"]
    assert summary["total"] == summary["labeled"] == summary["evaluable"] == 7
    assert summary["binary_evaluable"] == 6 and summary["binary_decided"] == 5
    assert summary["confusion_matrix"] == {"tp": 2, "fn": 1, "fp": 1, "tn": 1, "abstained_positive": 0, "abstained_negative": 1}
    assert summary["metrics"]["accuracy"] == .6
    assert summary["metrics"]["coverage"] == pytest.approx(5 / 6)
    assert summary["metrics"]["overall_binary_correct_rate"] == .5
    groups = {(item["source_kind"], item["ai_visible"]): item for item in summary["source_groups"]}
    assert len(groups) == 4
    assert groups[("reference", False)]["metrics"]["accuracy"] == .5
    assert groups[("reference", True)]["metrics"]["accuracy"] == 1
    assert groups[("reference", None)]["metrics"]["accuracy"] == 0
    synthetic = groups[("synthetic_expected", False)]
    assert synthetic["metrics"]["accuracy"] == 1
    assert synthetic["metrics"]["coverage"] == .5
    assert synthetic["expected_abstention_matches"] == 1
    assert synthetic["support_negative"] == 2 and synthetic["decided_support_negative"] == 1
    assert all("label_coverage" not in group for group in groups.values())


def test_fixed_reference_relation_preserves_score_after_latest_label_correction(client, event_payload, service_headers):
    analysis_id = seed(client, event_payload, service_headers)
    login_admin(client)
    attachment = confirm(client, preview(client)).json()["attachment_id"]
    corrected = confirm(client, preview(client, [{"event_id": "label-event", "expected_verdict": "false_positive"}]))
    assert corrected.status_code == 200
    with client.app.state.session_factory() as db:
        references = select(AnalysisLabel).where(AnalysisLabel.attachment_id == attachment).subquery()
        frozen = summarize_evaluations(db, evaluation_relation(references), [Analysis.id == analysis_id])
        latest = summarize_evaluations(db, evaluation_relation(), [Analysis.id == analysis_id])
    assert frozen.confusion_matrix.tp == 1 and frozen.metrics.accuracy == 1
    assert latest.confusion_matrix.fp == 1 and latest.metrics.accuracy == 0


def test_unlabeled_and_empty_searches_have_no_binary_scores(client, event_payload, service_headers):
    seed(client, event_payload, service_headers)
    for params, total, label_coverage in (({}, 1, 0), ({"company_name": "nonexistent"}, 0, None)):
        response = client.get("/api/v1/analyses", params=params, headers=service_headers)
        assert response.status_code == 200
        summary = response.json()["evaluation_summary"]
        assert summary["total"] == total and summary["label_coverage"] == label_coverage
        assert summary["source_groups"] == []
        assert summary["binary_evaluable"] == 0 and not any(summary["confusion_matrix"].values())
        assert all(value is None for key, value in summary["metrics"].items() if key != "basis")
