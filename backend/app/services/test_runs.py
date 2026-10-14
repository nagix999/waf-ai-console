"""Named administrator tests: immutable membership, bounded metadata, no LLM I/O."""
import hashlib
import json
import uuid
from datetime import UTC, datetime

from pydantic import ValidationError
from sqlalchemy import false, func, literal, select, true
from sqlalchemy.orm import Session, load_only

from ..models import AccessAudit, Analysis, AnalysisLabel, TestEvaluation, TestRun, TestRunItem, VLLMProfile, ValidationDatasetItem
from ..schemas import AnalysisInput
from ..test_run_schemas import TestRunCreate, TestRunDetail, TestRunItemResponse, TestRunSummary
from .analysis import AnalysisIngestError
from .evaluation import evaluation_relation, latest_labels, metadata_from_row, summarize_evaluations
from .internal_egress import allowed_targets_from_db
from .initial_assessment import split_admission
from .prompt_snapshots import load_analysis_prompt, pin_analysis_prompt
from .input_schemas import InputSchemaError, get_schema_version, pin_schema
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
                      kind, actor, filename=None, dataset_hash=None, model_test=None, candidate_configuration=None):
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
    candidate = candidate_configuration
    if candidate is not None:
        if mode != "moduagent" or model_test is not None:
            raise AnalysisIngestError("candidate_requires_llm_test", 409)
        from .candidate_configurations import resolve_profiles
        profile, candidate_verifier, candidate_editor = resolve_profiles(db, crypto, candidate)
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
        from .agent_configuration import profile_metadata, select_verifier
        # Candidate qualification deliberately tests the candidate in both roles.
        verifier = candidate_verifier if candidate is not None else profile if model_test else select_verifier(db, "test", profile)
        metadata["verifier_profile"] = profile_metadata(verifier)
    else:
        profile = None
        metadata.update(model_profile="stub", llm_called=False)
    temporary = Analysis()
    pin_analysis_prompt(db, crypto, temporary, version_id=candidate.prompt_policy_version_id if candidate else None)
    from .evidence_editor import capture_editor
    editor = candidate_editor if candidate is not None else capture_editor(db, "test", profile) if mode == "moduagent" and not model_test else None
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
        evidence_editor_snapshot_ciphertext=crypto.encrypt_text(editor.model_dump_json()) if editor else None,
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
            pin_schema(db, crypto, run, version=get_schema_version(db, candidate.input_schema_version_id) if candidate else None,
                       selection_origin="test_run")
    except InputSchemaError as exc:
        raise AnalysisIngestError(exc.code, exc.status_code) from None
    from .candidate_configurations import pin_configuration
    pin_configuration(run, crypto)
    db.add(run)
    db.flush()
    return run, False


def pin_item_prompt(analysis, run):
    analysis.prompt_snapshot_ciphertext = run.prompt_snapshot_ciphertext
    analysis.prompt_policy_version_id = run.prompt_policy_version_id
    analysis.prompt_version = run.prompt_version


