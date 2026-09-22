"""Observed setup state, independent of promotion eligibility and API traffic."""
from sqlalchemy import select, func
from ..models import (Analysis, ProductionPromotion, ServiceApiKey, TestRun, TestEvaluation,
    ValidationDatasetVersion, VLLMProfile, VLLMTestRun, utcnow)
from .agent_configuration import validate_role_profile
from .production_configurations import current_configuration
from .test_defaults import document as defaults_document
from .test_runs import describe_run


def document(db, crypto, settings):
    current = current_configuration(db, crypto, settings)
    complete = True
    for key in ("primary", "verifier", "evidence_editor"):
        value = current["snapshot"][key]
        if key == "evidence_editor" and not value["enabled"]:
            continue
        try:
            validate_role_profile(db, db.get(VLLMProfile, value["profile_id"]) if value["profile_id"] else None, require_verified=False)
        except ValueError:
            complete = False
    state = "promoted" if current["configuration_id"] else "legacy_active" if complete else "unconfigured"
    verified = False
    for profile in db.scalars(select(VLLMProfile).where(VLLMProfile.status != "disabled")):
        try:
            validate_role_profile(db, profile)
            verified = True
            break
        except ValueError:
            pass
    validating = db.scalar(select(VLLMTestRun.id).where(VLLMTestRun.status.in_(["pending", "running"])).limit(1))
    defaults = defaults_document(db, crypto)
    version = db.scalar(select(ValidationDatasetVersion).where(ValidationDatasetVersion.is_published.is_(True))
        .order_by(ValidationDatasetVersion.created_at.desc()).limit(1))
    run = db.scalar(select(TestRun).join(ValidationDatasetVersion, ValidationDatasetVersion.id == TestRun.dataset_version_id)
        .where(TestRun.test_purpose == "official_evaluation", TestRun.evaluation_mode == "ground_truth",
               ValidationDatasetVersion.is_published.is_(True), TestRun.configuration_hash.is_not(None))
        .order_by(TestRun.created_at.desc()).limit(1))
    official = {"state": "not_started", "test_run_id": None, "processed": None, "execution_failures": None, "total": None}
    if run:
        summary = describe_run(db, run, detail=False)
        evaluation = db.scalar(select(TestEvaluation.id).where(TestEvaluation.test_run_id == run.id,
            TestEvaluation.evaluation_kind == "ground_truth").limit(1))
        official.update(state="in_progress" if summary.status in {"pending", "processing"} else
            "ready" if evaluation and not run.official_evaluation_pending and not run.accepting_items else "needs_attention",
            test_run_id=run.id, processed=summary.completed, execution_failures=summary.failed + summary.rejected,
            total=summary.total, warning_count=summary.failed + summary.rejected)
    steps = {
        "model_connection": {"state": "ready" if verified else "in_progress" if validating else "not_started"},
        "test_configuration": {"state": "ready" if defaults["valid"] else "needs_attention" if defaults["revision"] else "not_started",
            "missing": [defaults["reason"]] if defaults["reason"] else []},
        "ground_truth_published": {"state": "ready" if version and version.item_version_ids else "not_started",
            "dataset_id": version.dataset_id if version else None, "dataset_revision_id": version.id if version else None},
        "official_candidate_test": official,
        "first_promotion": {"state": "ready" if current["configuration_id"] else "not_started", "test_run_id": current["source_test_run_id"]},
    }
    credentials = bool(db.scalar(select(ServiceApiKey.id).where(ServiceApiKey.purpose == "production",
        ServiceApiKey.revoked_at.is_(None), ServiceApiKey.deleted_at.is_(None)).limit(1)))
    traffic = select(func.max(Analysis.created_at)).where(Analysis.analysis_purpose == "production", Analysis.ingest_channel == "service_api")
    if current["applied_at"]:
        traffic = traffic.where(Analysis.created_at >= current["applied_at"])
    observed = db.scalar(traffic)
    next_step = next((key for key, value in steps.items() if value["state"] != "ready"), None)
    return {"production_state": state, "drifted": current["drifted"], "steps": steps,
        "production_api_credentials": "ready" if credentials else "missing",
        "production_api_traffic": {"status": "observed" if observed else "not_observed",
            "last_observed_at": observed, "since": current["applied_at"]},
        "next_action": {"kind": next_step, "resource_id": run.id if next_step == "first_promotion" and run else None}}
