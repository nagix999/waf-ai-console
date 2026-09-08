"""Named administrator tests: immutable membership, bounded metadata, no LLM I/O."""
import hashlib
import json
import uuid
from datetime import UTC, datetime

from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import AccessAudit, Analysis, AnalysisLabel, TestRun, TestRunItem, VLLMProfile
from ..schemas import AnalysisInput
from ..test_run_schemas import TestRunCreate, TestRunDetail, TestRunItemResponse, TestRunSummary
from .analysis import AnalysisIngestError
from .evaluation import evaluation_relation, metadata_from_row, summarize_evaluations
from .internal_egress import allowed_targets_from_db
from .prompt_snapshots import load_analysis_prompt, pin_analysis_prompt
from .input_schemas import InputSchemaError, pin_schema
from .upload_expected_labels import enqueue_test_upload_row
from .uploads import UploadFormatError, extract_test_upload_row, normalize_upload_row
from .vllm_profiles import assignment_block_reason, normalize_and_validate_profile_url, profile_fingerprint, validate_profile_provider_settings
from .vllm_profiles import TargetNotAllowedError

METADATA_FIELDS = {"difficulty": 80, "test_category": 120, "case_name": 240}
MAX_TEST_ITEMS = 5000


def split_test_metadata(row):
    if not isinstance(row, dict):
        raise UploadFormatError("invalid_test_row")
    event = dict(row)
    metadata = {}
    for field, maximum in METADATA_FIELDS.items():
        value = event.pop(field, None)
        if value in (None, ""):
            metadata[field] = None
        elif not isinstance(value, str) or not value.strip() or len(value) > maximum or not value.isprintable():
            raise UploadFormatError("invalid_test_metadata")
        else:
            metadata[field] = value.strip()
    event, expected = extract_test_upload_row(event)
    return event, expected, metadata


def write_lock(db):
    connection = db.connection()
    if connection.dialect.name == "sqlite" and not connection.connection.driver_connection.in_transaction:
        connection.exec_driver_sql("BEGIN IMMEDIATE")


def read_snapshot(db):
    connection = db.connection()
    if connection.dialect.name == "sqlite" and not connection.connection.driver_connection.in_transaction:
        connection.exec_driver_sql("BEGIN")


def create_run_record(db, crypto, settings, *, name, idempotency_key, request_hash,
                      kind, actor, filename=None, dataset_hash=None, model_test=None):
    # Reuse strict public metadata validation without inspecting event contents.
    values = TestRunCreate(name=name, idempotency_key=idempotency_key, event={})
    existing = db.scalar(select(TestRun).where(TestRun.idempotency_key == values.idempotency_key))
    if existing:
        if existing.request_hash != request_hash:
            raise AnalysisIngestError("test_run_idempotency_conflict", 409)
        return existing, True
    profile = db.get(VLLMProfile, model_test.profile_id) if model_test else db.scalar(
        select(VLLMProfile).where(VLLMProfile.is_test.is_(True)))
    mode = "moduagent" if model_test else settings.agent_mode
    if mode == "moduagent" and profile is None:
        raise AnalysisIngestError("test_model_profile_required", 409)
    metadata = {"verifier_confidence_threshold": settings.verifier_confidence_threshold}
    if mode == "moduagent":
        if model_test is None:
            reason = assignment_block_reason(db, profile)
            if reason:
                raise AnalysisIngestError(reason, 409)
        normalize_and_validate_profile_url(profile, allowed_targets_from_db(db) if profile.provider == "vllm" else "")
        validate_profile_provider_settings(profile, has_api_key=bool(profile.api_key_ciphertext))
        metadata.update(llm_provider=profile.provider, model_profile=profile.name,
                        model_profile_id=profile.id, model_name=profile.model_name,
                        profile_fingerprint=profile_fingerprint(profile))
    else:
        profile = None
        metadata.update(model_profile="stub", llm_called=False)
    temporary = Analysis()
    pin_analysis_prompt(db, crypto, temporary)
    identifier = str(uuid.uuid4())
    run = TestRun(
        id=identifier, name=values.name, idempotency_key=values.idempotency_key,
        request_hash=request_hash, kind=kind,
        source_system=(f"waf-internal-model-test-{model_test.id}" if model_test else f"waf-internal-test-run-{identifier}"),
        filename=filename, dataset_hash=dataset_hash,
        model_test_run_id=model_test.id if model_test else None,
        profile_id=profile.id if profile else None,
        profile_fingerprint=profile_fingerprint(profile) if profile else None,
        profile_metadata=metadata, execution_mode=mode,
        prompt_snapshot_ciphertext=temporary.prompt_snapshot_ciphertext,
        prompt_policy_version_id=temporary.prompt_policy_version_id,
        prompt_version=temporary.prompt_version, encryption_key_version=crypto.key_version,
        created_by=actor,
    )
    try:
        if model_test is not None:
            pin_schema(db, crypto, model_test, selection_origin="model_validation")
            run.input_schema_version_id = model_test.input_schema_version_id
            run.input_schema_snapshot_ciphertext = model_test.input_schema_snapshot_ciphertext
        else:
            pin_schema(db, crypto, run, selection_origin="test_run")
    except InputSchemaError as exc:
        raise AnalysisIngestError(exc.code, exc.status_code) from None
    db.add(run)
    db.flush()
    return run, False


