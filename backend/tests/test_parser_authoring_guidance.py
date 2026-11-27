"""Bounded, value-free parser authoring observations over synthetic inputs."""
import json
from types import SimpleNamespace

import pytest

from app.services import parser_authoring_guidance as guidance
from app.services.parser_rule_engine import MAX_SECONDS, MAX_SOURCE_BYTES, ParserRuleError


def sample(raw, **extra):
    return {"case_id": "c001", "raw_payload": raw, **extra}


def request(target="/synthetic-private-request", *, method="GET", mode="actual"):
    delimiter = {"actual": "\r\n", "escaped_crlf": "\\r\\n", "escaped_lf": "\\n"}[mode]
    return delimiter.join([f"{method} {target} HTTP/1.1", "Host: example.invalid", "", "synthetic-private-body"])


def observed(*raws):
    return guidance.authoring_structure_hints([sample(raw) for raw in raws])


@pytest.mark.parametrize("mode", ["actual", "escaped_crlf", "escaped_lf"])
def test_raw_request_observes_storage_framing_without_echoing_request(mode):
    value = observed(request(mode=mode))
    assert value == {
        "version": "parser-authoring-structure-v1", "sample_count": 1,
        "inspected_sample_count": 1, "truncated": False,
        "raw_http_line_endings": {mode: 1}, "json_document_count": 0,
        "json_request_paths": [],
    }
    serialized = json.dumps(value)
    assert not any(text in serialized for text in ("GET", "Host", "example.invalid", "synthetic-private"))


@pytest.mark.parametrize("mode", ["actual", "escaped_crlf", "escaped_lf"])
def test_json_string_observations_describe_paths_and_actual_decoded_storage(mode):
    value = observed(json.dumps({"request": request(mode=mode), "token": "synthetic-private-token"}))
    assert value["raw_http_line_endings"] == {}
    assert value["json_document_count"] == 1
    assert value["json_request_paths"] == [{
        "json_pointer": "/request", "observed_sample_count": 1, "line_endings": {mode: 1},
        "match_example": {"all": [{"kind": "json_string_exists", "json_pointer": "/request"}]},
        "extract_example": {"kind": "json_string", "json_pointer": "/request"},
    }]
    assert "synthetic-private" not in json.dumps(value)


def test_nested_paths_arrays_and_pointer_escaping_keep_exact_key_structure():
    value = observed(json.dumps({"a/b": [{"~capture": request()}]}))
    path = value["json_request_paths"][0]
    assert path["json_pointer"] == "/a~1b/0/~0capture"
    assert path["extract_example"]["json_pointer"] == path["json_pointer"]


def test_root_json_string_has_valid_empty_pointer():
    value = observed(json.dumps(request()))
    assert value["json_request_paths"][0]["json_pointer"] == ""


def test_mixed_shapes_keep_per_path_counts_instead_of_claiming_one_shared_path():
    value = observed(
        request("/first", method="GET"),
        json.dumps({"request": request("/second", method="POST")}),
        json.dumps({"request": request("/third", mode="escaped_crlf")}),
        json.dumps({"different": request("/fourth", method="PUT")}),
    )
    assert value["sample_count"] == value["inspected_sample_count"] == 4
    assert value["raw_http_line_endings"] == {"actual": 1}
    assert value["json_document_count"] == 3
    assert [(item["json_pointer"], item["observed_sample_count"]) for item in value["json_request_paths"]] == [
        ("/request", 2), ("/different", 1)]
    assert value["json_request_paths"][0]["line_endings"] == {"actual": 1, "escaped_crlf": 1}
    serialized = json.dumps(value)
    assert not any(text in serialized for text in ("/first", "/second", "/third", "/fourth", "GET", "POST", "PUT"))


def test_actual_request_line_wins_over_literal_escaped_newlines_in_body():
    raw = request() + r"\r\nGET /body-only HTTP/1.1\n"
    value = observed(raw, json.dumps({"request": raw}))
    assert value["raw_http_line_endings"] == {"actual": 1}
    assert value["json_request_paths"][0]["line_endings"] == {"actual": 1}


@pytest.mark.parametrize("raw", [
    '{"request":"GET /a HTTP/1.1","request":"GET /b HTTP/1.1"}',
    '{"request":"GET /a HTTP/1.1","\\u0072equest":"GET /b HTTP/1.1"}',
    '{"nested":{"request":"GET /a HTTP/1.1","request":"GET /b HTTP/1.1"}}',
    '{"request":"GET /a HTTP/1.1",}',
    '{"request":"GET /a HTTP/1.1"} trailing',
    '{"request":"GET /a HTTP/1.1","unpaired":"\\ud800"}',
    '{"request":"GET /a HTTP/1.1","number":NaN}',
])
def test_invalid_or_duplicate_json_never_produces_partial_path_hints(raw):
    value = observed(raw)
    assert value["inspected_sample_count"] == 1
    assert value["json_document_count"] == 0
    assert value["json_request_paths"] == []
    assert value["truncated"] is False


@pytest.mark.parametrize("raw", [
    "not a request", "response-prefix GET /a HTTP/1.1", "HTTP/1.1 200 OK",
    json.dumps({"method": "POST", "uri": "/a", "body": "synthetic-private-body"}),
    json.dumps({"message": "prefix GET /body HTTP/1.1"}),
    json.dumps({"message": "arbitrary\nGET /body HTTP/1.1"}),
    "GET * HTTP/1.1", "POST invalid-target HTTP/1.1",
])
def test_no_request_is_inferred_from_scalar_values_or_embedded_body_lines(raw):
    value = observed(raw)
    assert value["raw_http_line_endings"] == {}
    assert value["json_request_paths"] == []
    serialized = json.dumps(value)
    assert "synthetic-private" not in serialized and "invalid-target" not in serialized


