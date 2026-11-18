"""Synthetic match diagnostics; runtime selection and private data stay intact."""
from copy import deepcopy
import json
from types import SimpleNamespace

import pytest

from app.services import parser_match_diagnostics as match_diagnostics
from app.services import parser_rule_engine as engine
from app.services import parser_workbench_jobs as jobs
from app.services.parser_workbench_diagnostics import WorkbenchOutputError
from test_parser_rule_engine import bundle, rule
from test_parser_workbench import PREFIX, create, discovery_stub, login, profile, proposal, raw, run, sources
from test_parser_workbench_diagnostics import confirmed_session, envelope, finish_generation, wire


PRIVATE = "SYNTHETIC_PRIVATE_PAYLOAD_OR_CONDITION_DO_NOT_PERSIST"
pytestmark = pytest.mark.usefixtures("registered_vllm_target")


def diagnose(payload, predicates, *, mode="all"):
    definition = proposal()
    definition["match"] = {mode: predicates}
    return match_diagnostics.diagnose_rule_match(payload, definition)


def test_json_wrapped_request_is_not_mistaken_for_a_raw_http_prefix():
    payload = raw("/synthetic")
    checks = diagnose(payload, [{"kind": "starts_with", "value": "GET "}])
    assert checks == [{"condition_index": 0, "field": "match.all.0", "kind": "starts_with",
                       "matched": False, "code": "parser_prefix_mismatch"}]


@pytest.mark.parametrize(("payload", "prefix", "matched"), [
    ("GET /synthetic HTTP/1.1\r\n\r\n", "GET ", True),
    ("GET /synthetic HTTP/1.1\r\n\r\n", "get ", False),
    (" GET /synthetic HTTP/1.1\r\n\r\n", "GET ", False),
    (r"HEADER\nGET /synthetic", "HEADER\n", False),
    (r"HEADER\nGET /synthetic", r"HEADER\n", True),
])
def test_prefix_uses_exact_original_start_case_and_line_ending_representation(payload, prefix, matched):
    checks = diagnose(payload, [{"kind": "starts_with", "value": prefix}])
    assert checks[0]["matched"] is matched
    assert checks[0]["code"] == ("ok" if matched else "parser_prefix_mismatch")


@pytest.mark.parametrize("mode", ["all", "any"])
def test_diagnosis_does_not_relax_all_or_any_runtime_semantics(mode):
    payload = "GET /synthetic HTTP/1.1\r\nHost: example.invalid\r\n\r\n"
    definition = proposal()
    definition["match"] = {mode: [{"kind": "starts_with", "value": "POST "},
                                  {"kind": "starts_with", "value": "GET "}]}
    definition["extract"] = {"kind": "raw_payload"}
    before = deepcopy(definition)
    selected = engine.parse_rule_bundle(payload, bundle(rule(vendor="SyntheticVendor", definition=definition)),
                                        waf_vendor="SyntheticVendor")
    checks = match_diagnostics.diagnose_rule_match(payload, definition)
    assert [item["matched"] for item in checks] == [False, True]
    assert [item["field"] for item in checks] == [f"match.{mode}.0", f"match.{mode}.1"]
    assert checks[1]["code"] == "ok"
    repeated = engine.parse_rule_bundle(payload, bundle(rule(vendor="SyntheticVendor", definition=definition)),
                                        waf_vendor="SyntheticVendor")
    assert selected == repeated and definition == before
    assert selected["capture_selection"]["status"] == ("generic_fallback" if mode == "all" else "matched")


def test_json_pointer_failure_preserves_fixed_cause_without_returning_the_pointer_or_values():
    checks = diagnose(json.dumps({"request": PRIVATE}),
                      [{"kind": "json_string_exists", "json_pointer": "/" + PRIVATE}])
    assert checks[0]["matched"] is False and checks[0]["code"] == "parser_json_pointer_missing"
    assert PRIVATE not in json.dumps(checks)


