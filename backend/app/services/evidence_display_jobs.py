"""Encrypted, version-pinned presentation jobs independent of verdict workers."""
import asyncio
from datetime import timedelta
import hashlib
import hmac
import json
import logging
from pathlib import Path
from time import monotonic
import uuid

from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import and_, select, update
from sqlalchemy.orm import Session

from ..models import AgentConfiguration, AgentRun, Analysis, EvidenceDisplayJob, TestRun, VLLMProfile, utcnow

VERSION = "evidence-display-editor-v1"
VIEW_VERSION = "evidence-display-view-v1"
TERMINAL = frozenset({"completed", "skipped", "failed", "canceled"})
LEASE_SECONDS = 120
_FILES = ("../agent/evidence_display.py", "evidence_display_plan.py", "evidence_display_jobs.py",
          "../worker_entrypoint.py")
_UNSET = object()


def implementation_artifact():
    root = Path(__file__).parent
    try:
        files = {name: hashlib.sha256((root / name).read_bytes()).hexdigest() for name in _FILES}
    except OSError:
        raise ValueError("display_editor_implementation_unavailable") from None
    encoded = json.dumps({"version": VERSION, "files": files}, sort_keys=True, separators=(",", ":"))
    return {"version": VERSION, "files": files, "sha256": hashlib.sha256(encoded.encode()).hexdigest()}


class DisplayEditorSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    version: str = Field(pattern=r"^evidence-display-editor-v1$")
    instructions: str = Field(min_length=1, max_length=4000)
    instructions_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    profile_id: str = Field(min_length=1, max_length=36)
    profile_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    implementation: dict
    implementation_hash: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def binding(self):
        from ..agent.evidence_display import INSTRUCTIONS
        expected = implementation_artifact()
        if (self.instructions != INSTRUCTIONS
                or hashlib.sha256(self.instructions.encode()).hexdigest() != self.instructions_hash
                or self.implementation != expected
                or not hmac.compare_digest(self.implementation_hash, expected["sha256"])):
            raise ValueError("display_editor_snapshot_invalid")
        return self


def capture_display_profile(profile):
    from ..agent.evidence_display import INSTRUCTIONS
    from .vllm_profiles import profile_fingerprint
    artifact = implementation_artifact()
    return DisplayEditorSnapshot(version=VERSION, instructions=INSTRUCTIONS,
        instructions_hash=hashlib.sha256(INSTRUCTIONS.encode()).hexdigest(), profile_id=profile.id,
        profile_fingerprint=profile_fingerprint(profile), implementation=artifact, implementation_hash=artifact["sha256"])


def load_snapshot(crypto, ciphertext):
    try:
        return DisplayEditorSnapshot.model_validate_json(crypto.decrypt_text(ciphertext))
    except (ValueError, TypeError, UnicodeError):
        raise ValueError("display_editor_snapshot_invalid") from None


def editor_metadata(snapshot):
    return {"enabled": True, "profile_id": snapshot.profile_id, "profile_fingerprint": snapshot.profile_fingerprint,
        "version": snapshot.version, "instructions_hash": snapshot.instructions_hash,
        "implementation_hash": snapshot.implementation_hash}


def capture_for_purpose(db, crypto, purpose):
    """Existing approvals select old behavior; only new candidates use v1."""
    from .agent_configuration import validate_role_profile
    from .candidate_configurations import configuration_digest
    from .prompt_snapshots import approved_production_record
    config = db.get(AgentConfiguration, 1)
    if not config or not getattr(config, purpose + "_evidence_editor_enabled", False):
        return None
    if purpose == "production":
        record = approved_production_record(db)
        expected = (record.snapshot_json or {}).get("evidence_editor", {}) if record else {}
        if expected.get("version") != VERSION:
            return None
        if record.configuration_hash != configuration_digest(record.snapshot_json):
            raise ValueError("display_editor_approval_invalid")
        if record.source_test_run_id:
            run = db.get(TestRun, record.source_test_run_id)
            if run is None or not run.display_editor_snapshot_ciphertext:
                raise ValueError("display_editor_approval_invalid")
            selected = load_snapshot(crypto, run.display_editor_snapshot_ciphertext)
        else:
            profile = validate_role_profile(db, db.get(VLLMProfile, expected["profile_id"]), expected["profile_fingerprint"])
            selected = capture_display_profile(profile)
        if editor_metadata(selected) != expected:
            raise ValueError("display_editor_approval_invalid")
        return selected
    primary = db.scalar(select(VLLMProfile).where(VLLMProfile.is_test.is_(True)))
    identifier = getattr(config, purpose + "_evidence_editor_profile_id", None) or (primary.id if primary else None)
    return capture_display_profile(validate_role_profile(db, db.get(VLLMProfile, identifier))) if identifier else None


