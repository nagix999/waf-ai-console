"""Synthetic metadata/reference filters, before grouping and with no model calls."""
import uuid

import pytest
from sqlalchemy import func, select

from app.models import Analysis, AnalysisLabel, TestEvaluation as EvaluationRecord
from test_test_statistics_exports import combination_run, fixture_run
from test_candidate_configurations import candidate


def rule(field, value=None, operator="eq"):
    return {"field": field, "operator": operator, **({} if operator in ("is_empty", "is_not_empty") else {"value": value})}


def group(*rules, op="and"):
    return {"op": op, "rules": list(rules)}


def query(client, run, expression, **options):
    response = client.post(f"/api/v1/test-runs/{run['id']}/statistics/search", json={"case_filter": expression, **options})
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    return response.json()


def test_false_alarm_and_miss_filters_apply_before_every_group_and_summary(client, event_payload, monkeypatch):
    run = fixture_run(client, event_payload)
    monkeypatch.setattr(client.app.state.crypto, "decrypt_text", lambda _: pytest.fail("No raw input reads"))
    for outcome, reference, verdict in [("false_positive", "false_positive", "true_positive"), ("false_negative", "true_positive", "false_positive")]:
        expression = group(group(rule("evaluation_outcome", outcome)))
        result = query(client, run, expression, group_by="company_name,signature,waf_action", limit=1)
        assert result["case_filter_applied"] is True and result["scope_cases"] == 8
        assert result["summary"]["cases"] == result["summary"]["comparable"] == 1
        assert result["summary"]["accuracy"] == 0 and result["total_groups"] == 1
        assert result["items"][0]["cases"] == 1
        pair = query(client, run, group(group(rule("reference_verdict", reference), rule("verdict", verdict))))
        assert pair["summary"] == result["summary"]
    # Hold matches are not false alarms/misses. Hold differences can be selected separately.
    mismatch = query(client, run, group(group(*(rule("evaluation_outcome", outcome) for outcome in
        ("false_positive", "false_negative", "abstained", "expected_abstention_mismatch")), op="or")))
    assert mismatch["summary"]["cases"] == 3 and mismatch["summary"]["matches"] == 0


def test_and_or_parentheses_and_tuple_drilldown_use_the_same_cohort(client, event_payload):
    import json
    run = combination_run(client, event_payload)
    expression = group(group(rule("company_name", "Company A"), rule("company_name", "Company B"), op="or"), group(rule("waf_action", "A")))
    result = query(client, run, expression, group_by="company_name,signature,waf_action")
    assert result["summary"]["cases"] == 2 and result["total_groups"] == 2
    assert result["scope_cases"] == 8
    for item in result["items"]:
        detail = client.post(f"/api/v1/test-runs/{run['id']}/cases/search", json={"case_filter": expression,
            "group_filters": json.dumps([{"field": field, "value": value} for field, value in item["values"].items()])})
        assert detail.status_code == 200, detail.text
        assert detail.json()["total_items"] == item["cases"] == 1
        assert detail.json()["evaluation_summary"]["total"] == 8  # existing whole-scope evaluation is not rewritten
    alternate = query(client, run, group(group(rule("company_name", "Company B")), group(rule("waf_action", "A")), op="or"))
    assert alternate["summary"]["cases"] == 5
    # Table search is after grouping and must not change the filter's summary denominator.
    searched = query(client, run, expression, group_by="company_name,signature", q="SQL")
    assert searched["total_groups"] == 1 and searched["summary"]["cases"] == 2