def add_run_items(db, crypto, settings, run, rows, *, ai_visible=None, source_ref="test-upload:expected_verdict", trusted_items=None, source_kind="synthetic_expected", actor_kind="admin_session", allow_initial_assessment=False):
    write_lock(db)
    if db.scalar(select(TestRun.stopped_at).where(TestRun.id == run.id)) is not None:
        raise AnalysisIngestError("test_run_stopped", 409)
    if not rows or len(rows) > MAX_TEST_ITEMS:
        raise AnalysisIngestError("test_item_count_out_of_range", 422)
    try:
        snapshot = pin_schema(db, crypto, run, selection_origin="legacy_default")
    except InputSchemaError as exc:
        raise AnalysisIngestError(exc.code, exc.status_code) from None
    prompt = load_analysis_prompt(run, crypto)
    start = db.scalar(select(func.max(TestRunItem.row_number)).where(TestRunItem.test_run_id == run.id)) or 0
    if start + len(rows) > MAX_TEST_ITEMS:
        raise AnalysisIngestError("test_item_count_out_of_range", 422)
    for position, raw in enumerate(rows):
        number = start + position + 1
        trusted = trusted_items[position] if trusted_items else {}
        item = TestRunItem(test_run_id=run.id, row_number=number, ingest_status="rejected")
        item.dataset_item_version_id = trusted.get("dataset_item_version_id")
        try:
            event, expected, metadata = split_test_metadata(raw)
            event, initial = split_admission(event, allowed=allow_initial_assessment)
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
                if trusted.get("source_system"):
                    first = None
                if first is not None and expected is not None:
                    label = db.get(AnalysisLabel, first.label_id) if first.label_id else None
                    if label is None or label.verdict != expected:
                        raise AnalysisIngestError("expected_verdict_conflict", 409)
                analysis, duplicate, _ = enqueue_test_upload_row(
                    db, crypto, trusted.get("source_system", run.source_system), payload, expected_verdict=expected,
                    actor=run.created_by, commit=False, payload_max_bytes=settings.payload_max_bytes,
                    attachment_id=run.model_test_run_id or run.id, ai_visible=trusted.get("ai_visible", ai_visible), label_source_ref=source_ref,
                    source_kind=trusted.get("source_kind", source_kind), comment=trusted.get("comment", ""), actor_kind=actor_kind,
                    ingest_channel="model_validation" if run.model_test_run_id else "test_lab" if run.kind == "direct" else "file_upload",
                    schema_snapshot=snapshot,
                    prompt_snapshot=prompt,
                    initial_assessment=initial,
                )
                if not duplicate:
                    pin_item_prompt(analysis, run)
                    analysis.internal_only = bool(trusted.get("internal_only", False))
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
                      filename=None, content_hash=None, candidate_configuration=None):
    try:
        try:
            document = {"name": name, "kind": kind, "rows": rows, "filename": filename}
            # Preserve old replay hashes when the optional candidate is absent.
            if candidate_configuration is not None:
                document["candidate_configuration"] = candidate_configuration.model_dump(mode="json")
            serialized = json.dumps(document,
                                    sort_keys=True, ensure_ascii=False, allow_nan=False)
            digest = hashlib.sha256(serialized.encode()).hexdigest()
        except (ValueError, TypeError, RecursionError, UnicodeError):
            raise AnalysisIngestError("invalid_test_document", 422) from None
        write_lock(db)
        run, duplicate = create_run_record(db, crypto, settings, name=name, idempotency_key=idempotency_key,
            request_hash=digest, kind=kind, actor=actor, filename=filename, dataset_hash=content_hash,
            candidate_configuration=candidate_configuration)
        if not duplicate:
            add_run_items(db, crypto, settings, run, rows, allow_initial_assessment=kind == "direct")
            db.add(AccessAudit(actor_kind="admin_session", actor_id=actor, action="create_test_run",
                resource_type="test_run", resource_id=run.id))
        db.commit()
        return run, duplicate
    except Exception:
        db.rollback()
        raise


def _test_read_labels(labels):
    # A flattened labels -> item -> current join can become a nested scan for
    # each analysis. Keep just comparison/provenance fields and let SQLite
    # build its temporary lookup once for this statement.
    names = ("analysis_id", "id", "revision", "verdict", "source_kind", "source_ref", "ai_visible", "created_at")
    return select(*(labels.c[name] for name in names)).cte("test_read_labels").prefix_with("MATERIALIZED", dialect="sqlite")


def _test_read_relation(labels, current):
    # LEFT JOIN may scan a fixed label set for every analysis when SQLite
    # underestimates its size. Its exact equivalent is labelled inner rows
    # UNION ALL unlabelled rows. This preserves duplicates, NULL semantics,
    # cancellation and the shared evaluator's original policy unchanged.
    identifiers = select(current.c.analysis_id)
    labelled = evaluation_relation(labels, labelled_only=True)
    empty = select(*(literal(None, type_=column.type).label(column.name) for column in labels.c)).where(false()).subquery()
    unlabelled = evaluation_relation(empty)
    labelled_ids = select(labels.c.analysis_id).where(labels.c.analysis_id.is_not(None))
    return select(labelled).where(labelled.c.analysis_id.in_(identifiers)).union_all(
        select(unlabelled).where(unlabelled.c.analysis_id.in_(identifiers),
                                unlabelled.c.analysis_id.not_in(labelled_ids))).cte(
        "test_read_evaluation").prefix_with("MATERIALIZED", dialect="sqlite")