def pin_item_prompt(analysis, run):
    analysis.prompt_snapshot_ciphertext = run.prompt_snapshot_ciphertext
    analysis.prompt_policy_version_id = run.prompt_policy_version_id
    analysis.prompt_version = run.prompt_version


def add_run_items(db, crypto, settings, run, rows, *, ai_visible=None, source_ref="test-upload:expected_verdict"):
    if not rows or len(rows) > MAX_TEST_ITEMS:
        raise AnalysisIngestError("test_item_count_out_of_range", 422)
    try:
        snapshot = pin_schema(db, crypto, run, selection_origin="legacy_default")
    except InputSchemaError as exc:
        raise AnalysisIngestError(exc.code, exc.status_code) from None
    prompt = load_analysis_prompt(run, crypto)
    for number, raw in enumerate(rows, 1):
        item = TestRunItem(test_run_id=run.id, row_number=number, ingest_status="rejected")
        try:
            event, expected, metadata = split_test_metadata(raw)
            for key, value in metadata.items():
                setattr(item, key, value)
            candidate_id = event.get("event_id")
            if isinstance(candidate_id, str) and 1 <= len(candidate_id) <= 255 and candidate_id.isprintable():
                item.event_id = candidate_id
            payload = AnalysisInput.model_validate(normalize_upload_row(event))
            item.event_id = payload.event_id
            # A failed row does not roll back preceding accepted rows. Outer
            # transaction keeps the run and its complete membership atomic.
            with db.begin_nested():
                first = db.scalar(select(TestRunItem).where(TestRunItem.test_run_id == run.id,
                    TestRunItem.event_id == payload.event_id, TestRunItem.ingest_status == "accepted"))
                if first is not None and expected is not None:
                    label = db.get(AnalysisLabel, first.label_id) if first.label_id else None
                    if label is None or label.verdict != expected:
                        raise AnalysisIngestError("expected_verdict_conflict", 409)
                analysis, duplicate, _ = enqueue_test_upload_row(
                    db, crypto, run.source_system, payload, expected_verdict=expected,
                    actor=run.created_by, commit=False, payload_max_bytes=settings.payload_max_bytes,
                    attachment_id=run.model_test_run_id or run.id, ai_visible=ai_visible, label_source_ref=source_ref,
                    ingest_channel="model_validation" if run.model_test_run_id else "test_lab" if run.kind == "direct" else "file_upload",
                    schema_snapshot=snapshot,
                    prompt_snapshot=prompt,
                )
                if not duplicate:
                    pin_item_prompt(analysis, run)
                if run.model_test_run_id:
                    analysis.model_test_run_id = run.model_test_run_id
                item.analysis_id = analysis.id
                item.ingest_status = "duplicate" if duplicate else "accepted"
                item.label_id = db.scalar(select(AnalysisLabel.id).where(AnalysisLabel.analysis_id == analysis.id)
                    .order_by(AnalysisLabel.revision).limit(1))
                item.error_code = None
        except AnalysisIngestError as exc:
            if run.model_test_run_id:
                raise
            item.error_code = exc.code
        except UploadFormatError as exc:
            if run.model_test_run_id:
                raise AnalysisIngestError("invalid_model_validation_metadata", 422) from None
            item.error_code = str(exc)
        except (ValidationError, ValueError, OverflowError):
            if run.model_test_run_id:
                raise AnalysisIngestError("invalid_model_validation_event", 422) from None
            item.error_code = "invalid_test_event"
        db.add(item)
        db.flush()