def test_metadata_text_literal_wildcards_numbers_empty_values_and_stable_pagination(client, event_payload):
    run = fixture_run(client, event_payload)
    with client.app.state.session_factory() as db:
        row = db.get(Analysis, run["items"][0]["analysis_id"])
        row.signature, row.src_port, row.confidence_score = "SQL_%\\start", 0, 0
        db.get(Analysis, run["items"][1]["analysis_id"]).signature = "sql_%\\end"
        db.commit()
    for operator, value, count in [("contains", "_%\\", 2), ("starts_with", "SQL_%\\", 2), ("ends_with", "\\start", 1),
                                   ("not_contains", "_%\\", 3)]:
        assert query(client, run, group(rule("signature", value, operator)))["summary"]["cases"] == count
    empty = query(client, run, group(rule("signature", operator="is_empty")))
    assert empty["summary"]["cases"] == 3 and empty["summary"]["accuracy"] is None
    assert query(client, run, group(rule("src_port", 0)))["summary"]["cases"] == 1
    assert query(client, run, group(rule("src_port", 100, "gt")))["summary"]["cases"] == 7
    assert query(client, run, group(rule("confidence_score", 0)))["summary"]["cases"] == 1
    all_rows = query(client, run, group(rule("signature", operator="is_not_empty")), sort_by="value", sort_order="asc")["items"]
    pages = [query(client, run, group(rule("signature", operator="is_not_empty")), sort_by="value", sort_order="asc", limit=1, offset=i)["items"][0]
             for i in range(len(all_rows))]
    assert pages == all_rows
    zero = query(client, run, group(rule("reference_verdict", operator="is_empty"), rule("verdict", "inconclusive")))
    assert zero["summary"]["cases"] == 0 and zero["summary"]["accuracy"] is None and zero["total_groups"] == 0


def test_reference_corrections_and_saved_evaluations_do_not_mutate_while_filtering(client, event_payload):
    run = fixture_run(client, event_payload)
    saved = client.post(f"/api/v1/test-runs/{run['id']}/evaluations", json={"idempotency_key": str(uuid.uuid4())}).json()
    with client.app.state.session_factory() as db:
        row = db.get(Analysis, run["items"][1]["analysis_id"])
        db.add(AnalysisLabel(analysis_id=row.id, revision=2, verdict="true_positive", source_kind="reference", source_ref="synthetic-correction",
            ai_visible=False, created_by="fixture", attachment_id=str(uuid.uuid4()), token_digest="c" * 64))
        db.commit()
        counts = tuple(db.scalar(select(func.count()).select_from(model)) for model in (Analysis, AnalysisLabel, EvaluationRecord))
    expression = group(rule("evaluation_outcome", "false_positive"))
    assert query(client, run, expression)["summary"]["cases"] == 0
    assert query(client, run, expression, reference_basis="initial")["summary"]["cases"] == 1
    assert query(client, run, expression, evaluation_id=saved["id"])["summary"]["cases"] == 1
    assert client.post(f"/api/v1/test-runs/{run['id']}/statistics/search", json={"case_filter": expression, "evaluation_id": str(uuid.uuid4())}).status_code == 404
    with client.app.state.session_factory() as db:
        assert counts == tuple(db.scalar(select(func.count()).select_from(model)) for model in (Analysis, AnalysisLabel, EvaluationRecord))


@pytest.mark.parametrize("expression", [True, [], {}, group(), group(rule("payload", "private")), group(rule("result_json", "x")),
    group(rule("verdict", "true_positive", "contains")), group(rule("reference_verdict", "invalid")), group(rule("company_name", "x", "regex")),
    group(rule("src_port", True)), group(rule("src_port", 1.2)), group(rule("src_port", 65536)), group(rule("src_port", "10")),
    group(rule("src_port", 10 ** 100)), group(rule("confidence_score", 2)), group(rule("signature", "")), group(rule("signature", "x" * 4097)),
    group({"field": "signature", "operator": "is_empty", "value": "x"}), group(group(group(rule("signature", "SQL")))),
    group(*(rule("company_name", "x") for _ in range(21))), group(*(group(rule("signature", "x")) for _ in range(6))),
    {"op": "and", "rules": [rule("signature", "SQL")], "extra": True}, group({**rule("signature", "SQL"), "sql": "1=1"}),
    group(*(rule("company_name", "가" * 4096) for _ in range(3)))])
