"""Audited, encrypted local parser comparisons. Never invoke a model here."""
import json
from collections import Counter

from sqlalchemy import func, select

from ..models import AccessAudit, Analysis, ParserJob, TestRun, TestRunItem, utcnow
from .parser_engine import parse_capture_payload, valid_source_spans
from .parser_versions import ParserVersionError, get_version

MAX_SOURCE_BYTES = 64 * 1024
MAX_TOTAL_BYTES = 512 * 1024
COMPARISON_FIELDS = ("parse_status", "method", "uri", "protocol", "query", "headers", "body", "warnings", "raw_source_spans")


def audit(db, actor, action, identifier, resource_type="parser_job", *, actor_kind="admin_session"):
    db.add(AccessAudit(actor_kind=actor_kind, actor_id=actor, action=action,
                       resource_type=resource_type, resource_id=identifier))


def selected_sources(db, crypto, selection, actor):
    """Freeze exact source text. No reference answers or prior AI output."""
    if selection.analysis_ids is not None:
        identifiers = list(dict.fromkeys(selection.analysis_ids))
        total = len(identifiers)
    else:
        run = db.get(TestRun, selection.test_run_id)
        if run is None:
            raise ParserVersionError("parser_test_run_not_found", 404)
        from .test_runs import run_cohort, test_read_context
        _, _, current, _ = test_read_context(db, run, cohort=run_cohort(run.id))
        query = select(current.c.analysis_id).join(TestRunItem, TestRunItem.id == current.c.item_id).where(
            TestRunItem.ingest_status == "accepted").order_by(TestRunItem.row_number, current.c.analysis_id)
        total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
        identifiers = list(db.scalars(query.limit(selection.max_cases)))
    if not identifiers:
        raise ParserVersionError("parser_selection_empty", 422)
    rows = {row.id: row for row in db.execute(select(Analysis.id, Analysis.payload_ciphertext, Analysis.waf_vendor)
            .where(Analysis.id.in_(identifiers)))}
    if len(rows) != len(identifiers):
        raise ParserVersionError("parser_analysis_not_found", 404)
    sources, used = [], 0
    for identifier in identifiers:
        try:
            raw = crypto.decrypt_text(rows[identifier].payload_ciphertext)
            size = len(raw.encode("utf-8"))
        except (ValueError, UnicodeError):
            raise ParserVersionError("parser_source_unreadable", 409) from None
        if size > MAX_SOURCE_BYTES or used + size > MAX_TOTAL_BYTES:
            raise ParserVersionError("parser_source_size_limit", 413)
        used += size
        sources.append({"analysis_id": identifier, "raw_payload": raw, "waf_vendor": rows[identifier].waf_vendor})
        audit(db, actor, "read_raw_for_parser", identifier, "analysis")
    return sources, {"source_total": total, "sample_count": len(sources), "selection_limited": total > len(sources),
                     "source_bytes": used, "max_source_bytes": MAX_SOURCE_BYTES, "max_total_bytes": MAX_TOTAL_BYTES}


def new_job(db, crypto, *, kind, baseline, actor, sources, selection_summary, candidate=None, profile=None):
    from .vllm_profiles import profile_fingerprint
    document = {"schema_version": 1, "sources": sources, "selection": selection_summary,
                "baseline_content_hash": baseline.content_hash}
    if candidate is not None:
        document["candidate_content_hash"] = candidate.content_hash
    row = ParserJob(kind=kind, status="pending", baseline_version_id=baseline.id,
        candidate_version_id=candidate.id if candidate else None,
        profile_id=profile.id if profile else None,
        profile_fingerprint=profile_fingerprint(profile) if profile else None,
        request_ciphertext=crypto.encrypt_text(json.dumps(document, ensure_ascii=False)),
        encryption_key_version=crypto.key_version, created_by=actor, summary_json=selection_summary)
    db.add(row)
    db.flush()
    return row


