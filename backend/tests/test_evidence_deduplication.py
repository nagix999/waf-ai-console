"""Synthetic source-grounded deduplication without rewriting role snapshots."""

from copy import deepcopy

import pytest

from app.agent.contracts import EvidenceItem, WAFAnalysisOutput
from app.agent.evidence import EvidenceSourceResolver
from app.agent.evidence_deduplication import canonical_evidence_field, deduplicate_evidence
from app.agent.policy import finalize_with_verifier, finalize_without_verifier
from app.models import Analysis
from app.worker import _ground_output_evidence
from test_agent_policy import output


def evidence(field="payload.query", excerpt="q=synthetic", interpretation="합성 입력을 관찰했습니다."):
    return EvidenceItem(field=field, excerpt=excerpt, interpretation_ko=interpretation)


def with_evidence(items, verdict="true_positive", severity=None):
    document = output(verdict=verdict, severity=severity).model_dump(mode="json")
    document["evidence"] = [item.model_dump(mode="json") for item in items]
    return WAFAnalysisOutput.model_validate(document)


@pytest.mark.parametrize("field,expected", [
    ("payload", "payload"),
    ("raw_payload", "payload"),
    ("event.raw_payload", "payload"),
    (" event.payload ", "payload"),
    ("payload.request_target", "payload.uri"),
    ("event.payload.request_target", "payload.uri"),
    ("payload.headers.Cookie", "payload.headers.cookie"),
    ("event.payload.headers.X-Synthetic", "payload.headers.x-synthetic"),
    ("payload.headers.X.Custom", "payload.headers.x.custom"),
    ("extra_fields.items[0].value", "extra_fields.items.0.value"),
    ("event.extra_fields.items[12].value", "extra_fields.items.12.value"),
    ("extra_fields.items[0][1].value", "extra_fields.items.0.1.value"),
])
def test_only_source_resolver_aliases_are_canonicalized(field, expected):
    assert canonical_evidence_field(field) == expected


@pytest.mark.parametrize("field", [
    "query", "body", "uri", "request_target", "headers.Cookie",
    "payload.query.Q", "payload.query.q[0]", "payload.query.encoded%20key",
    "payload.request_target.child", "payload.Headers.Cookie", "payload.headers.Bad Name",
    "extra_fields.items[00].value", "extra_fields.items.00.value",
])
def test_non_alias_source_names_are_not_rewritten(field):
    assert canonical_evidence_field(field) == field


@pytest.mark.parametrize("fields,excerpt", [
    (["payload", "event.raw_payload", " raw_payload "], "synthetic"),
    (["payload.uri", "payload.request_target", "event.payload.uri"], "/?q=synthetic"),
    (["payload.headers.Cookie", "payload.headers.cookie", "event.payload.headers.COOKIE"], "session=synthetic"),
    (["extra_fields.items[0].value", "event.extra_fields.items.0.value"], "synthetic"),
])
def test_canonicalized_items_really_match_the_same_source_before_deduplication(fields, excerpt):
    raw = "GET /?q=synthetic HTTP/1.1\r\nCookie: session=synthetic\r\n\r\nsynthetic"
    resolver = EvidenceSourceResolver(raw, {"extra_fields": {"items": [{"value": "synthetic"}]}})
    items = [evidence(field, excerpt) for field in fields]
    assert all(resolver.matches(item.field, item.excerpt) for item in items)
    retained = deduplicate_evidence(items)
    assert len(retained) == 1
    assert retained[0] == items[0]
    assert retained[0].field == fields[0]
    assert retained[0].excerpt == excerpt


def test_identical_triples_are_removed_without_mutating_or_sharing_retained_items():
    first = evidence(field="event.payload.query", interpretation="원문과 해석은 그대로 둡니다.")
    items = [first, first.model_copy(deep=True), first.model_copy(update={"field": "payload.query"})]
    before = deepcopy(items)
    retained = deduplicate_evidence(items)
    assert retained == [first]
    assert items == before
    assert retained[0] is not first
    retained[0].interpretation_ko = "최종 표시 객체의 합성 변경"
    assert items == before


def test_same_source_and_excerpt_with_opposing_or_different_interpretations_are_retained():
    items = [
        evidence(interpretation="공격 구문으로 해석할 수 있습니다."),
        evidence(field="event.payload.query", interpretation="정상 문자열 검색일 수 있습니다."),
        evidence(interpretation="서버 응답 정보는 포함되지 않았습니다."),
    ]
    assert deduplicate_evidence(items) == items


@pytest.mark.parametrize("first,second", [
    (evidence(excerpt="q=synthetic"), evidence(excerpt="synthetic")),
    (evidence(excerpt=" q=synthetic"), evidence(excerpt="q=synthetic")),
    (evidence(excerpt="q=SYNTHETIC"), evidence(excerpt="q=synthetic")),
    (evidence(excerpt="q=%41"), evidence(excerpt="q=A")),
    (evidence(interpretation="관찰했습니다."), evidence(interpretation="관찰했습니다. ")),
    (evidence(interpretation="공격이 아닙니다."), evidence(interpretation="공격입니다.")),
    (evidence(interpretation="첫 문장. 둘째 문장."), evidence(interpretation="첫 문장.")),
])
def test_substrings_whitespace_encoding_and_similar_text_are_not_treated_as_duplicates(first, second):
    assert deduplicate_evidence([first, second]) == [first, second]


