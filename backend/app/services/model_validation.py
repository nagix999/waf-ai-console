"""Fixed synthetic dataset preparation and read-only, initial-reference scoring."""
import hashlib
import json
from importlib.resources import files

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import Analysis, AnalysisLabel, VLLMTestRun
from ..schemas import AnalysisInput, ModelDatasetEvaluation
from .crypto import CryptoService
from .evaluation import evaluation_relation, summarize_evaluations
from .upload_expected_labels import enqueue_test_upload_row
from .input_schemas import pin_schema


DATASET_VERSION = "waf-dummy-v1"
DATASET_SIZE = 150


class DatasetError(ValueError):
    def __init__(self, code="model_validation_dataset_unavailable"):
        self.code = code
        super().__init__(code)


def dataset_source(run_id: str) -> str:
    return f"waf-internal-model-test-{run_id}"


def load_dataset() -> tuple[list[tuple[AnalysisInput, str]], str]:
    try:
        content = files("app").joinpath("data/waf_dummy_v1.json").read_bytes()
        rows = json.loads(content)
        if not isinstance(rows, list) or len(rows) != DATASET_SIZE:
            raise ValueError
        events = []
        for value in rows:
            row = dict(value)
            expected = row.pop("expected_verdict")
            if expected not in {"true_positive", "false_positive", "inconclusive"}:
                raise ValueError
            events.append((AnalysisInput.model_validate(row), expected))
        if len({event.event_id for event, _ in events}) != DATASET_SIZE:
            raise ValueError
        return events, hashlib.sha256(content).hexdigest()
    except (OSError, ValueError, TypeError, KeyError):
        raise DatasetError() from None


def enqueue_dataset(db: Session, crypto: CryptoService, test_run: VLLMTestRun, actor: str, settings=None) -> None:
    """Caller owns the transaction: run, all cases, prompts and answers are atomic."""
    events, digest = load_dataset()
    test_run.dataset_version = DATASET_VERSION
    test_run.dataset_hash = digest
    snapshot = pin_schema(db, crypto, test_run, selection_origin="model_validation")
    if settings is not None:
        from .test_runs import add_run_items, create_run_record
        try:
            metadata = json.loads(files("app").joinpath("data/waf_dummy_v1_metadata.json").read_bytes())
            if not isinstance(metadata, dict) or set(metadata) != {event.event_id for event, _ in events}:
                raise ValueError
            if any(not isinstance(value, dict) or set(value) - {"difficulty", "test_category", "case_name"}
                   for value in metadata.values()):
                raise ValueError
            rows = [{**event.model_dump(mode="json", exclude_unset=True), "expected_verdict": expected, **metadata[event.event_id]}
                    for event, expected in events]
        except (OSError, ValueError, TypeError):
            raise DatasetError() from None
        run, duplicate = create_run_record(db, crypto, settings, name=test_run.name,
            idempotency_key=f"model-test:{test_run.id}", request_hash=test_run.profile_fingerprint,
            kind="model_validation", actor=actor, dataset_hash=digest, model_test=test_run)
        if duplicate:
            raise DatasetError("model_validation_dataset_already_exists")
        add_run_items(db, crypto, settings, run, rows, ai_visible=False, source_ref=DATASET_VERSION)
        db.flush()
        return
    for payload, expected in events:
        analysis, duplicate, _state = enqueue_test_upload_row(
            db, crypto, dataset_source(test_run.id), payload,
            expected_verdict=expected, actor=actor, commit=False,
            label_source_ref=DATASET_VERSION, attachment_id=test_run.id,
            ai_visible=False, ingest_channel="model_validation",
            schema_snapshot=snapshot,
        )
        if duplicate:
            raise DatasetError("model_validation_dataset_already_exists")
        analysis.model_test_run_id = test_run.id
    db.flush()


def dataset_evaluation(db: Session, test_run: VLLMTestRun) -> ModelDatasetEvaluation:
    counts = dict(db.execute(select(Analysis.status, func.count()).where(
        Analysis.model_test_run_id == test_run.id,
    ).group_by(Analysis.status)).all())
    # The benchmark is scored against the references fixed at submission, not
    # a later analyst correction. Normal analysis views retain latest-label semantics.
    references = select(AnalysisLabel).where(AnalysisLabel.attachment_id == test_run.id).subquery()
    relation = evaluation_relation(references)
    summary = summarize_evaluations(db, relation, [Analysis.model_test_run_id == test_run.id])
    stage = (test_run.metrics_json or {}).get("dataset_status")
    status = (stage if stage in {"skipped", "failed", "completed"} else
              "failed" if test_run.status == "failed" else
              "running" if test_run.status == "running" else "waiting")
    return ModelDatasetEvaluation(
        dataset_version=test_run.dataset_version or DATASET_VERSION,
        dataset_hash=test_run.dataset_hash or "",
        source_system=dataset_source(test_run.id), total=sum(counts.values()),
        pending=counts.get("pending", 0), processing=counts.get("processing", 0),
        completed=counts.get("completed", 0), failed=counts.get("failed", 0),
        status=status, summary=summary,
    )