def pin_job(db, crypto, analysis, snapshot):
    if snapshot is None or analysis.model_test_run_id:
        return None
    selected = DisplayEditorSnapshot.model_validate(snapshot.model_dump())
    db.flush()
    existing = db.scalar(select(EvidenceDisplayJob).where(EvidenceDisplayJob.analysis_id == analysis.id))
    if existing:
        if load_snapshot(crypto, existing.snapshot_ciphertext) != selected:
            raise ValueError("display_editor_snapshot_conflict")
        return existing
    row = EvidenceDisplayJob(analysis_id=analysis.id, snapshot_ciphertext=crypto.encrypt_text(selected.model_dump_json()),
        encryption_key_version=crypto.key_version, status="pending", metadata_json={})
    db.add(row)
    db.flush()
    return row


def copy_job(db, crypto, original, child):
    old = db.scalar(select(EvidenceDisplayJob).where(EvidenceDisplayJob.analysis_id == original.id))
    if old is None:
        return None
    selected = load_snapshot(crypto, old.snapshot_ciphertext)
    row = pin_job(db, crypto, child, selected)
    # The immutable instruction ciphertext is copied, never recaptured.
    row.snapshot_ciphertext = old.snapshot_ciphertext
    return row


def has_job_for_active_run(db):
    identifier = db.info.get("worker_run_id")
    return bool(identifier and db.scalar(select(EvidenceDisplayJob.id).join(AgentRun,
        AgentRun.analysis_id == EvidenceDisplayJob.analysis_id).where(AgentRun.id == identifier)))


def _relations(db, crypto, analysis):
    from .evidence_display_access import read_evidence_relations
    return read_evidence_relations(db, crypto, analysis)


def _fallback(result, relations, *, status="fallback", reason=None):
    from .evidence_display_plan import build_display_plan
    try:
        return build_display_plan(result, relations=relations, status=status, reason=reason)
    except (ValueError, TypeError, KeyError):
        return None


def get_display_view(db, crypto, analysis, *, relations=_UNSET, job=_UNSET):
    from .evidence_display_plan import source_hash, validate_display_plan
    row = db.scalar(select(EvidenceDisplayJob).where(EvidenceDisplayJob.analysis_id == analysis.id)) if job is _UNSET else job
    if relations is _UNSET:
        relations = _relations(db, crypto, analysis)
    result = analysis.result_json if isinstance(analysis.result_json, dict) else {}
    view = {"version": VIEW_VERSION, "status": "disabled", "plan": None, "relations": relations, "metadata": {}}
    if row is None:
        view["plan"] = _fallback(result, relations, status="skipped", reason="display_disabled")
        return view
    allowed = {"source_count", "group_count", "displayed_count", "model_called", "duration_ms"}
    view["metadata"] = {key: value for key, value in (row.metadata_json or {}).items()
        if key in allowed and isinstance(value, (int, bool))}
    if row.error_code:
        view["metadata"]["reason"] = (row.error_code if row.error_code in {
            "analysis_not_completed", "display_lease_expired"} else _safe_failure_code(ValueError(row.error_code)))
    view["status"] = row.status if row.status in {"pending", "running", "completed", "skipped"} else "fallback"
    try:
        load_snapshot(crypto, row.snapshot_ciphertext)
        if row.status in {"completed", "skipped"}:
            if row.source_hash != source_hash(result, relations):
                raise ValueError()
            saved = json.loads(crypto.decrypt_text(row.output_ciphertext))
            view["plan"] = validate_display_plan(result, relations, saved["plan"])
        else:
            view["plan"] = _fallback(result, relations,
                status="fallback" if view["status"] == "fallback" else "pending",
                reason="job_failed" if view["status"] == "fallback" else "job_pending")
    except (ValueError, TypeError, KeyError, UnicodeError):
        view["status"] = "fallback"
        view["plan"] = _fallback(result, relations, reason="display_plan_invalid")
    return view