@pytest.mark.parametrize("first_field,second_field", [
    ("payload", "payload.body"),
    ("payload.query", "payload.query.q"),
    ("payload.query", "payload.body"),
    ("payload.headers.Cookie", "payload.headers.X-Synthetic"),
    ("payload.uri", "payload.path"),
    ("payload.body", "body"),
    ("payload.query", "query"),
    ("payload.query.q", "payload.query.Q"),
    ("payload.query.q[0]", "payload.query.q.0"),
    ("signature", "event_name"),
    ("extra_fields.items.0.value", "extra_fields.items.1.value"),
])
def test_identical_text_at_distinct_claimed_sources_is_preserved(first_field, second_field):
    items = [evidence(first_field), evidence(second_field)]
    assert deduplicate_evidence(items) == items


def test_existing_five_item_and_primary_first_order_limit_is_unchanged():
    primary = [evidence(excerpt=f"synthetic-{index}") for index in range(5)]
    verifier = [evidence(excerpt=f"verifier-synthetic-{index}") for index in range(5)]
    assert deduplicate_evidence(primary + verifier) == primary


def test_duplicate_removal_leaves_capacity_for_later_distinct_evidence():
    first = evidence()
    later = [evidence(excerpt=f"synthetic-{index}") for index in range(4)]
    assert deduplicate_evidence([first, first, first, *later]) == [first, *later]


@pytest.mark.parametrize("route", ["no_verifier", "verifier_failed", "agreed", "disagreed"])
def test_every_finalization_route_deduplicates_without_changing_verdict_policy_or_snapshots(route):
    shared = evidence("event.payload.headers.Cookie", "session=synthetic", "요청 안의 문자열입니다.")
    alias_duplicate = shared.model_copy(update={"field": "payload.headers.cookie"})
    other_interpretation = shared.model_copy(update={"interpretation_ko": "인증 성공 여부는 이 문자열만으로 확인할 수 없습니다."})
    primary = with_evidence([shared, alias_duplicate, other_interpretation], severity="HIGH")
    verifier = with_evidence(
        [alias_duplicate, evidence("payload.body", "synthetic-body", "서로 다른 본문 위치입니다.")],
        verdict="false_positive" if route == "disagreed" else "true_positive",
        severity="NONE" if route == "disagreed" else "LOW",
    )
    before = deepcopy((primary, verifier))
    if route == "no_verifier":
        final = finalize_without_verifier(primary)
    else:
        final = finalize_with_verifier(
            primary,
            None if route == "verifier_failed" else verifier,
            ["synthetic-reason"],
            verifier_failure="synthetic-failure" if route == "verifier_failed" else None,
        )
    expected = [shared, other_interpretation]
    if route in {"agreed", "disagreed"}:
        expected.append(verifier.evidence[1])
    assert final.output.evidence == expected
    assert (primary, verifier) == before
    assert final.output is not primary
    assert all(item is not shared for item in final.output.evidence)
    assert final.output.verdict.value == ("inconclusive" if route in {"verifier_failed", "disagreed"} else "true_positive")
    assert final.output.threat_analysis.severity.value == {
        "no_verifier": "HIGH", "verifier_failed": "UNKNOWN", "agreed": "LOW", "disagreed": "UNKNOWN",
    }[route]
    assert final.verifier_executed == (route != "no_verifier")
    assert final.agreement == {"no_verifier": None, "verifier_failed": False, "agreed": True, "disagreed": False}[route]
    assert final.verifier_reasons == (() if route == "no_verifier" else ("synthetic-reason",))
    assert final.verifier_failure == ("synthetic-failure" if route == "verifier_failed" else None)
    WAFAnalysisOutput.model_validate(final.output.model_dump(mode="json"))


def test_no_verifier_path_preserves_non_evidence_result_fields_exactly():
    first = evidence()
    primary = with_evidence([first, first])
    result = finalize_without_verifier(primary).output
    assert result.model_dump(exclude={"evidence"}) == primary.model_dump(exclude={"evidence"})
    assert result.evidence == [first]


def test_empty_inconclusive_evidence_is_not_replaced_by_invented_evidence():
    primary = output(verdict="inconclusive")
    result = finalize_without_verifier(primary).output
    assert result.evidence == []
    assert result.verdict.value == "inconclusive"
    WAFAnalysisOutput.model_validate(result.model_dump(mode="json"))


def test_grounding_still_rejects_wrong_source_before_exact_deduplication():
    valid = evidence("payload.query", "q=synthetic")
    duplicate = valid.model_copy(update={"field": "event.payload.query"})
    wrong_source = valid.model_copy(update={"field": "payload.body"})
    primary = with_evidence([valid, duplicate, wrong_source])
    before = primary.model_dump(mode="json")
    grounded, telemetry = _ground_output_evidence(
        primary, Analysis(extra_fields={}), "GET /?q=synthetic HTTP/1.1\r\n\r\ndifferent-body",
    )
    assert telemetry["checked_count"] == 3
    assert telemetry["accepted_count"] == 2
    assert telemetry["rejected_count"] == 1
    result = finalize_without_verifier(grounded).output
    assert result.evidence == [valid]
    assert result.verdict.value == "true_positive"
    assert primary.model_dump(mode="json") == before
    assert "synthetic" not in str(telemetry)


def test_invalid_only_evidence_is_not_restored_during_finalization():
    primary = with_evidence([evidence("payload.body", "q=synthetic")])
    grounded, _ = _ground_output_evidence(
        primary, Analysis(extra_fields={}), "GET /?q=synthetic HTTP/1.1\r\n\r\n",
    )
    result = finalize_without_verifier(grounded).output
    assert result.evidence == []
    assert result.verdict.value == "inconclusive"
    assert result.threat_analysis.severity.value == "UNKNOWN"
