"""Read-only diagnostic candidate: preserved judgments, dispatch and snapshots."""
from copy import deepcopy
import hashlib
from pathlib import Path
from types import SimpleNamespace

import pytest

from app import worker_v235
from app.agent import evidence_pipeline_v234 as previous_pipeline
from app.agent import evidence_pipeline_v235 as pipeline
from app.agent import execution_policy_v234, execution_policy_v235
from app.agent import input_builder_v234, input_builder_v235
from app.agent import legacy_prompts_v234, legacy_prompts_v235 as prompts
from app.agent.policy import VerifierPolicyContext
from app.services import worker_runtime_v235 as runtime
from app.services.http_parser import parse_http_payload
from app.services.prompt_policies import get_active_policy
from app.services.prompt_snapshots import (
    PromptSnapshotError, _baseline_rules, _build_snapshot, load_analysis_prompt,
)
from test_execution_policy_v221 import output

pytestmark = pytest.mark.usefixtures("legacy_v235_admissions")


FROZEN_V234 = "17308bad35c3f06841d7fb2185c8b62e3a174b65a9c05425a38a0106783f34ca"
FROZEN_PROMPTS = "5bc1731da5a1fcecf071bf7550824f3dab1ea011d23476470934df23aad85f18"


def test_v234_execution_bytes_and_complete_instructions_are_preserved():
    previous = previous_pipeline.pipeline_artifact()
    assert previous["sha256"] == FROZEN_V234 and len(previous["files"]) == 193
    assert hashlib.sha256(Path(legacy_prompts_v234.__file__).read_bytes()).hexdigest() == FROZEN_PROMPTS
    assert prompts.FIXED_RULES_VERSION == "waf-system-v2.35"
    assert prompts.PROMPT_VERSION == "waf-judgment-v2.35"
    for name in ("FIXED_INSTRUCTIONS", "DEFAULT_POLICY_TEXT", "PRIMARY_ROLE", "VERIFIER_ROLE",
                 "PRIMARY_INSTRUCTIONS", "VERIFIER_INSTRUCTIONS", "BASE_INSTRUCTIONS"):
        assert getattr(prompts, name) == getattr(legacy_prompts_v234, name)
    assert prompts.build_role_instructions("Synthetic administrator instructions.") == (
        legacy_prompts_v234.build_role_instructions("Synthetic administrator instructions."))
    assert prompts.V3_RULES_VERSIONS == legacy_prompts_v234.V3_RULES_VERSIONS | {pipeline.RULES_VERSION}
    assert prompts.SEMANTIC_RULES_VERSIONS == legacy_prompts_v234.SEMANTIC_RULES_VERSIONS | {pipeline.RULES_VERSION}


def test_diagnostic_helper_and_all_new_execution_paths_are_pinned_without_changing_old_files():
    previous, current = previous_pipeline.pipeline_artifact(), pipeline.pipeline_artifact()
    assert current["version"] == "evidence-pipeline-v15"
    assert current["input_version"] == previous["input_version"]
    assert current["source_catalog_version"] == previous["source_catalog_version"]
    assert current["relations_version"] == previous["relations_version"]
    assert all(current["files"][name] == digest for name, digest in previous["files"].items())
    for name in ("rejected_evidence_v1.py", "executor_v235.py", "../worker_v235.py",
                 "../worker_entrypoint_v235.py", "../services/worker_runtime_v235.py"):
        assert name in current["files"]
    root = Path(pipeline.__file__).parent
    assert all(hashlib.sha256((root / name).read_bytes()).hexdigest() == digest
               for name, digest in current["files"].items())
    assert pipeline.validate_pipeline_snapshot(previous, "waf-system-v2.34") == previous
    assert pipeline.validate_pipeline_snapshot(current, pipeline.RULES_VERSION) == current
    altered = deepcopy(current)
    altered["files"]["rejected_evidence_v1.py"] = "0" * 64
    with pytest.raises(ValueError, match="evidence_pipeline_snapshot_invalid"):
        pipeline.validate_pipeline_snapshot(altered, pipeline.RULES_VERSION)
    with pytest.raises(ValueError, match="evidence_pipeline_snapshot_invalid"):
        pipeline.validate_pipeline_snapshot(previous, pipeline.RULES_VERSION)


