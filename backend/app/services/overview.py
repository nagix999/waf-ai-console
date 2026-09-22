"""Overview context is anchored to current Production's published evaluation.

Comparison keys are server-owned. No user-selected or arbitrary fallback
dataset can accidentally look like a Production quality trend.
"""
from sqlalchemy import func, select
from ..models import Analysis, ProductionPromotion, TestEvaluation, TestRun, ValidationDataset, utcnow
from .production_configurations import current_configuration, official_for_hash, official_document, preflight
from .ground_truth_working import document
from .analysis import AnalysisIngestError
from .test_attempts import failed_test_ids


def overview_document(db, crypto, settings):
    current = current_configuration(db, crypto, settings)
    latest = official_for_hash(db, current["configuration_hash"])
    ground_truth = (latest.summary_json or {}).get("ground_truth") if latest else None
    comparison_key = ground_truth.get("comparison_key") if ground_truth else None
    working = None
    if ground_truth:
        dataset = db.get(ValidationDataset, ground_truth["dataset_id"])
        if dataset and not dataset.deleted_at:
            data = document(db, dataset.id, crypto)
            working = {k: data[k] for k in ("id", "name", "working_revision", "total", "counts", "changes",
                "working_changes_count", "latest_published_revision_id", "published_revisions")}
    records = list(db.scalars(select(TestEvaluation).where(TestEvaluation.evaluation_kind == "ground_truth")
        .order_by(TestEvaluation.created_at.desc(), TestEvaluation.id.desc()).limit(500)))
    def same(record):
        return bool(comparison_key and record.summary_json and
            (record.summary_json.get("ground_truth") or {}).get("comparison_key") == comparison_key)
    comparable = [record for record in records if same(record)]
    promoted = {r.configuration_hash for r in db.scalars(select(ProductionPromotion))}
    promoted.add(current["configuration_hash"])
    trend, seen_configs = [], set()
    recent, seen_runs = [], set()
    for record in comparable:
        if record.test_run_id not in seen_runs and len(recent) < 5:
            recent.append(official_document(record))
            seen_runs.add(record.test_run_id)
        if record.configuration_hash in promoted and record.configuration_hash not in seen_configs:
            trend.append(official_document(record))
            seen_configs.add(record.configuration_hash)
    actions = []
    attention = []
    for dataset in db.scalars(select(ValidationDataset).where(ValidationDataset.deleted_at.is_(None))):
        data = document(db, dataset.id, crypto)
        if data["counts"]["needs_attention"]:
            attention.append({"dataset_id": dataset.id, "name": dataset.name, "count": data["counts"]["needs_attention"]})
    if attention:
        actions.append({"kind": "ground_truth", "count": sum(v["count"] for v in attention), "datasets": attention})
    failed_tests = db.scalar(select(func.count()).select_from(failed_test_ids().subquery()))
    if failed_tests:
        actions.append({"kind": "failed_tests", "count": failed_tests})
    failed_inference = db.scalar(select(func.count()).select_from(Analysis).where(
        Analysis.status == "failed", Analysis.analysis_purpose == "production"))
    if failed_inference:
        actions.append({"kind": "failed_inference", "count": failed_inference})
    # Eligibility is checked again on the full page and within promotion's write
    # transaction. This action is a navigation affordance, never authorization.
    for record in records[:20]:
        if record.configuration_hash == current["configuration_hash"] or not (record.summary_json or {}).get("ground_truth", {}).get("published"):
            continue
        try:
            review = preflight(db, crypto, settings, record.test_run_id)
        except (AnalysisIngestError, ValueError):
            continue
        if review["eligible"]:
            actions.append({"kind": "promotion", "test_run_id": record.test_run_id, "name": review["candidate_name"]})
            break
    from .setup_status import document as setup_document
    setup = setup_document(db, crypto, settings)
    if setup["production_state"] == "promoted":
        if setup["production_api_credentials"] != "ready":
            actions.append({"kind": "production_credentials"})
        if setup["production_api_traffic"]["status"] == "not_observed":
            actions.append({"kind": "production_traffic"})
    for record in recent:
        run = db.get(TestRun, record["test_run_id"])
        record.update(name=run.name, configuration_snapshot=run.configuration_snapshot_json,
            profile_metadata=run.profile_metadata, prompt_policy_version_id=run.prompt_policy_version_id,
            input_schema_version_id=run.input_schema_version_id)
    return {"production": current, "setup": setup,
        "ground_truth_needs_attention_total": sum(v["count"] for v in attention),
        "ground_truth_needs_attention_dataset_count": len(attention), "ground_truth_needs_attention_datasets": attention,
        "evaluation": official_document(latest), "comparison_key": comparison_key,
        "trend": list(reversed(trend)), "ground_truth_working_draft": working,
        "recent_comparable_tests": recent, "actions": actions, "updated_at": utcnow()}