def fixed_reference_relation(run_id, evaluation=None, *, include_retries=True, attempts=None):
    from .test_attempts import test_attempts
    nodes, current = attempts if attempts is not None else test_attempts(run_id,
        before=evaluation.created_at if evaluation and evaluation.analysis_ids_json is None else None, include_retries=include_retries,
        analysis_ids=evaluation.analysis_ids_json if evaluation else None)
    if evaluation is not None:
        labels = select(*(column for column in AnalysisLabel.__table__.columns if column.name != "analysis_id"),
            current.c.analysis_id.label("analysis_id")).join(nodes, nodes.c.analysis_id == AnalysisLabel.analysis_id).join(
                current, current.c.item_id == nodes.c.item_id).join(TestRunItem, TestRunItem.id == nodes.c.item_id).where(
                AnalysisLabel.id.in_(evaluation.label_ids), TestRunItem.ingest_status == "accepted").subquery()
    else:
        labels = select(*(column for column in AnalysisLabel.__table__.columns if column.name != "analysis_id"),
            current.c.analysis_id.label("analysis_id")).join(TestRunItem, TestRunItem.label_id == AnalysisLabel.id).join(
                current, current.c.item_id == TestRunItem.id).where(
                TestRunItem.test_run_id == run_id, TestRunItem.ingest_status == "accepted").subquery()
    return _test_read_relation(_test_read_labels(labels), current) if attempts is not None else evaluation_relation(labels)


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


def test_read_context(db, run, *, evaluation_id=None, reference_basis="initial", cohort=None):
    """Share the selected attempts and reference snapshot across read views."""
    evaluation = db.get(TestEvaluation, evaluation_id) if evaluation_id else None
    if evaluation_id and (evaluation is None or evaluation.test_run_id != run.id):
        raise AnalysisIngestError("test_evaluation_not_found", 404)
    from .test_attempts import EVALUATION_RETRY_AUDIT_ACTION, test_attempts
    # Before this feature, saved evaluations deliberately ignored all retries,
    # including retries already completed at save time. Preserve that meaning.
    include_retries = evaluation is None or evaluation.evaluation_kind == "ground_truth" or bool(db.scalar(select(AccessAudit.id).where(
        AccessAudit.action == EVALUATION_RETRY_AUDIT_ACTION,
        AccessAudit.resource_type == "test_evaluation", AccessAudit.resource_id == evaluation.id).limit(1)))
    _nodes, current = test_attempts(run.id, before=evaluation.created_at if evaluation and evaluation.analysis_ids_json is None else None, include_retries=include_retries,
        analysis_ids=evaluation.analysis_ids_json if evaluation else None)
    # A flattened derived table can rerun retry/window and comparison work for
    # every returned row after an outcome predicate. Materialize only scoped
    # IDs/small metadata, never raw input or complete result JSON.
    selected = select(current)
    if cohort is not None:
        selected = selected.join(TestRunItem, TestRunItem.id == current.c.item_id).where(*cohort)
    current = selected.cte("test_read_current").prefix_with("MATERIALIZED", dialect="sqlite")
    official = run.evaluation_mode == "ground_truth"
    relation = _test_read_relation(_test_read_labels(latest_labels(select(current.c.analysis_id))), current) if reference_basis == "latest" and evaluation is None and not official else fixed_reference_relation(
        run.id, evaluation, include_retries=include_retries, attempts=(_nodes, current))
    return evaluation, _nodes, current, relation


