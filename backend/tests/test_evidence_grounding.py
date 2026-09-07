import pytest

from app.agent.contracts import TuningScope
from app.agent.evidence import EvidenceSourceResolver
from app.models import Analysis
from app.worker import _ground_output_evidence
from test_moduagent_worker import primary_output


@pytest.fixture
def sources():
    return EvidenceSourceResolver(
        "POST /search?q=%27+OR+1%3D1--&q=second&encoded%20key=raw-value HTTP/1.1\r\n"
        "Host: example.internal\r\nCookie: session=fixture\r\n"
        "X-Example: header-only\r\nX-Example: duplicate-header\r\n\r\n"
        '{"marker":"body-only"}',
        {
            "signature": "metadata-only",
            "event_name": "example-event",
            "src_port": 43122,
            "extra_fields": {
                "attributes": {"rule": "synthetic-rule"},
                "items": [{"value": "first-item"}, {"value": "second-item"}],
                "payload": {"query": "metadata-only-query"},
                "enabled": True,
            },
        },
    )


@pytest.mark.parametrize("field,excerpt", [
    ("payload", "header-only"),
    ("event.payload", "body-only"),
    ("raw_payload", "%27+OR+1%3D1--"),
    ("event.raw_payload", "q=second"),
    ("payload.request_line", "POST /search?"),
    ("payload.method", "POST"),
    ("payload.uri", "/search?q=%27+OR+1%3D1--"),
    ("payload.path", "/search"),
    ("payload.protocol", "HTTP/1.1"),
    ("payload.query", "%27+OR+1%3D1--"),
    ("payload.query.q", "q=second"),
    ("payload.query.encoded%20key", "raw-value"),
    ("payload.headers", "X-Example: header-only\r\nX-Example: duplicate-header"),
    ("payload.headers.COOKIE", "session=fixture"),
    ("event.payload.headers.x-example", "duplicate-header"),
    ("payload.body", '{"marker":"body-only"}'),
    ("signature", "metadata-only"),
    ("event.event_name", "example-event"),
    ("src_port", "43122"),
    ("extra_fields.attributes.rule", "synthetic-rule"),
    ("event.extra_fields.items.0.value", "first-item"),
    ("extra_fields.items[1].value", "second-item"),
    ("extra_fields.payload.query", "metadata-only-query"),
])
def test_exact_evidence_resolves_only_its_claimed_source(sources, field, excerpt):
    assert sources.matches(field, excerpt)


@pytest.mark.parametrize("field,excerpt", [
    ("payload.query", "metadata-only"),
    ("payload.query", "metadata-only-query"),
    ("payload.query", "body-only"),
    ("payload.body", "%27+OR+1%3D1--"),
    ("signature", "example-event"),
    ("signature", "session=fixture"),
    ("unknown_field", "body-only"),
    ("payload.unknown_field", "body-only"),
    ("payload.query", "' OR 1=1--"),
    ("payload.query.encoded key", "raw-value"),
    ("payload.query.q", "raw-value"),
    ("payload.headers.Cookie", "header-only"),
    ("payload.headers.Cookie", "SESSION=FIXTURE"),
    ("payload.headers.Authorization", "session=fixture"),
    ("payload.path", "q=second"),
    ("extra_fields", "synthetic-rule"),
    ("extra_fields.items.0.value", "second-item"),
    ("extra_fields.items.99.value", "first-item"),
    ("extra_fields.enabled", "true"),
    ("generic_http_parser_hints.uri", "/search"),
    ("payload", "  "),
])
def test_cross_field_unknown_and_transformed_evidence_is_rejected(sources, field, excerpt):
    assert not sources.matches(field, excerpt)


def test_unrecognized_payload_cannot_invent_http_subfields():
    resolver = EvidenceSourceResolver("synthetic vendor prefix body=marker", {})
    assert resolver.matches("payload", "body=marker")
    assert not resolver.matches("payload.body", "marker")
    assert not resolver.matches("payload.query", "marker")


def test_lf_and_absolute_request_target_preserve_raw_bytes():
    resolver = EvidenceSourceResolver(
        "GET https://example.internal/a%2Fb?q=%2F HTTP/1.1\nX-Test: value\n\nraw\\nbody", {},
    )
    assert resolver.matches("payload.uri", "https://example.internal/a%2Fb?q=%2F")
    assert resolver.matches("payload.path", "/a%2Fb")
    assert not resolver.matches("payload.path", "example.internal")
    assert not resolver.matches("payload.path", "/a/b")
    assert resolver.matches("payload.body", "raw\\nbody")
    assert not resolver.matches("payload.body", "raw\nbody")


def test_authority_target_is_not_mislabeled_as_a_path():
    resolver = EvidenceSourceResolver("CONNECT example.internal:443 HTTP/1.1\r\n\r\n", {})
    assert resolver.matches("payload.uri", "example.internal:443")
    assert not resolver.matches("payload.path", "example.internal:443")


def test_invalid_claimed_field_downgrades_and_disables_tuning_without_recording_raw_data():
    analysis = Analysis(signature="metadata-only", extra_fields={})
    output = primary_output("metadata-only", "payload.query")
    output.tuning_recommendation = output.tuning_recommendation.model_copy(update={
        "recommended": True,
        "scope": TuningScope.parameter,
        "proposal_ko": "합성 조정 제안",
        "risk_ko": "합성 위험",
        "validation_ko": "합성 검증",
    })
    grounded, telemetry = _ground_output_evidence(output, analysis, "GET /?q=safe HTTP/1.1\r\n\r\n")
    assert grounded.verdict.value == "inconclusive"
    assert grounded.threat_analysis.severity.value == "UNKNOWN"
    assert grounded.confidence_score <= 0.49
    assert not grounded.tuning_recommendation.recommended
    assert grounded.evidence == []
    assert output.evidence[0].excerpt == "metadata-only"
    assert telemetry["rejected_count"] == 1
    assert telemetry["downgraded_to_inconclusive"] is True
    assert "metadata-only" not in str(telemetry)


def test_only_invalid_item_is_removed_when_other_evidence_has_valid_provenance():
    output = primary_output("q=safe", "payload.query")
    output.evidence.append(output.evidence[0].model_copy(update={
        "field": "payload.body", "excerpt": "q=safe",
    }))
    grounded, telemetry = _ground_output_evidence(
        output, Analysis(extra_fields={}), "GET /?q=safe HTTP/1.1\r\n\r\n",
    )
    assert grounded.verdict.value == "true_positive"
    assert len(grounded.evidence) == 1
    assert grounded.evidence[0].field == "payload.query"
    assert telemetry["rejected_count"] == 1
    assert telemetry["downgraded_to_inconclusive"] is False
