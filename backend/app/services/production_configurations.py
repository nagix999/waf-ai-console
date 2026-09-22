"""One transactional Production mutation path, qualified by frozen evidence."""
from sqlalchemy import select

from ..models import (AccessAudit, AgentConfiguration, Analysis, ProductionPromotion,
                      PromptPolicyState, PromptPolicyVersion, InputSchemaVersion, TestEvaluation, TestRun, VLLMProfile, ValidationDatasetItem,
                      ValidationDatasetVersion)
from .agent_configuration import configuration_document, profile_metadata, validate_role_profile, has_input_budget
from .analysis import AnalysisIngestError
from .candidate_configurations import configuration_digest, snapshot_from_run, verify_configuration
from .change_events import record_change
from .evidence_editor import capture_editor_profile
from .input_schemas import (activate_schema_version, get_schema_state, get_schema_version,
                            load_definition, pin_schema)
from .official_evaluations import METRICS_VERSION, ground_truth_metadata
from .prompt_policies import get_active_policy, get_policy_version, read_policy_text
from .prompt_snapshots import pin_analysis_prompt
from .test_runs import describe_run, write_lock
from .vllm_profiles import profile_fingerprint


def fail(code):
    raise AnalysisIngestError(code, 409)


def live_snapshot(db, crypto, settings, *, roles=None, prompt_id=None, schema_id=None):
    roles = roles or configuration_document(db)["assignments"]["production"]
    primary = db.get(VLLMProfile, roles["primary_profile_id"]) if roles["primary_profile_id"] else None
    verifier = db.get(VLLMProfile, roles["verifier_profile_id"] or primary.id) if primary else None
    editor_id = roles["evidence_editor_profile_id"] or (primary.id if primary else None)
    editor_profile = db.get(VLLMProfile, editor_id) if roles["evidence_editor_enabled"] and editor_id else None
    temporary = TestRun(profile_id=primary.id if primary else None,
        profile_fingerprint=profile_fingerprint(primary) if primary else None,
        profile_metadata={"verifier_profile": profile_metadata(verifier) if verifier else {
            "model_profile_id": None, "profile_fingerprint": None},
            "verifier_confidence_threshold": settings.verifier_confidence_threshold})
    pin_analysis_prompt(db, crypto, temporary, version_id=prompt_id)
    pin_schema(db, crypto, temporary, version=get_schema_version(db, schema_id) if schema_id else None)
    editor = capture_editor_profile(editor_profile) if editor_profile else None
    temporary.evidence_editor_snapshot_ciphertext = crypto.encrypt_text(editor.model_dump_json()) if editor else None
    return snapshot_from_run(temporary, crypto)


def ensure_baseline(db, crypto, settings):
    write_lock(db)
    if db.scalar(select(ProductionPromotion.id).limit(1)):
        return
    snapshot = live_snapshot(db, crypto, settings)
    row = ProductionPromotion(kind="baseline", configuration_hash=configuration_digest(snapshot),
        snapshot_json=snapshot, actor_id="system:canonical-v5")
    db.add(row)
    db.flush()
    record_change(db, category="configuration", actor=row.actor_id, action="capture_production_baseline",
        resource_type="production_configuration", resource_id=row.id, after=snapshot)


def current_configuration(db, crypto, settings):
    snapshot = live_snapshot(db, crypto, settings)
    latest = db.scalar(select(ProductionPromotion).where(ProductionPromotion.kind == "promotion")
        .order_by(ProductionPromotion.created_at.desc(), ProductionPromotion.id.desc()).limit(1))
    digest = configuration_digest(snapshot)
    profile_names = {p.id: p.name for p in db.scalars(select(VLLMProfile))}
    version_names = {p.id: f"v{p.version_number} · {p.name}" for cls in (PromptPolicyVersion, InputSchemaVersion) for p in db.scalars(select(cls))}
    return {"configuration_id": latest.id if latest else None, "configuration_hash": digest,
        "snapshot": snapshot, "profile_names": profile_names, "version_names": version_names,
        "source_test_run_id": latest.source_test_run_id if latest else None,
        "applied_at": latest.created_at if latest else None,
        "drifted": bool(latest and latest.configuration_hash != digest)}


def official_document(record):
    if not record:
        return None
    return {"id": record.id, "test_run_id": record.test_run_id, "created_at": record.created_at,
        "configuration_hash": record.configuration_hash, "summary": record.summary_json,
        "metrics_version": record.metrics_version}