def enqueue_named_run(db, crypto, settings, *, name, idempotency_key, rows, kind, actor,
                      filename=None, content_hash=None):
    try:
        try:
            serialized = json.dumps({"name": name, "kind": kind, "rows": rows, "filename": filename},
                                    sort_keys=True, ensure_ascii=False, allow_nan=False)
            digest = hashlib.sha256(serialized.encode()).hexdigest()
        except (ValueError, TypeError, RecursionError, UnicodeError):
            raise AnalysisIngestError("invalid_test_document", 422) from None
        write_lock(db)
        run, duplicate = create_run_record(db, crypto, settings, name=name, idempotency_key=idempotency_key,
            request_hash=digest, kind=kind, actor=actor, filename=filename, dataset_hash=content_hash)
        if not duplicate:
            add_run_items(db, crypto, settings, run, rows)
            db.add(AccessAudit(actor_kind="admin_session", actor_id=actor, action="create_test_run",
                resource_type="test_run", resource_id=run.id))
        db.commit()
        return run, duplicate
    except Exception:
        db.rollback()
        raise


def fixed_reference_relation(run_id):
    labels = select(AnalysisLabel).join(TestRunItem, TestRunItem.label_id == AnalysisLabel.id).where(
        TestRunItem.test_run_id == run_id, TestRunItem.ingest_status == "accepted").subquery()
    return evaluation_relation(labels)


def run_cohort(run_id, difficulty=None, test_category=None, difficulty_missing=False, test_category_missing=False):
    conditions = [TestRunItem.test_run_id == run_id]
    if difficulty is not None:
        conditions.append(TestRunItem.difficulty == difficulty)
    if test_category is not None:
        conditions.append(TestRunItem.test_category == test_category)
    if difficulty_missing:
        conditions.append(TestRunItem.difficulty.is_(None))
    if test_category_missing:
        conditions.append(TestRunItem.test_category.is_(None))
    return conditions


def describe_run(db, run, *, limit=50, offset=0, difficulty=None, test_category=None,
                 status=None, evaluation_outcome=None, reference_verdict=None, verdict=None, detail=True,
                 difficulty_missing=False, test_category_missing=False):
    read_snapshot(db)
    cohort = run_cohort(run.id, difficulty, test_category, difficulty_missing, test_category_missing)
    accepted_ids = select(TestRunItem.analysis_id).where(*cohort, TestRunItem.ingest_status == "accepted")
    relation = fixed_reference_relation(run.id)
    evaluation_summary = summarize_evaluations(db, relation, [Analysis.id.in_(accepted_ids)])
    counts = dict(db.execute(select(TestRunItem.ingest_status, func.count()).where(
        *cohort).group_by(TestRunItem.ingest_status)).all())
    processing = dict(db.execute(select(Analysis.status, func.count()).where(
        Analysis.id.in_(accepted_ids)).group_by(Analysis.status)).all())
    state = "processing" if processing.get("processing") else "pending" if processing.get("pending") else (
        "failed" if processing.get("failed") or counts.get("rejected") else "completed")
    started, finished = db.execute(select(func.min(Analysis.started_at), func.max(Analysis.completed_at)).where(
        Analysis.id.in_(accepted_ids))).one()
    if state in {"pending", "processing"}:
        finished = None
    end = finished or (run.created_at if not processing else datetime.now(UTC))
    utc = lambda value: value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
    elapsed = max(0, int((utc(end) - utc(run.created_at)).total_seconds() * 1000))
    summary = TestRunSummary(
        id=run.id, name=run.name, kind=run.kind, source_system=run.source_system, created_at=run.created_at,
        status=state, total=sum(counts.values()), accepted=counts.get("accepted", 0),
        duplicates=counts.get("duplicate", 0), rejected=counts.get("rejected", 0),
        pending=processing.get("pending", 0), processing=processing.get("processing", 0),
        completed=processing.get("completed", 0), failed=processing.get("failed", 0),
        execution_mode=run.execution_mode, profile_metadata=run.profile_metadata,
        prompt_version=run.prompt_version, model_test_run_id=run.model_test_run_id,
        prompt_policy_version_id=run.prompt_policy_version_id,
        evaluation_summary=evaluation_summary,
        started_at=started, completed_at=finished, total_elapsed_ms=elapsed,
    )
    if not detail:
        return summary
    query = select(TestRunItem, Analysis, relation).outerjoin(Analysis, Analysis.id == TestRunItem.analysis_id).outerjoin(
        relation, relation.c.analysis_id == Analysis.id).where(*cohort)
    if status is not None:
        query = query.where(Analysis.status == status)
    if evaluation_outcome is not None:
        query = query.where(relation.c.outcome == evaluation_outcome)
    if reference_verdict is not None:
        query = query.where(relation.c.reference_verdict == reference_verdict)
    if verdict is not None:
        query = query.where(Analysis.verdict == verdict)
    total_items = db.scalar(select(func.count()).select_from(query.subquery())) or 0
    items = []
    for row in db.execute(query.order_by(TestRunItem.row_number).limit(limit).offset(offset)):
        item, analysis = row[0], row[1]
        metadata = metadata_from_row(row._mapping) if analysis else None
        items.append(TestRunItemResponse(
            **{key: getattr(item, key) for key in ("id", "row_number", "analysis_id", "event_id", "difficulty",
                "test_category", "case_name", "ingest_status")},
            error_code=item.error_code or (analysis.error_code if analysis else None),
            status=analysis.status if analysis else None, verdict=analysis.verdict if analysis else None,
            summary_ko=analysis.summary_ko if analysis else None,
            **({"evaluation": metadata} if metadata else {}),
        ))
    facets = {name: list(db.scalars(select(column).where(TestRunItem.test_run_id == run.id,
        column.is_not(None)).distinct().order_by(column))) for name, column in (
            ("difficulties", TestRunItem.difficulty), ("test_categories", TestRunItem.test_category))}
    return TestRunDetail(**summary.model_dump(), items=items, total_items=total_items,
                         limit=limit, offset=offset, facets=facets,
        missing_difficulty_count=db.scalar(select(func.count()).select_from(TestRunItem).where(
            TestRunItem.test_run_id == run.id, TestRunItem.difficulty.is_(None))) or 0,
        missing_test_category_count=db.scalar(select(func.count()).select_from(TestRunItem).where(
            TestRunItem.test_run_id == run.id, TestRunItem.test_category.is_(None))) or 0)