def get_saved_display_plan(db, crypto, analysis, *, relations=_UNSET, job=_UNSET):
    return get_display_view(db, crypto, analysis, relations=relations, job=job)["plan"]


def get_display_views(db, crypto, analyses, relations_by_id):
    jobs = {row.analysis_id: row for row in db.scalars(select(EvidenceDisplayJob).where(
        EvidenceDisplayJob.analysis_id.in_([row.id for row in analyses])))} if analyses else {}
    return {row.id: get_display_view(db, crypto, row, relations=relations_by_id.get(row.id),
        job=jobs.get(row.id)) for row in analyses}


def qualification(db, run, crypto=None):
    """Only the selected current attempts count, never an ancestor's display."""
    if not run.display_editor_snapshot_ciphertext:
        return {"required": False, "ready": True, "pending": False, "failed": 0}
    from .test_attempts import test_attempts
    _, current = test_attempts(run.id)
    rows = list(db.execute(select(Analysis.id, Analysis.status, EvidenceDisplayJob).join(current,
        current.c.analysis_id == Analysis.id).outerjoin(EvidenceDisplayJob,
        EvidenceDisplayJob.analysis_id == Analysis.id)))
    pending, failed = False, 0
    expected = load_snapshot(crypto, run.display_editor_snapshot_ciphertext) if crypto else None
    for _identifier, state, job in rows:
        if state != "completed":
            failed += int(state in {"failed", "canceled"})
            pending |= state in {"pending", "processing"}
        elif job is None:
            failed += 1
        elif job.status in {"pending", "running"}:
            pending = True
        elif job.status not in {"completed", "skipped"}:
            failed += 1
        elif crypto:
            try:
                if load_snapshot(crypto, job.snapshot_ciphertext) != expected:
                    raise ValueError()
                analysis = db.get(Analysis, job.analysis_id)
                if get_display_view(db, crypto, analysis)["status"] not in {"completed", "skipped"}:
                    raise ValueError()
            except (ValueError, TypeError, KeyError):
                failed += 1
    return {"required": True, "ready": bool(rows) and not pending and failed == 0,
            "pending": pending, "failed": failed}


def claim_next(db, owner):
    now = utcnow()
    # Unknown completion after a process crash is never re-sent to the model.
    db.execute(update(EvidenceDisplayJob).where(EvidenceDisplayJob.status == "running",
        EvidenceDisplayJob.lease_expires_at < now).values(status="failed", error_code="display_lease_expired",
        completed_at=now, lease_owner=None, lease_expires_at=None))
    db.execute(update(EvidenceDisplayJob).where(EvidenceDisplayJob.status == "pending",
        EvidenceDisplayJob.analysis_id.in_(select(Analysis.id).where(Analysis.status.in_(["failed", "canceled"])))).values(
            status="canceled", error_code="analysis_not_completed", completed_at=now))
    identifier = db.scalar(select(EvidenceDisplayJob.id).join(Analysis,
        Analysis.id == EvidenceDisplayJob.analysis_id).where(EvidenceDisplayJob.status == "pending",
        Analysis.status == "completed").order_by(EvidenceDisplayJob.created_at, EvidenceDisplayJob.id).limit(1))
    if identifier is None:
        db.commit()
        return None
    claimed = db.execute(update(EvidenceDisplayJob).where(EvidenceDisplayJob.id == identifier,
        EvidenceDisplayJob.status == "pending").values(status="running", lease_owner=owner,
        lease_expires_at=now + timedelta(seconds=LEASE_SECONDS), started_at=now,
        attempt_count=EvidenceDisplayJob.attempt_count + 1))
    db.commit()
    return identifier if claimed.rowcount == 1 else None