def test_invalid_expressions_fail_with_safe_code(client, event_payload, expression):
    run = fixture_run(client, event_payload)
    for suffix in ("statistics/search", "cases/search"):
        response = client.post(f"/api/v1/test-runs/{run['id']}/{suffix}", json={"case_filter": expression})
        assert response.status_code == 422 and response.json()["detail"] == "invalid_test_case_filter"


@pytest.mark.parametrize("suffix", ["statistics/search", "cases/search"])
def test_filtered_reads_are_admin_only_and_reject_extra_options(client, event_payload, service_headers, suffix):
    from test_test_runs import login
    run = fixture_run(client, event_payload)
    expression = group(rule("verdict", "true_positive"))
    path = f"/api/v1/test-runs/{run['id']}/{suffix}"
    assert client.post(path, json={"case_filter": expression, "unknown": "x"}).status_code == 422
    assert client.post(path + "?group_by=signature", json={"case_filter": expression}).status_code == 422
    client.cookies.clear()
    assert client.post(path, json={"case_filter": expression}).status_code == 401
    assert client.post(path, json={"case_filter": expression}, headers=service_headers).status_code == 403
    login(client)
    assert client.post(f"/api/v1/test-runs/{uuid.uuid4()}/{suffix}", json={"case_filter": expression}).status_code == 404


def test_filters_follow_successful_retry_but_saved_record_keeps_failed_attempt(client, event_payload, registered_vllm_target):
    from test_test_retries import fixture_run as failed_run, finish
    from test_analysis_retries_keys import retry
    run, _ = failed_run(client, event_payload, count=1)
    saved = client.post(f"/api/v1/test-runs/{run['id']}/evaluations", json={"idempotency_key": str(uuid.uuid4())}).json()
    child = retry(client, run["items"][0]["analysis_id"]).json()["analysis_id"]
    finish(client, child, "true_positive")
    expression = group(rule("verdict", "true_positive"))
    assert query(client, run, expression)["summary"]["matches"] == 1
    assert query(client, run, expression, evaluation_id=saved["id"])["summary"]["cases"] == 0


def test_or_filters_cannot_escape_test_membership_or_fixed_difficulty_scope(client, event_payload):
    run = fixture_run(client, event_payload)
    other = fixture_run(client, event_payload)
    expression = group(group(rule("company_name", "Example Corp")), group(rule("verdict", "true_positive")), op="or")
    assert query(client, run, expression)["summary"]["cases"] == 8
    assert query(client, run, expression, difficulty="hard")["summary"]["cases"] == 4
    assert query(client, other, expression, difficulty="easy")["summary"]["cases"] == 4


@pytest.mark.usefixtures("registered_vllm_target")
def test_official_filters_use_fixed_expected_verdict_not_later_manual_reference(client, event_payload, candidate):
    from test_official_evaluations import approved_item, execute
    from test_validation_data import create_dataset
    from test_test_retries import finish
    dataset = create_dataset(client)
    approved_item(client, dataset, {**event_payload, "vendor_score": 2}, "false_positive")
    run = execute(client, dataset, candidate)
    finish(client, run["items"][0]["analysis_id"], "true_positive")
    with client.app.state.session_factory() as db:
        row = db.get(Analysis, run["items"][0]["analysis_id"])
        db.add(AnalysisLabel(analysis_id=row.id, revision=2, verdict="true_positive", source_kind="reference", source_ref="synthetic-correction",
            ai_visible=False, created_by="fixture", attachment_id=str(uuid.uuid4()), token_digest="d" * 64))
        db.commit()
    result = query(client, run, group(rule("evaluation_outcome", "false_positive")), reference_basis="latest")
    assert result["reference_basis"] == "ground_truth" and result["summary"]["cases"] == result["summary"]["false_positives"] == 1
