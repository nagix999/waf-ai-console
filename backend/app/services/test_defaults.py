"""Visible, complete Test defaults. Never mutate live roles or saved runs."""
from ..candidate_schemas import CandidateConfiguration
from ..models import TestConfigurationDefaults, TestRun, ValidationDatasetVersion, utcnow
from sqlalchemy import select
from .analysis import AnalysisIngestError
from .candidate_configurations import resolve_profiles
from .input_schemas import get_schema_version, load_definition
from .test_runs import write_lock
from .change_events import record_change

FIELDS = tuple(CandidateConfiguration.model_fields)


def validate(db, crypto, value):
    resolve_profiles(db, crypto, value)
    load_definition(get_schema_version(db, value.input_schema_version_id), crypto)


def document(db, crypto):
    row = db.get(TestConfigurationDefaults, 1)
    value = {key: getattr(row, key) for key in FIELDS} if row else None
    valid, reason = False, "test_defaults_not_configured"
    if value:
        try:
            validate(db, crypto, CandidateConfiguration.model_validate(value))
            valid, reason = True, None
        except ValueError as exc:
            reason = getattr(exc, "code", "test_defaults_resources_unavailable")
    return {"candidate_configuration": value, "revision": row.revision if row else 0,
            "updated_at": row.updated_at if row else None, "valid": valid, "reason": reason}


def update(db, crypto, payload, actor):
    write_lock(db)
    before = document(db, crypto)
    if before["revision"] != payload.expected_revision:
        raise AnalysisIngestError("test_defaults_changed", 409)
    validate(db, crypto, payload.candidate_configuration)
    row = db.get(TestConfigurationDefaults, 1)
    if row is None:
        row = TestConfigurationDefaults(id=1)
        db.add(row)
    for key, value in payload.candidate_configuration.model_dump().items():
        setattr(row, key, value)
    row.revision = before["revision"] + 1
    row.updated_at = utcnow()
    db.flush()
    after = document(db, crypto)
    record_change(db, category="configuration", actor=actor, action="save_test_defaults",
        resource_type="test_configuration_defaults", resource_id="1",
        before=before["candidate_configuration"], after=after["candidate_configuration"])
    db.commit()
    return after


def clone_template(db, crypto, run_id):
    run = db.get(TestRun, run_id)
    if run is None:
        raise AnalysisIngestError("test_run_not_found", 404)
    snapshot = run.configuration_snapshot_json or {}
    value = None
    try:
        value = dict(primary_profile_id=snapshot["primary"]["profile_id"],
            verifier_profile_id=snapshot["verifier"]["profile_id"],
            evidence_editor_enabled=snapshot["evidence_editor"]["enabled"],
            evidence_editor_profile_id=snapshot["evidence_editor"]["profile_id"],
            prompt_policy_version_id=snapshot["prompt"]["policy_version_id"],
            input_schema_version_id=snapshot["input_schema"]["version_id"])
        validate(db, crypto, CandidateConfiguration.model_validate(value))
        validity = {"valid": True}
    except (ValueError, KeyError, TypeError) as exc:
        validity = {"valid": False, "reason": getattr(exc, "code", "source_configuration_unavailable")}
    version = db.get(ValidationDatasetVersion, run.dataset_version_id) if run.dataset_version_id else None
    latest = db.scalar(select(ValidationDatasetVersion).where(ValidationDatasetVersion.dataset_id == version.dataset_id,
        ValidationDatasetVersion.is_published.is_(True)).order_by(ValidationDatasetVersion.revision.desc()).limit(1)) if version else None
    return {"candidate_configuration": value, "test_purpose": run.test_purpose,
        "evaluation_mode": run.evaluation_mode, "source_dataset_id": version.dataset_id if version else None,
        "source_dataset_revision_id": version.id if version else None,
        "latest_published_dataset_revision_id": latest.id if latest else None,
        "input_source_kind": run.kind, "resource_validity": validity}
