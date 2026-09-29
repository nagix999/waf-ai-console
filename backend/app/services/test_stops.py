"""Stop current test attempts atomically, without rewriting completed history."""
from sqlalchemy import select

from ..models import AccessAudit, AgentRun, AgentStep, Analysis, TestRun, TestRunItem, utcnow
from .analysis import AnalysisIngestError
from .test_attempts import test_attempts
from .test_runs import write_lock

STOP_CODE = "test_run_stopped"


def stop_test_run(db, run_id, actor):
    # Serializes admission, retry creation, terminal worker commits and official
    # evaluation finalization. Never hold this lock during provider I/O.
    write_lock(db)
    run = db.get(TestRun, run_id, populate_existing=True)
    if run is None:
        raise AnalysisIngestError("test_run_not_found", 404)
    if run.model_test_run_id is not None:
        raise AnalysisIngestError("test_run_stop_not_supported_for_model_validation", 409)
    if run.stopped_at is not None:
        db.commit()
        return run
    _, current = test_attempts(run.id)
    targets = list(db.scalars(select(Analysis).join(current, current.c.analysis_id == Analysis.id)
        .join(TestRunItem, TestRunItem.id == current.c.item_id).where(
            TestRunItem.ingest_status == "accepted", Analysis.analysis_purpose == "test",
            Analysis.status.in_(["pending", "processing"]))
        .execution_options(populate_existing=True)))
    if not targets and not run.accepting_items:
        raise AnalysisIngestError("test_run_not_active", 409)
    now = utcnow()
    run.stopped_at, run.stopped_by = now, actor
    run.accepting_items = False
    run.official_evaluation_pending = False
    for analysis in targets:
        analysis.status = "canceled"
        analysis.completed_at = now
        analysis.lease_owner = analysis.lease_expires_at = None
        analysis.error_code = STOP_CODE
        analysis.error_message = None
    ids = [analysis.id for analysis in targets]
    runs = list(db.scalars(select(AgentRun).where(AgentRun.analysis_id.in_(ids), AgentRun.status == "running")))
    for trace in runs:
        trace.status, trace.failure_id, trace.completed_at = "failed", STOP_CODE, now
    steps = db.scalars(select(AgentStep).where(AgentStep.run_id.in_([trace.id for trace in runs]), AgentStep.status == "running"))
    for step in steps:
        step.status, step.completed_at = "failed", now
        metadata = dict(step.metadata_json or {})
        metadata.pop("duration_ms", None)
        step.metadata_json = {**metadata, "error_code": STOP_CODE, "timing_incomplete": True}
    db.add(AccessAudit(actor_kind="admin_session", actor_id=actor, action="stop_test_run",
        resource_type="test_run", resource_id=run.id))
    db.commit()
    return run