def process_job(session_factory, crypto, identifier, owner, *, stop=None):
    from ..agent.evidence_display import (DisplayEditorOutput, build_editor_input, serialized_input, input_fits)
    from ..agent.executor_v221 import execute_structured_agent
    from .agent_configuration import role_request_check, validate_role_profile
    from .evidence_display_plan import build_display_plan, source_hash
    from .test_runs import analysis_test_run, named_test_request_check
    from .official_evaluations import finalize_for_analysis
    started = monotonic()

    def owned_lease():
        # Fence every side-effect, not just the preceding read. Another process
        # may have recovered an expired lease between those two operations.
        return and_(EvidenceDisplayJob.id == identifier, EvidenceDisplayJob.status == "running",
            EvidenceDisplayJob.lease_owner == owner, EvidenceDisplayJob.lease_expires_at > utcnow())

    def check(*, admission=False):
        if admission and stop is not None and stop.is_set():
            raise ValueError("display_worker_stopping")
        with session_factory() as latest:
            job = latest.get(EvidenceDisplayJob, identifier)
            if not job or job.status != "running" or job.lease_owner != owner:
                raise ValueError("display_lease_lost")
            expires = job.lease_expires_at
            if expires is None or expires.replace(tzinfo=utcnow().tzinfo) <= utcnow():
                raise ValueError("display_lease_lost")
            analysis = latest.get(Analysis, job.analysis_id)
            if not analysis or analysis.status != "completed":
                raise ValueError("display_source_unavailable")
            run = analysis_test_run(latest, analysis)
            if run is not None:
                named_test_request_check(latest.get_bind(), run.id)()

    with session_factory() as db:
        job = db.get(EvidenceDisplayJob, identifier)
        if not job or job.lease_owner != owner or job.status != "running":
            return
        analysis_id = job.analysis_id
        call, model_called = None, False
        try:
            check(admission=True)
            selected = load_snapshot(crypto, job.snapshot_ciphertext)
            profile = validate_role_profile(db, db.get(VLLMProfile, selected.profile_id), selected.profile_fingerprint)
            analysis = db.get(Analysis, analysis_id)
            relations = _relations(db, crypto, analysis)
            from ..result_contracts import validate_new_result
            result = validate_new_result(analysis.result_json)
            binding = source_hash(result, relations)
            document = build_editor_input(result, relations=relations)
            if job.input_ciphertext is not None:
                raise ValueError("display_input_already_dispatched")
            captured = db.execute(update(EvidenceDisplayJob).execution_options(synchronize_session=False).where(owned_lease(),
                EvidenceDisplayJob.input_ciphertext.is_(None)).values(
                    input_ciphertext=crypto.encrypt_text(json.dumps({"result": result, "relations": relations,
                        "editor_input": document}, ensure_ascii=False, separators=(",", ":"))), source_hash=binding))
            if captured.rowcount != 1:
                raise ValueError("display_lease_lost")
            db.commit()
            if document is None:
                plan = build_display_plan(result, relations=relations, status="skipped", reason="no_duplicate_candidates")
                status = "skipped"
            else:
                text = serialized_input(document)
                if not input_fits(profile, selected.instructions, text):
                    raise ValueError("display_input_budget_exceeded")
                key = crypto.decrypt_text(profile.api_key_ciphertext) if profile.api_key_ciphertext else None
                request_check = role_request_check(db.get_bind(), profile, lambda: check(admission=True),
                    internal_only=analysis.internal_only)
                model_called = True
                dispatched = db.execute(update(EvidenceDisplayJob).execution_options(synchronize_session=False).where(owned_lease()).values(
                    metadata_json={"model_called": True}))
                if dispatched.rowcount != 1:
                    raise ValueError("display_lease_lost")
                db.commit()  # No DB transaction is held during queueing/model I/O.
                call = asyncio.run(asyncio.wait_for(execute_structured_agent(profile=profile, api_key=key,
                    instructions=selected.instructions, user_input=text, session_id=identifier + ":display",
                    agent_name="waf-evidence-display-editor", egress_check=request_check, concurrency_engine=db.get_bind(),
                    output_model=DisplayEditorOutput, output_validation_max_attempts=1,
                    structured_output_recovery=True), timeout=90))
                if not call.succeeded:
                    summary = call.telemetry.get("error_summary", {})
                    code = summary.get("code") if isinstance(summary, dict) else None
                    if code in {"model_output_incomplete", "vllm_output_incomplete", "openai_output_incomplete"}:
                        raise ValueError("display_output_incomplete")
                    if code == "output_validation_failed":
                        raise ValueError("display_output_invalid")
                    raise ValueError("display_call_failed")
                plan = build_display_plan(result, relations=relations, group_output=call.output, status="completed")
                status = "completed"
            check()
            db.expire_all()
            analysis = db.get(Analysis, analysis_id)
            if binding != source_hash(analysis.result_json, _relations(db, crypto, analysis)):
                raise ValueError("display_source_changed")
            output = crypto.encrypt_text(json.dumps({"plan": plan,
                "model_output": call.output.model_dump(mode="json") if call and call.output else None},
                ensure_ascii=False, separators=(",", ":")))
            metadata = {"source_count": len(result.get("evidence", [])),
                "group_count": len(plan.get("groups", [])), "model_called": model_called,
                "duration_ms": round((monotonic() - started) * 1000)}
            saved = db.execute(update(EvidenceDisplayJob).execution_options(synchronize_session=False).where(owned_lease()).values(
                output_ciphertext=output, status=status, error_code=None, metadata_json=metadata,
                completed_at=utcnow(), lease_owner=None, lease_expires_at=None))
            if saved.rowcount != 1:
                raise ValueError("display_lease_lost")
            db.commit()
        except Exception as exc:
            db.rollback()
            values = {"status": "failed", "error_code": _safe_failure_code(exc),
                "completed_at": utcnow(), "lease_owner": None, "lease_expires_at": None,
                "metadata_json": {"model_called": model_called, "duration_ms": round((monotonic() - started) * 1000)}}
            if call and call.output is not None:
                values["output_ciphertext"] = crypto.encrypt_text(json.dumps({"plan": None,
                    "model_output": call.output.model_dump(mode="json")}, ensure_ascii=False, separators=(",", ":")))
            db.execute(update(EvidenceDisplayJob).where(EvidenceDisplayJob.id == identifier,
                EvidenceDisplayJob.status == "running", EvidenceDisplayJob.lease_owner == owner).values(**values))
            db.commit()
    # A display write failure can never rewrite a completed analysis.
    finalize_for_analysis(session_factory, analysis_id)