def test_json_scalar_type_and_non_string_request_failures_have_specific_fixed_codes():
    checks = diagnose('{"request":42,"enabled":true}', [
        {"kind": "json_equals", "json_pointer": "/enabled", "value": 1},
        {"kind": "json_string_exists", "json_pointer": "/request"}])
    assert [item["matched"] for item in checks] == [False, False]
    assert [item["code"] for item in checks] == ["parser_json_value_mismatch", "parser_json_string_missing"]


def test_regex_condition_is_search_not_full_match_and_never_returns_pattern_or_raw_text():
    if engine._re2 is None:
        pytest.skip("Native RE2 is unavailable; separate dependency guards cover this environment")
    checks = diagnose(f"prefix {PRIVATE} GET /synthetic HTTP/1.1\r\n\r\n", [
        {"kind": "regex_match", "pattern": "GET /synthetic"},
        {"kind": "regex_match", "pattern": PRIVATE + "NO_MATCH"}])
    assert [item["matched"] for item in checks] == [True, False]
    assert [item["code"] for item in checks] == ["ok", "parser_regex_no_match"]
    assert PRIVATE not in json.dumps(checks)


def test_match_diagnostics_are_bounded_and_omit_condition_literals():
    checks = diagnose(PRIVATE, [{"kind": "starts_with", "value": f"different-{PRIVATE}-{index}"}
                                for index in range(8)])
    assert len(checks) == 8 and PRIVATE not in json.dumps(checks)
    assert all(set(item) == {"condition_index", "field", "kind", "matched", "code"} for item in checks)
    assert all(item["matched"] is False and item["code"] == "parser_prefix_mismatch" for item in checks)


def test_unexpected_match_exception_is_fixed_and_never_copies_exception_text(monkeypatch):
    def unexpected(*_args):
        raise RuntimeError(PRIVATE)
    monkeypatch.setattr(engine, "_matches", unexpected)
    monkeypatch.setattr(match_diagnostics, "_matches", unexpected, raising=False)
    checks = diagnose(PRIVATE, [{"kind": "starts_with", "value": "GET "}])
    assert checks[0]["matched"] is False
    assert checks[0]["code"] == "parser_match_condition_failed"
    assert PRIVATE not in json.dumps(checks)


def test_feedback_selects_false_conditions_only_and_keeps_original_diagnostics():
    checks = [{"sample_id": f"c{index // 8 + 1:03d}", "condition_index": index % 8,
               "field": f"match.all.{index % 8}", "kind": "starts_with", "matched": index % 2 == 0,
               "code": "ok" if index % 2 == 0 else "parser_prefix_mismatch"} for index in range(32)]
    failure = WorkbenchOutputError("parser_workbench_authoring_failed", "authoring", match_checks=checks)
    original = deepcopy(checks)
    profile = SimpleNamespace(context_window=32768, max_output_tokens=3072)
    payload = json.loads(jobs._correction_payload(profile, {"samples": []}, "", failure))
    selected = payload["correction_feedback"]["match_checks"]
    assert len(selected) == 12 and all(check["matched"] is False for check in selected)
    assert checks == original


def test_condition_feedback_is_reduced_to_fit_budget_without_relaxing_rule():
    checks = [{"sample_id": f"c{index // 8 + 1:03d}", "condition_index": index % 8,
               "field": f"match.all.{index % 8}", "kind": "starts_with", "matched": False,
               "code": "parser_prefix_mismatch"} for index in range(16)]
    previous = proposal()
    failure = WorkbenchOutputError("parser_workbench_authoring_failed", "authoring",
                                   previous_rule=previous, match_checks=checks)
    profile = SimpleNamespace(context_window=5700, max_output_tokens=1024)
    text = jobs._correction_payload(profile, {"samples": []}, "", failure)
    jobs._budget(profile, text, "")
    feedback = json.loads(text)["correction_feedback"]
    assert feedback["match_checks_omitted"] is True
    assert len(feedback.get("match_checks", [])) < 12
    assert failure.match_checks == checks and failure.previous_rule == previous


