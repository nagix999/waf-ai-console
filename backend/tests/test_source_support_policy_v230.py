"""Source roles depend on provenance, not matching keywords in real requests."""
from copy import deepcopy
from types import SimpleNamespace

import pytest

from app.agent import evidence_source_policy_v230 as policy
from app.agent.contracts import RequestEvidenceSupport


def selection(support, source_id="c1"):
    return SimpleNamespace(evidence=[SimpleNamespace(source_id=source_id, supports=support)])


@pytest.mark.parametrize("field", ["signature", "event.signature", "signature_explanation",
    "event.signature_explanation", "extra_fields.signature_explanation", "event.extra_fields.signature_explanation"])
@pytest.mark.parametrize("support", ["attack_request", "normal_request", "true_positive", "false_positive"])
def test_reference_sources_remain_context_only_even_with_permissive_catalog(field, support):
    catalog = {"items": [{"source_id": "c1", "field": field,
                           "allowed_supports": ["attack_request", "normal_request", "context"]}]}
    assert policy.source_support_issues(selection(support), catalog, fixed_rules_version=policy.RULES_VERSION) == [
        {"index": 0, "reason": "signature_support_not_allowed"}]
    assert not policy.source_support_issues(selection("context"), catalog, fixed_rules_version=policy.RULES_VERSION)


@pytest.mark.parametrize("field", ["payload", "payload.body", "extra_fields.request.signature_explanation",
    "extra_fields.signature", "extra_fields.request.cookies"])
@pytest.mark.parametrize("support", ["attack_request", "normal_request", "true_positive", "false_positive",
    RequestEvidenceSupport.attack_request, RequestEvidenceSupport.normal_request])
def test_request_fields_are_not_restricted_by_names_or_text(field, support):
    catalog = {"items": [{"source_id": "c1", "field": field, "excerpt": "signature_explanation"}]}
    assert not policy.source_support_issues(selection(support), catalog, fixed_rules_version=policy.RULES_VERSION)


@pytest.mark.parametrize("support", ["attack_request", "normal_request", "true_positive", "false_positive"])
def test_explicit_allowed_supports_enforced_for_non_reference_fields_in_both_contracts(support):
    catalog = {"items": [{"source_id": "c1", "field": "extra_fields.request", "allowed_supports": ["context"]}]}
    before = deepcopy(catalog)
    selected = selection(support)
    assert policy.source_support_issues(selected, catalog, fixed_rules_version=policy.RULES_VERSION) == [
        {"index": 0, "reason": "source_support_not_allowed"}]
    assert selected.evidence[0].supports == support
    assert catalog == before
    assert not policy.source_support_issues(selection("context"), catalog, fixed_rules_version=policy.RULES_VERSION)


def test_catalog_content_ids_and_limits_are_identical_and_input_is_not_mutated():
    from app.agent import input_builder_v229, input_builder_v230
    from app.services.http_parser import parse_http_payload
    raw = "GET /synthetic HTTP/1.1\r\nHost: synthetic.invalid\r\n\r\n"
    event = {"payload": raw, "signature": "Synthetic", "signature_explanation": "Synthetic reference"}
    before = deepcopy(event)
    parsed = parse_http_payload(raw)
    old = input_builder_v229.build_agent_input(event, raw, parsed, 32768, 3072,
        fixed_rules_version="waf-system-v2.29", evidence_selection=True)
    current = input_builder_v230.build_agent_input(event, raw, parsed, 32768, 3072,
        fixed_rules_version="waf-system-v2.30", evidence_selection=True)
    assert old == current
    assert event == before