def _safe_failure_code(exc):
    if isinstance(exc, (TimeoutError, asyncio.TimeoutError)):
        return "display_timeout"
    known = {"display_lease_lost", "display_source_unavailable", "display_input_already_dispatched",
        "display_input_budget_exceeded", "display_source_changed", "display_snapshot_invalid",
        "display_editor_snapshot_invalid", "display_output_invalid", "display_output_incomplete", "display_call_failed",
        "display_groups_invalid", "display_support_mismatch", "display_condition_mismatch", "display_dependency_mismatch",
        "display_source_invalid", "display_plan_invalid", "display_worker_stopping", "display_test_stopped",
        "display_timeout", "display_profile_unavailable", "display_egress_blocked", "display_editor_unavailable"}
    candidate = getattr(exc, "code", None)
    if candidate is None and len(exc.args) == 1 and isinstance(exc.args[0], str):
        candidate = exc.args[0]
    if candidate in known:
        return candidate
    if candidate == "test_run_stopped":
        return "display_test_stopped"
    if candidate in {"agent_profile_unavailable", "agent_profile_changed", "agent_profile_not_verified"}:
        return "display_profile_unavailable"
    if candidate in {"dataset_internal_models_required", "vllm_target_not_allowed", "openai_profile_unavailable"}:
        return "display_egress_blocked"
    return "display_editor_unavailable"


def serve(session_factory, crypto, settings, worker_id, stop):
    while not stop.is_set():
        try:
            owner = worker_id + ":display:" + str(uuid.uuid4())
            with session_factory() as db:
                identifier = claim_next(db, owner)
            if identifier:
                process_job(session_factory, crypto, identifier, owner, stop=stop)
            else:
                stop.wait(min(settings.worker_poll_seconds, 5))
        except Exception as exc:
            # A coordination or DB error must not silently kill the display
            # lane. Never include exception text, source data or model output.
            logging.getLogger("waf-worker").error("display lane recovery failed error_type=%s", type(exc).__name__)
            stop.wait(min(settings.worker_poll_seconds, 5))