def test_wrapper_prefix_mismatch_is_repaired_with_condition_feedback_without_leaking_holdout(
    client, settings, monkeypatch,
):
    session, profile_id = confirmed_session(client, settings, monkeypatch)
    invalid = proposal()
    invalid["match"] = {"all": [{"kind": "starts_with", "value": "GET "}]}
    calls = wire(monkeypatch, lambda _body, _document, index: envelope(invalid if index == 1 else None))
    job = finish_generation(client, session, profile_id, settings)
    assert job["status"] == "completed" and len(calls) == 2
    feedback = calls[1]["document"]["correction_feedback"]
    assert feedback["previous_rule"] == invalid
    assert feedback["code"] == "parser_workbench_authoring_failed"
    checks = feedback["match_checks"]
    allowed = {sample["case_id"] for sample in calls[1]["document"]["samples"]}
    assert checks and len(checks) <= 12
    assert all(item["sample_id"] in allowed and item["matched"] is False for item in checks)
    assert all(item["code"] == "parser_prefix_mismatch" and item["condition_index"] == 0 for item in checks)
    attempts = job["summary"]["diagnostics"]["attempts"]
    assert attempts[0]["match_checks"] == checks
    assert attempts[-1]["code"] == "ok"
    serialized = json.dumps(calls, ensure_ascii=False)
    assert all(case["case_id"] not in serialized for case in session["cases"] if case["role"] == "holdout")
    assert not any(field in serialized for field in ("expected_verdict", "reference_verdict", "expectations"))


def test_failed_job_keeps_safe_bounded_per_sample_condition_metadata(client, settings, monkeypatch):
    login(client)
    paths = tuple("/" + item for item in ("alpha", "beta", "gamma", "delta", "epsilon", "zeta",
                                          "eta", "theta", "iota", "kappa", "lambda", "mu"))
    session, profile_id = create(client, sources(client, paths=paths)), profile(client)
    with monkeypatch.context() as setup:
        setup.setattr(jobs, "request_completion", discovery_stub([]))
        response = client.post(PREFIX + f"/sessions/{session['id']}/discover", json={
            "expected_revision": session["revision"], "profile_id": profile_id,
            "internal_data_confirmed": True})
        assert response.status_code == 202 and run(client, settings)
    session = client.get(PREFIX + f"/sessions/{session['id']}").json()
    groups = [{key: group[key] for key in ("group_id", "name", "waf_vendor", "case_ids")}
              for group in session["groups"]]
    response = client.post(PREFIX + f"/sessions/{session['id']}/groups", json={
        "expected_revision": session["revision"], "groups": groups})
    assert response.status_code == 200
    session = response.json()
    invalid = proposal()
    invalid["match"] = {"all": [{"kind": "starts_with", "value": f"{PRIVATE}-{index}"}
                                  for index in range(8)]}
    calls = wire(monkeypatch, lambda *_args: envelope(invalid))
    job = finish_generation(client, session, profile_id, settings)
    assert job["status"] == "failed" and job["error_code"] == "parser_workbench_authoring_failed"
    assert len(calls) == job["summary"]["call_limit"] == 3
    attempts = job["summary"]["diagnostics"]["attempts"]
    for attempt in attempts:
        checks = attempt["match_checks"]
        assert 0 < len(checks) <= 64
        assert len({item["sample_id"] for item in checks}) <= 8
        assert all(item["matched"] is False and 0 <= item["condition_index"] < 8 for item in checks)
    feedback = calls[1]["document"]["correction_feedback"]
    assert 0 < len(feedback["match_checks"]) <= 12
    assert PRIVATE not in json.dumps(job, ensure_ascii=False)
    assert "raw_payload" not in json.dumps(attempts)
    assert all(case["case_id"] not in json.dumps(feedback) for case in session["cases"] if case["role"] == "holdout")