@pytest.mark.parametrize("rules", [legacy_prompts_v234, prompts])
def test_snapshots_restore_exact_baseline_and_dispatch_the_original_revision(client, monkeypatch, rules):
    crypto = client.app.state.crypto
    with client.app.state.session_factory() as db:
        snapshot = _build_snapshot(get_active_policy(db, crypto), crypto, rules=rules)
    row = SimpleNamespace(prompt_snapshot_ciphertext=crypto.encrypt_text(snapshot.model_dump_json()),
        prompt_version=snapshot.prompt_version, prompt_policy_version_id=snapshot.policy_version_id)
    assert load_analysis_prompt(row, crypto) == snapshot
    assert _baseline_rules(snapshot.metadata()) is rules
    calls = []
    monkeypatch.setattr(worker_v235, "process_moduagent", lambda *args, **kwargs: calls.append("v235"))
    monkeypatch.setattr(runtime.legacy, "process_moduagent", lambda *args, **kwargs: calls.append("v234"))
    runtime.process_moduagent(None, crypto, row, "", .75)
    assert calls == (["v235"] if rules is prompts else ["v234"])


@pytest.mark.parametrize("large", [False, True])
def test_input_and_capture_facts_are_exactly_the_same_as_v234(large):
    raw = "POST /synthetic HTTP/1.1\r\nHost: synthetic.invalid\r\n\r\n" + "body " * (15000 if large else 1)
    event, parsed = {"payload": raw, "signature": "synthetic", "event_id": "synthetic-id"}, parse_http_payload(raw)
    previous = input_builder_v234.build_agent_input(event, raw, parsed, 32768, 3072,
        evidence_selection=True, fixed_rules_version="waf-system-v2.34")
    current = input_builder_v235.build_agent_input(event, raw, parsed, 32768, 3072,
        evidence_selection=True, fixed_rules_version="waf-system-v2.35")
    assert current == previous


@pytest.mark.parametrize("action", ["D", "A"])
@pytest.mark.parametrize("omitted", [False, True])
def test_verifier_conditions_and_integrity_policy_are_unchanged(action, omitted):
    primary = output()
    previous = VerifierPolicyContext(waf_action=action, parser_status="partial", confidence_threshold=.75,
        input_truncated=omitted, fixed_rules_version="waf-system-v2.34")
    current = VerifierPolicyContext(waf_action=action, parser_status="partial", confidence_threshold=.75,
        input_truncated=omitted, fixed_rules_version="waf-system-v2.35")
    assert execution_policy_v235.verifier_reasons(primary, current) == (
        execution_policy_v234.verifier_reasons(primary, previous))
    assert execution_policy_v235.integrity_version_for_rules(current.fixed_rules_version) == (
        execution_policy_v234.integrity_version_for_rules(previous.fixed_rules_version))
    assert execution_policy_v235.verifier_policy_metadata(current.fixed_rules_version) == (
        execution_policy_v234.verifier_policy_metadata(previous.fixed_rules_version))


@pytest.mark.parametrize("version", ["waf-system-v2.20", "waf-system-v2.32", "waf-system-v2.34"])
def test_previous_execution_routes_only_to_frozen_dispatcher(monkeypatch, version):
    monkeypatch.setattr(runtime, "load_analysis_prompt", lambda *_a: SimpleNamespace(fixed_rules_version=version))
    monkeypatch.setattr(worker_v235, "process_moduagent", lambda *_a, **_k: pytest.fail("new worker for old snapshot"))
    seen, check = [], object()
    monkeypatch.setattr(runtime.legacy, "process_moduagent", lambda *a, **k: seen.append((a, k)) or "previous")
    row, crypto = SimpleNamespace(prompt_snapshot_ciphertext="synthetic", prompt_version=None), object()
    assert runtime.process_moduagent(None, crypto, row, "", .7, request_check=check) == "previous"
    assert seen == [((None, crypto, row, "", .7), {"request_check": check})]


@pytest.mark.parametrize("version", ["v2.34", "v2.35", "v2.36"])
def test_missing_pinned_provenance_never_uses_legacy_fallback(monkeypatch, version):
    monkeypatch.setattr(runtime.legacy, "process_moduagent", lambda *_a, **_k: pytest.fail("legacy fallback"))
    row = SimpleNamespace(prompt_snapshot_ciphertext=None, prompt_version=f"waf-judgment-{version}/policy-1")
    with pytest.raises(PromptSnapshotError):
        runtime.process_moduagent(None, object(), row, "", .7)


def test_unknown_future_snapshot_never_uses_legacy_fallback(monkeypatch):
    monkeypatch.setattr(runtime, "load_analysis_prompt", lambda *_a: SimpleNamespace(fixed_rules_version="waf-system-v2.36"))
    monkeypatch.setattr(runtime.legacy, "process_moduagent", lambda *_a, **_k: pytest.fail("legacy fallback"))
    row = SimpleNamespace(prompt_snapshot_ciphertext="synthetic", prompt_version=None)
    with pytest.raises(PromptSnapshotError):
        runtime.process_moduagent(None, object(), row, "", .7)
