"""Additive administration dispatch; frozen verdict manifests remain unchanged."""
import asyncio

from sqlalchemy import or_, select

from ..models import ParserJob, ParserWorkbenchJob, utcnow


def next_parser_job_kind(session_factory):
    """Both parser generations share the existing single administration lane."""
    with session_factory() as db:
        old = db.scalar(select(ParserJob.created_at).where(ParserJob.kind == "suggestion", or_(
            ParserJob.status == "pending", (ParserJob.status == "running") & (ParserJob.lease_expires_at <= utcnow())
        )).order_by(ParserJob.created_at, ParserJob.id).limit(1))
        new = db.scalar(select(ParserWorkbenchJob.created_at).where(or_(
            ParserWorkbenchJob.status == "pending",
            (ParserWorkbenchJob.status == "running") & (ParserWorkbenchJob.lease_expires_at <= utcnow())
        )).order_by(ParserWorkbenchJob.created_at, ParserWorkbenchJob.id).limit(1))
        if old is None:
            return "workbench" if new is not None else None
        return "workbench" if new is not None and new < old else "legacy"


def pending_parser_job(session_factory):
    return next_parser_job_kind(session_factory) is not None


def run_parser_job(session_factory, crypto, settings, worker_id):
    from .parser_jobs import process_pending_parser_job
    from .parser_workbench_jobs import process_pending_workbench_job
    with session_factory() as db:
        engine = db.get_bind()
    process = process_pending_workbench_job if next_parser_job_kind(session_factory) == "workbench" else process_pending_parser_job
    return asyncio.run(process(settings, engine, crypto, worker_id=worker_id))