@pytest.mark.parametrize("line", ["OPTIONS * HTTP/1.1", "CONNECT example.invalid:443 HTTP/1.1",
                                      "GET https://example.invalid/a HTTP/1.1"])
def test_recognizes_supported_request_target_forms_without_emitting_literals(line):
    assert observed(line)["raw_http_line_endings"] == {"actual": 1}


@pytest.mark.parametrize("samples", [None, {}, [], "raw", [sample("x")] * 101,
                                    [None], [{}], [sample(None)], [sample(1)], [sample(b"GET / HTTP/1.1")],
                                    [sample("\ud800")], [sample("x" * (MAX_SOURCE_BYTES + 1))],
                                    [sample("가" * (MAX_SOURCE_BYTES // 3 + 1))]])
def test_invalid_input_returns_no_optional_hint(samples):
    assert guidance.authoring_structure_hints(samples) is None


def test_all_one_hundred_admitted_cases_share_one_time_budget(monkeypatch):
    instances = []

    class Budget:
        def __init__(self):
            instances.append(self)
            self.checks = 0

        def check(self):
            self.checks += 1

    monkeypatch.setattr(guidance, "_Budget", Budget)
    value = guidance.authoring_structure_hints([sample(request())] * 100)
    assert value["sample_count"] == value["inspected_sample_count"] == 100
    assert value["raw_http_line_endings"] == {"actual": 100}
    assert value["truncated"] is False
    assert len(instances) == 1 and instances[0].checks == 100
    assert MAX_SECONDS == 0.1


def test_string_scan_cap_does_not_visit_later_scalar_values():
    payload = {f"scalar_{index}": "unrelated" for index in range(32)}
    payload["late_request"] = request()
    value = observed(json.dumps(payload))
    assert value["json_request_paths"] == []
    assert value["json_document_count"] == value["inspected_sample_count"] == 1
    assert value["truncated"] is True


def test_observation_at_last_allowed_string_is_kept():
    payload = {f"scalar_{index}": "unrelated" for index in range(31)}
    payload["last_request"] = request()
    value = observed(json.dumps(payload))
    assert value["json_request_paths"][0]["json_pointer"] == "/last_request"
    assert value["truncated"] is False


def test_only_first_eight_paths_are_retained_without_losing_known_path_counts():
    payload = {f"request_{index}": request() for index in range(9)}
    value = observed(json.dumps(payload), json.dumps({"request_0": request("/changed")}))
    assert len(value["json_request_paths"]) == 8
    assert value["json_request_paths"][0]["observed_sample_count"] == 2
    assert value["truncated"] is True
    assert "/request_8" not in json.dumps(value)


@pytest.mark.parametrize("key", ["x" * 513, "가" * 513])
def test_overlong_json_pointer_is_omitted_not_truncated_to_another_key(key):
    value = observed(json.dumps({key: request()}))
    assert value["json_request_paths"] == []
    assert value["json_document_count"] == 1


def test_deep_json_pointer_is_omitted_but_shallow_sibling_is_observed():
    nested = request()
    for _index in range(17):
        nested = {"node": nested}
    value = observed(json.dumps({"nested": nested, "request": request()}))
    assert [item["json_pointer"] for item in value["json_request_paths"]] == ["/request"]


def test_serialized_hint_budget_counts_utf8_bytes_not_characters():
    payload = {f"{index}" + "가" * 160: request() for index in range(8)}
    value = observed(json.dumps(payload, ensure_ascii=False))
    assert value["truncated"] is True
    assert 0 < len(value["json_request_paths"]) < 8
    assert len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")) <= 4096
    assert all(item["observed_sample_count"] == 1 for item in value["json_request_paths"])


def test_timeout_does_not_publish_partially_scanned_sample(monkeypatch):
    class Budget:
        def __init__(self):
            self.count = 0

        def check(self):
            self.count += 1
            if self.count == 3:
                raise ParserRuleError("parser_rule_time_limit")

    class Context:
        def __init__(self, _raw, _budget):
            pass

        def json(self):
            return SimpleNamespace(strings={
                ("first",): SimpleNamespace(text=request()),
                ("second",): SimpleNamespace(text=request()),
            })

    monkeypatch.setattr(guidance, "_Budget", Budget)
    monkeypatch.setattr(guidance, "_Context", Context)
    value = observed("{}")
    assert value["sample_count"] == 1 and value["inspected_sample_count"] == 0
    assert value["json_document_count"] == 0 and value["json_request_paths"] == []
    assert value["truncated"] is True
    assert "parser_rule_time_limit" not in json.dumps(value)


def test_timeout_retains_only_completed_sample_observations(monkeypatch):
    class Budget:
        def __init__(self):
            self.count = 0

        def check(self):
            self.count += 1
            if self.count == 2:
                raise ParserRuleError("parser_rule_time_limit")

    monkeypatch.setattr(guidance, "_Budget", Budget)
    value = observed(request(), request("/second"))
    assert value["sample_count"] == 2 and value["inspected_sample_count"] == 1
    assert value["raw_http_line_endings"] == {"actual": 1}
    assert value["truncated"] is True


def test_extra_source_labels_are_never_copied_to_optional_observations():
    value = guidance.authoring_structure_hints([sample(json.dumps({"request": request()}),
        expected_verdict="synthetic-private-answer", reference_verdict="synthetic-private-reference",
        expectations={"method": "PRIVATE"}, prior_result="synthetic-private-result")])
    text = json.dumps(value)
    assert not any(token in text for token in ("expected_verdict", "reference_verdict", "expectations", "prior_result", "PRIVATE"))