def analysis_test_run(db, analysis):
    return db.scalar(select(TestRun).join(TestRunItem, TestRunItem.test_run_id == TestRun.id).where(
        TestRunItem.analysis_id == analysis.id, TestRunItem.ingest_status == "accepted"))


def attach_test_run_ids(db, rows):
    if not rows:
        return
    mapping = dict(db.execute(select(TestRunItem.analysis_id, TestRunItem.test_run_id).where(
        TestRunItem.analysis_id.in_([row.id for row in rows]), TestRunItem.ingest_status == "accepted")).all())
    for row in rows:
        row._test_run_id = mapping.get(row.id)


def named_test_request_check(engine, run_id):
    def check():
        with Session(engine) as latest:
            run = latest.get(TestRun, run_id)
            profile = latest.get(VLLMProfile, run.profile_id) if run and run.profile_id else None
            if profile is None or profile.status == "disabled" or profile_fingerprint(profile) != run.profile_fingerprint:
                raise TargetNotAllowedError("test_run_profile_changed")
            # Ordinary saved tests survive role reassignment, not revocation
            # followed by an unverified re-enable. Candidate 150 validation is
            # intentionally allowed to evaluate a draft profile.
            if run.model_test_run_id is None and assignment_block_reason(latest, profile):
                raise TargetNotAllowedError("test_run_profile_not_verified")
            normalized = normalize_and_validate_profile_url(profile,
                allowed_targets_from_db(latest) if profile.provider == "vllm" else "")
            if normalized != profile.base_url:
                raise TargetNotAllowedError("test_run_profile_changed")
            validate_profile_provider_settings(profile, has_api_key=bool(profile.api_key_ciphertext))
    return check


def selected_test_request_check(engine, profile_id, selected_fingerprint):
    """Keep a legacy in-flight test on its selected, still-approved configuration."""
    def check():
        with Session(engine) as latest:
            profile = latest.get(VLLMProfile, profile_id)
            if profile is None or profile.status == "disabled" or profile_fingerprint(profile) != selected_fingerprint:
                raise TargetNotAllowedError("test_run_profile_changed")
            if assignment_block_reason(latest, profile):
                raise TargetNotAllowedError("test_run_profile_not_verified")
            normalized = normalize_and_validate_profile_url(profile,
                allowed_targets_from_db(latest) if profile.provider == "vllm" else "")
            if normalized != profile.base_url:
                raise TargetNotAllowedError("test_run_profile_changed")
            validate_profile_provider_settings(profile, has_api_key=bool(profile.api_key_ciphertext))
    return check
