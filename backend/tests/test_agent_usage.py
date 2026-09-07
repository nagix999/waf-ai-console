"""Offline regression for the actual ModuAgent Usage dataclass."""

from dataclasses import dataclass

import pytest
from moduagent.messages import Usage

from app.agent.executor import _usage_jsonable


def test_moduagent_usage_serializes_only_allowlisted_numeric_fields():
    value = Usage.from_provider({
        "prompt_tokens": 123, "completion_tokens": 45, "total_tokens": 168,
        "untrusted": "synthetic-provider-text-must-not-be-stored",
    })
    assert _usage_jsonable(value) == {"input_tokens": 123, "output_tokens": 45, "total_tokens": 168}
    assert value.provider["untrusted"] == "synthetic-provider-text-must-not-be-stored"


@pytest.mark.parametrize("value", [None, {}, Usage(), "Usage(input_tokens=10)", object(), Usage])
def test_missing_unknown_or_legacy_usage_is_not_interpreted_as_zero(value):
    assert _usage_jsonable(value) is None


@pytest.mark.parametrize("invalid", [True, False, -1, 1.5, float("inf"), float("nan"), "10", None])
def test_invalid_counter_is_unknown_without_coercion(invalid):
    assert _usage_jsonable({"input_tokens": invalid, "output_tokens": 2, "total_tokens": 10}) == {
        "input_tokens": None, "output_tokens": 2, "total_tokens": 10,
    }


def test_missing_counters_are_not_invented_or_derived():
    assert _usage_jsonable({"input_tokens": 100}) == {
        "input_tokens": 100, "output_tokens": None, "total_tokens": None,
    }


def test_zero_component_is_preserved_when_other_usage_is_measured():
    assert _usage_jsonable(Usage(100, 0, 100)) == {
        "input_tokens": 100, "output_tokens": 0, "total_tokens": 100,
    }


def test_dataclass_provider_is_not_serialized_or_stringified():
    class UntrustedProvider:
        def __str__(self):
            raise AssertionError("Provider text must not be inspected")

    @dataclass(frozen=True, slots=True)
    class Counters:
        input_tokens: int = 10
        output_tokens: int = 20
        total_tokens: int = 30
        provider: object = None

    assert _usage_jsonable(Counters(provider=UntrustedProvider())) == {
        "input_tokens": 10, "output_tokens": 20, "total_tokens": 30,
    }