def describe_run(db, run, *, limit=50, offset=0, difficulty=None, test_category=None,
                 status=None, evaluation_outcome=None, reference_verdict=None, verdict=None, detail=True,
                 difficulty_missing=False, test_category_missing=False, evaluation_id=None, reference_basis="initial",
                 sort_by="row_number", sort_order="asc", group_field=None, group_value=None, group_missing=False, group_filters=None, case_filter=None):
    from .test_attempts import test_attempts
    read_snapshot(db)
    cohort = run_cohort(run.id, difficulty, test_category, difficulty_missing, test_category_missing)
    evaluation, _nodes, current, relation = test_read_context(db, run, evaluation_id=evaluation_id, reference_basis=reference_basis, cohort=cohort)
    accepted_ids = select(current.c.analysis_id).join(TestRunItem, TestRunItem.id == current.c.item_id).where(
        *cohort, TestRunItem.ingest_status == "accepted")
    official = run.evaluation_mode == "ground_truth"
    evaluation_summary = summarize_evaluations(db, relation, [Analysis.id.in_(accepted_ids)])
    counts = dict(db.execute(select(TestRunItem.ingest_status, func.count()).where(
        *cohort).group_by(TestRunItem.ingest_status)).all())
    processing_rows = db.execute(select(Analysis.status, func.count(), func.max(Analysis.completed_at)).where(
        Analysis.id.in_(accepted_ids)).group_by(Analysis.status)).all()
    processing = {state: count for state, count, _ in processing_rows}
    finished = max((completed for _, _, completed in processing_rows if completed is not None), default=None)
    state = "processing" if processing.get("processing") else "pending" if processing.get("pending") else (
        "failed" if processing.get("failed") or counts.get("rejected") else "completed")
    if run.stopped_at is not None and evaluation is None:
        state, finished = "stopped", run.stopped_at
    history_ids = select(_nodes.c.analysis_id).join(TestRunItem, TestRunItem.id == _nodes.c.item_id).where(
        *cohort, TestRunItem.ingest_status == "accepted")
    started = db.scalar(select(func.min(Analysis.started_at)).where(Analysis.id.in_(history_ids)))
    if state in {"pending", "processing"}:
        finished = None
    end = finished or (run.created_at if not processing else datetime.now(UTC))
    utc = lambda value: value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
    elapsed = max(0, int((utc(end) - utc(run.created_at)).total_seconds() * 1000))
    from .official_evaluations import ground_truth_metadata
    ground_truth = ground_truth_metadata(db, run)
    # Actions concern the whole live run, never a filtered or saved evaluation.
    _, live_attempts = test_attempts(run.id)
    can_stop = run.stopped_at is None and run.model_test_run_id is None and (run.accepting_items or bool(db.scalar(
        select(Analysis.id).join(live_attempts, live_attempts.c.analysis_id == Analysis.id)
        .join(TestRunItem, TestRunItem.id == live_attempts.c.item_id).where(
            TestRunItem.ingest_status == "accepted", Analysis.analysis_purpose == "test",
            Analysis.status.in_(["pending", "processing"])).limit(1))))
    if ground_truth and ground_truth.get("published"):
        from .validation_datasets import digest as scope_digest
        scope = {"difficulty": difficulty, "test_category": test_category,
            "difficulty_missing": difficulty_missing, "test_category_missing": test_category_missing}
        scope_hash = scope_digest({"scope": "published_membership", "filters": {k: v for k, v in scope.items() if v}})
        for key in ("dataset_id", "dataset_revision_id", "metrics_version"):
            setattr(evaluation_summary, key, ground_truth[key])
        evaluation_summary.sample_count = sum(counts.values())
        evaluation_summary.evaluation_scope_hash = scope_hash
        evaluation_summary.comparison_key = scope_digest([ground_truth["dataset_id"], run.dataset_version_id, scope_hash, run.metrics_version])
    summary = TestRunSummary(
        id=run.id, name=run.name, kind=run.kind, source_system=run.source_system, created_at=run.created_at,
        status=state, total=sum(counts.values()), accepted=counts.get("accepted", 0),
        duplicates=counts.get("duplicate", 0), rejected=counts.get("rejected", 0),
        pending=processing.get("pending", 0), processing=processing.get("processing", 0),
        completed=processing.get("completed", 0), failed=processing.get("failed", 0),
        canceled=processing.get("canceled", 0), stopped_at=run.stopped_at, stopped_by=run.stopped_by, can_stop=can_stop,
        execution_mode=run.execution_mode, profile_metadata=run.profile_metadata,
        configuration_snapshot=run.configuration_snapshot_json, configuration_hash=run.configuration_hash,
        evaluation_mode=run.evaluation_mode, ground_truth=ground_truth, test_purpose=run.test_purpose,
        official_evaluation_pending=run.official_evaluation_pending if not evaluation else False,
        prompt_version=run.prompt_version, model_test_run_id=run.model_test_run_id,
        prompt_policy_version_id=run.prompt_policy_version_id,
        evaluation_summary=evaluation_summary,
        evaluation_id=evaluation.id if evaluation else None,
        evaluation_revision=evaluation.revision if evaluation else 0,
        reference_basis="saved" if evaluation else "ground_truth" if official else reference_basis,
        dataset_version_id=run.dataset_version_id, accepting_items=run.accepting_items,
        started_at=started, completed_at=finished, total_elapsed_ms=elapsed,
    )
    if evaluation and evaluation.evaluation_kind == "ground_truth" and evaluation.summary_json and not any(
            (difficulty, test_category, difficulty_missing, test_category_missing)):
        # Unfiltered historical scores/timing are a stored observation, not a
        # recomputation under future metric definitions or mutable labels.
        summary = TestRunSummary.model_validate({**evaluation.summary_json,
            "stopped_at": run.stopped_at, "stopped_by": run.stopped_by, "can_stop": can_stop,
            "evaluation_id": evaluation.id, "evaluation_revision": evaluation.revision,
            "reference_basis": "saved", "official_evaluation_pending": False})
    if not detail:
        return summary
    query = select(TestRunItem, Analysis, current.c.retry_count, relation,
        ValidationDatasetItem.item_id.label("stable_case_id")).join(current, current.c.item_id == TestRunItem.id).outerjoin(Analysis, Analysis.id == current.c.analysis_id).outerjoin(
        relation, relation.c.analysis_id == Analysis.id).outerjoin(ValidationDatasetItem,
        ValidationDatasetItem.id == TestRunItem.dataset_item_version_id).where(*cohort)
    from .test_run_statistics import group_conditions
    condition = group_conditions(group_filters, field=group_field, value=group_value, missing=group_missing)
    if condition is not None:
        query = query.where(condition, TestRunItem.ingest_status == "accepted")
    from .test_case_filters import case_condition
    condition = case_condition(case_filter, relation)
    if condition is not None:
        query = query.where(condition, TestRunItem.ingest_status == "accepted")
    if status is not None:
        query = query.where(Analysis.status == status)
    if evaluation_outcome is not None:
        query = query.where(relation.c.outcome == evaluation_outcome)
    if reference_verdict is not None:
        query = query.where(relation.c.reference_verdict == reference_verdict)
    if verdict is not None:
        query = query.where(Analysis.verdict == verdict)
    if evaluation_outcome is not None and reference_verdict is not None and verdict is not None:
        # A matrix cell counts accepted cases, not duplicate admissions that
        # happen to point at the same Analysis and would repeat its verdict.
        query = query.where(TestRunItem.ingest_status == "accepted")
    items = []
    column = {"row_number": TestRunItem.row_number, "case_name": TestRunItem.case_name,
              "status": Analysis.status}.get(sort_by, TestRunItem.row_number)
    # Count and page share a single scoped comparison evaluation. Keep only
    # IDs/small reference metadata here; hydrate ORM rows AFTER LIMIT/OFFSET.
    metadata_names = [name for name in relation.c.keys() if name != "analysis_id"]
    matches = query.with_only_columns(TestRunItem.id.label("item_id"), current.c.analysis_id,
        current.c.retry_count, *(relation.c[name] for name in metadata_names),
        ValidationDatasetItem.item_id.label("stable_case_id"), column.label("sort_value"),
        TestRunItem.row_number.label("row_order"), maintain_column_froms=True).cte("test_read_matches").prefix_with("MATERIALIZED", dialect="sqlite")
    order = matches.c.sort_value.desc().nulls_last() if sort_order == "desc" else matches.c.sort_value.asc().nulls_last()
    page = select(matches).order_by(order, matches.c.row_order).limit(limit).offset(offset).cte("test_read_page")
    count = select(func.count().label("filtered_total")).select_from(matches).subquery()
    query = select(TestRunItem, Analysis, page.c.retry_count, *(page.c[name] for name in metadata_names),
        page.c.stable_case_id, count.c.filtered_total).select_from(count).outerjoin(page, true()).outerjoin(
        TestRunItem, TestRunItem.id == page.c.item_id).outerjoin(Analysis, Analysis.id == page.c.analysis_id).options(
        load_only(Analysis.id, Analysis.error_code, Analysis.status, Analysis.verdict, Analysis.summary_ko, raiseload=True))
    page_order = page.c.sort_value.desc().nulls_last() if sort_order == "desc" else page.c.sort_value.asc().nulls_last()
    total_items = 0
    for row in db.execute(query.order_by(page_order, page.c.row_order)):
        item, analysis = row[0], row[1]
        total_items = row._mapping["filtered_total"]
        if item is None:  # A zero/empty page still returns its exact count.
            continue
        metadata = metadata_from_row(row._mapping) if analysis else None
        items.append(TestRunItemResponse(
            ground_truth_source={"dataset_id": ground_truth["dataset_id"],
                "dataset_revision_id": run.dataset_version_id, "item_version_id": item.dataset_item_version_id,
                "stable_case_id": row._mapping["stable_case_id"]}
                if ground_truth and item.dataset_item_version_id else None,
            **{key: getattr(item, key) for key in ("id", "row_number", "event_id", "difficulty",
                "test_category", "case_name", "ingest_status")},
            analysis_id=analysis.id if analysis else None, original_analysis_id=item.analysis_id,
            retry_count=row._mapping["retry_count"],
            error_code=item.error_code or (analysis.error_code if analysis else None),
            status=analysis.status if analysis else None, verdict=analysis.verdict if analysis else None,
            summary_ko=analysis.summary_ko if analysis else None,
            **({"evaluation": metadata} if metadata else {}),
        ))
    facet_rows = db.execute(select(TestRunItem.difficulty, TestRunItem.test_category, func.count()).where(
        TestRunItem.test_run_id == run.id).group_by(TestRunItem.difficulty, TestRunItem.test_category)).all()
    facets = {"difficulties": sorted({d for d, _, _ in facet_rows if d is not None}),
        "test_categories": sorted({c for _, c, _ in facet_rows if c is not None})}
    return TestRunDetail(**summary.model_dump(), items=items, total_items=total_items,
                         limit=limit, offset=offset, facets=facets,
        missing_difficulty_count=sum(n for d, _, n in facet_rows if d is None),
        missing_test_category_count=sum(n for _, c, n in facet_rows if c is None))