def compare(db, crypto, payload, actor):
    baseline = get_version(db, payload.baseline_version_id)
    candidate = get_version(db, payload.candidate_version_id)
    sources, selection = selected_sources(db, crypto, payload, actor)
    row = new_job(db, crypto, kind="comparison", baseline=baseline, candidate=candidate,
                  actor=actor, sources=sources, selection_summary=selection)
    row.started_at = utcnow()
    counts = {"baseline": Counter(), "candidate": Counter()}
    items, all_valid, changed = [], True, 0
    for source in sources:
        raw = source["raw_payload"]
        before = parse_capture_payload(raw, baseline.engine_version, baseline.options_json, waf_vendor=source.get("waf_vendor"))
        after = parse_capture_payload(raw, candidate.engine_version, candidate.options_json, waf_vendor=source.get("waf_vendor"))
        changed_fields = [key for key in COMPARISON_FIELDS if before.get(key) != after.get(key)]
        valid = valid_source_spans(raw, before) and valid_source_spans(raw, after)
        all_valid = all_valid and valid
        changed += bool(changed_fields)
        for label, parsed in (("baseline", before), ("candidate", after)):
            counts[label][parsed["parse_status"]] += 1
        items.append({**source, "baseline": before, "candidate": after,
                      "changed_fields": changed_fields, "valid_source_spans": valid})
    summary = {**selection, "total": len(items), "changed_cases": changed,
               "status_counts": {key: {status: value[status] for status in ("success", "partial", "failed")}
                                 for key, value in counts.items()},
               "valid_source_spans": all_valid, "all_valid_source_spans": all_valid, "structure_reviewed": False,
               "baseline_content_hash": baseline.content_hash, "candidate_content_hash": candidate.content_hash}
    row.summary_json = summary
    row.result_ciphertext = crypto.encrypt_text(json.dumps({"summary": summary, "items": items,
        "limits": {"max_cases": payload.max_cases, "max_source_bytes": MAX_SOURCE_BYTES, "max_total_bytes": MAX_TOTAL_BYTES},
        "result_truncated": False}, ensure_ascii=False))
    row.status, row.completed_at = "completed", utcnow()
    audit(db, actor, "compare_parsers", row.id)
    return row


def job_summary(row):
    return {"id": row.id, "kind": row.kind, "status": row.status,
            "baseline_version_id": row.baseline_version_id, "candidate_version_id": row.candidate_version_id,
            "profile_id": row.profile_id, "summary": row.summary_json, "error_code": row.error_code,
            "created_by": row.created_by, "created_at": row.created_at,
            "started_at": row.started_at, "completed_at": row.completed_at}


def job_detail(db, crypto, identifier, actor):
    row = db.get(ParserJob, identifier)
    if row is None:
        raise ParserVersionError("parser_job_not_found", 404)
    try:
        result = json.loads(crypto.decrypt_text(row.result_ciphertext)) if row.result_ciphertext else None
    except (ValueError, TypeError):
        raise ParserVersionError("parser_result_unreadable", 409) from None
    audit(db, actor, "view_parser_job", row.id)
    return {**job_summary(row), "result": result}


def review_comparison(db, identifier, actor):
    row = db.get(ParserJob, identifier)
    if row is None:
        raise ParserVersionError("parser_job_not_found", 404)
    summary = row.summary_json
    if row.kind != "comparison" or row.status != "completed" or not row.result_ciphertext:
        raise ParserVersionError("parser_completed_comparison_required")
    candidate = get_version(db, row.candidate_version_id)
    baseline = get_version(db, row.baseline_version_id)
    parsed_count = sum(summary.get("status_counts", {}).get("candidate", {}).get(key, 0) for key in ("success", "partial"))
    if (summary.get("valid_source_spans") is not True or not summary.get("sample_count") or not parsed_count
            or summary.get("candidate_content_hash") != candidate.content_hash
            or summary.get("baseline_content_hash") != baseline.content_hash):
        raise ParserVersionError("parser_comparison_review_unavailable")
    row.summary_json = {**summary, "structure_reviewed": True, "reviewed_by": actor,
                        "reviewed_at": utcnow().isoformat()}
    audit(db, actor, "review_parser_comparison", row.id)
    return row