def official_for_hash(db, digest):
    return db.scalar(select(TestEvaluation).join(TestRun, TestRun.id == TestEvaluation.test_run_id)
        .join(ValidationDatasetVersion, ValidationDatasetVersion.id == TestRun.dataset_version_id)
        .where(ValidationDatasetVersion.is_published.is_(True), TestEvaluation.configuration_hash == digest,
        TestEvaluation.evaluation_kind == "ground_truth", TestEvaluation.metrics_version == METRICS_VERSION)
        .order_by(TestEvaluation.created_at.desc(), TestEvaluation.revision.desc()).limit(1))


def schema_diff(db, crypto, current_id, candidate_id):
    def fields(identifier):
        return {f.name: f.model_dump(mode="json") for f in load_definition(get_schema_version(db, identifier), crypto)}
    before, after = fields(current_id), fields(candidate_id)
    diff = [{"field": key, "before": before.get(key), "after": after.get(key)}
            for key in sorted(before.keys() | after.keys()) if before.get(key) != after.get(key)]
    return {"schema_changed": current_id != candidate_id, "field_diff": diff,
        "compatibility_warnings": ["input_contract_changed_review_required"] if diff else []}


def preflight(db, crypto, settings, identifier):
    run = db.get(TestRun, identifier)
    if not run:
        raise AnalysisIngestError("test_run_not_found", 404)
    current = current_configuration(db, crypto, settings)
    checks = []
    def check(code, passed):
        remedies = {"tested_profiles_still_valid": "open_llm_profile", "tested_instructions_still_current": "open_instructions"}
        checks.append({"code": code, "passed": bool(passed), "remediation": None if passed else
            {"kind": remedies.get(code, "rerun_test"), "resource_id": None if code in remedies else run.id}})
    try:
        snapshot = verify_configuration(run, crypto)
    except (ValueError, AnalysisIngestError):
        snapshot = None
    check("candidate_snapshot_valid", snapshot is not None)
    summary = describe_run(db, run, detail=False, reference_basis="initial")
    check("candidate_completed_without_failures", summary.status == "completed" and summary.failed == 0
          and summary.rejected == 0 and summary.completed > 0 and not run.accepting_items)
    check("official_approved_evaluation", run.test_purpose == "official_evaluation"
          and run.evaluation_mode == "ground_truth" and not run.official_evaluation_pending)
    evaluation = db.scalar(select(TestEvaluation).where(TestEvaluation.test_run_id == run.id,
        TestEvaluation.evaluation_kind == "ground_truth").order_by(TestEvaluation.revision.desc()).limit(1))
    version = db.get(ValidationDatasetVersion, run.dataset_version_id) if run.dataset_version_id else None
    approved_ids = run.approved_item_version_ids or []
    approved = list(db.scalars(select(ValidationDatasetItem).where(ValidationDatasetItem.id.in_(approved_ids))))
    from .validation_datasets import digest
    check("published_membership_valid", version and version.is_published and approved_ids and len(approved) == len(approved_ids)
        and sorted(approved_ids) == sorted(version.item_version_ids)
        and version.membership_hash == digest(sorted(version.item_version_ids))
        and all(item.reference_verdict is not None for item in approved))
    frozen = evaluation.summary_json if evaluation else None
    check("official_snapshot_matches_candidate", evaluation and frozen and
        evaluation.configuration_hash == run.configuration_hash and evaluation.metrics_version == METRICS_VERSION
        and frozen.get("status") == "completed" and frozen.get("failed") == 0
        and frozen.get("completed") == len(approved_ids)
        and frozen.get("evaluation_summary", {}).get("evaluable") == len(approved_ids)
        and frozen.get("ground_truth") == ground_truth_metadata(db, run))
    valid_profiles = True
    same_instructions = False
    if snapshot:
        try:
            for role in ("primary", "verifier", "evidence_editor"):
                selected = snapshot[role]
                if role == "evidence_editor" and not selected["enabled"]:
                    continue
                profile = validate_role_profile(db, db.get(VLLMProfile, selected["profile_id"]), selected["profile_fingerprint"])
                if profile.api_key_ciphertext:
                    key = crypto.decrypt_text(profile.api_key_ciphertext)
                    if not key or any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in key):
                        raise ValueError("model_profile_api_key_unavailable")
                if role != "evidence_editor" and not has_input_budget(profile, read_policy_text(get_policy_version(db, snapshot["prompt"]["policy_version_id"]), crypto)):
                    raise ValueError("agent_context_budget_too_small")
            candidate_now = live_snapshot(db, crypto, settings, roles={
                "primary_profile_id": snapshot["primary"]["profile_id"],
                "verifier_profile_id": snapshot["verifier"]["profile_id"],
                "evidence_editor_enabled": snapshot["evidence_editor"]["enabled"],
                "evidence_editor_profile_id": snapshot["evidence_editor"]["profile_id"]},
                prompt_id=snapshot["prompt"]["policy_version_id"], schema_id=snapshot["input_schema"]["version_id"])
            same_instructions = candidate_now == snapshot
        except ValueError:
            valid_profiles = False
    else:
        valid_profiles = False
    check("tested_profiles_still_valid", valid_profiles)
    check("tested_instructions_still_current", same_instructions)
    schema = schema_diff(db, crypto, current["snapshot"]["input_schema"]["version_id"],
        snapshot["input_schema"]["version_id"]) if snapshot and same_instructions else {
            "schema_changed": False, "field_diff": [], "compatibility_warnings": []}
    baseline_eval = official_for_hash(db, current["configuration_hash"])
    left_gt = (baseline_eval.summary_json or {}).get("ground_truth") if baseline_eval else None
    right_gt = frozen.get("ground_truth") if frozen else None
    return {"candidate_test_run_id": run.id, "candidate_name": run.name, "candidate": snapshot,
        "candidate_configuration_hash": run.configuration_hash, "current": current,
        "checks": checks, "eligible": all(c["passed"] for c in checks), **schema,
        "evaluation": official_document(evaluation), "baseline_evaluation": official_document(baseline_eval),
        "comparable": bool(left_gt and right_gt and left_gt.get("comparison_key") and
            left_gt.get("comparison_key") == right_gt.get("comparison_key") and baseline_eval.metrics_version == evaluation.metrics_version)}