def analysis_test_run(db, analysis):
    from .test_attempts import test_attempts
    nodes, _current = test_attempts()
    return db.scalar(select(TestRun).join(TestRunItem, TestRunItem.test_run_id == TestRun.id).join(
        nodes, nodes.c.item_id == TestRunItem.id).where(
        nodes.c.analysis_id == analysis.id, TestRunItem.ingest_status == "accepted"))


def attach_test_run_ids(db, rows):
    if not rows:
        return
    from .test_attempts import test_attempts
    nodes, _current = test_attempts()
    mapping = dict(db.execute(select(nodes.c.analysis_id, TestRunItem.test_run_id).join(
        TestRunItem, TestRunItem.id == nodes.c.item_id).where(
        nodes.c.analysis_id.in_([row.id for row in rows]), TestRunItem.ingest_status == "accepted")).all())
    for row in rows:
        row._test_run_id = mapping.get(row.id)


def named_test_request_check(engine, run_id):
    def check():
        with Session(engine) as latest:
            run = latest.get(TestRun, run_id)
            if run and run.stopped_at is not None:
                raise TargetNotAllowedError("test_run_stopped")
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
            verifier_metadata = run.profile_metadata.get("verifier_profile")
            if verifier_metadata:
                from .agent_configuration import validate_role_profile
                validate_role_profile(latest, latest.get(VLLMProfile, verifier_metadata["model_profile_id"]),
                                      verifier_metadata["profile_fingerprint"], require_verified=run.model_test_run_id is None)
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