def promote(db, crypto, settings, payload, actor):
    write_lock(db)
    ensure_baseline(db, crypto, settings)
    review = preflight(db, crypto, settings, payload.candidate_test_run_id)
    if review["current"]["configuration_hash"] != payload.expected_production_configuration_hash:
        fail("production_configuration_changed")
    if not review["eligible"]:
        fail("candidate_not_eligible_for_promotion")
    if review["schema_changed"] and not payload.acknowledge_schema_change:
        fail("schema_change_ack_required")
    # Same-config submissions can have an unchanged hash. A duplicate click
    # must not append a second successful promotion for the same evaluation.
    prior = db.scalar(select(ProductionPromotion).where(ProductionPromotion.kind == "promotion")
        .order_by(ProductionPromotion.created_at.desc(), ProductionPromotion.id.desc()).limit(1))
    if prior and prior.source_test_run_id == payload.candidate_test_run_id and prior.evaluation_id == review["evaluation"]["id"] and prior.configuration_hash == review["current"]["configuration_hash"]:
        db.commit()
        return {"id": prior.id, "configuration_hash": prior.configuration_hash, "source_test_run_id": prior.source_test_run_id}
    snapshot = review["candidate"]
    config = db.get(AgentConfiguration, 1)
    if config is None:
        config = AgentConfiguration(id=1, revision=0)
        db.add(config)
    for profile in db.scalars(select(VLLMProfile).where(VLLMProfile.status == "production")):
        profile.status = "verified"
    db.flush()
    db.get(VLLMProfile, snapshot["primary"]["profile_id"]).status = "production"
    config.production_verifier_profile_id = snapshot["verifier"]["profile_id"]
    config.production_evidence_editor_enabled = snapshot["evidence_editor"]["enabled"]
    config.production_evidence_editor_profile_id = snapshot["evidence_editor"]["profile_id"]
    config.revision += 1
    policy = db.get(PromptPolicyState, 1)
    policy.active_version_id = snapshot["prompt"]["policy_version_id"]
    policy.revision += 1
    schema = get_schema_state(db, crypto)
    if schema.active_version_id != snapshot["input_schema"]["version_id"]:
        activate_schema_version(db, crypto, snapshot["input_schema"]["version_id"], schema.revision, actor)
    row = ProductionPromotion(kind="promotion", source_test_run_id=payload.candidate_test_run_id,
        evaluation_id=review["evaluation"]["id"], previous_configuration_hash=review["current"]["configuration_hash"],
        configuration_hash=review["candidate_configuration_hash"], snapshot_json=snapshot, actor_id=actor)
    db.add(row)
    db.flush()
    record_change(db, category="promotion", actor=actor, action="promote_production", resource_type="production_configuration",
        resource_id=row.id, before=review["current"]["snapshot"], after=snapshot)
    db.add(AccessAudit(actor_kind="admin_session", actor_id=actor, action="promote_production",
        resource_type="production_configuration", resource_id=row.id))
    # Catch accidental drift before committing all five components.
    if configuration_digest(live_snapshot(db, crypto, settings)) != row.configuration_hash:
        fail("promotion_snapshot_mismatch")
    db.commit()
    return {"id": row.id, "configuration_hash": row.configuration_hash, "source_test_run_id": row.source_test_run_id}
