import importlib.util
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from threading import Barrier

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import inspect, text

from app.database import Base, build_engine, build_session_factory
from app.models import Analysis, AnalysisLabel, AccessAudit, Review
from app.services.evaluation_labels import LabelAttachmentError, confirm_labels, preview_labels
from test_provider_migrations import seed_history, snapshot_history


def migration_module():
    path = Path(__file__).resolve().parents[1] / "alembic/versions/0005_analysis_labels.py"
    spec = importlib.util.spec_from_file_location("evaluation_label_migration", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_label_migration_adds_only_empty_table_and_preserves_all_existing_history(tmp_path):
    engine = build_engine(f"sqlite+pysqlite:///{tmp_path / 'label-migration.db'}")
    Base.metadata.create_all(engine)
    migration = migration_module()
    with engine.begin() as connection:
        seed_history(connection)
        connection.execute(Review.__table__.insert().values(
            analysis_id="legacy-analysis", source_system="synthetic-source", external_review_id="synthetic-review",
            event_id="legacy-event", decision="deferred", comment="Synthetic preserved review", ai_visible=False,
        ))
        reviews_before = connection.execute(Review.__table__.select()).mappings().all()
        with Operations.context(MigrationContext.configure(connection)):
            migration.downgrade()
        tables_before = set(inspect(connection).get_table_names())
        before = snapshot_history(connection)
        with Operations.context(MigrationContext.configure(connection)):
            migration.upgrade()
            migration.upgrade()
        assert set(inspect(connection).get_table_names()) - tables_before == {"analysis_labels"}
        assert snapshot_history(connection) == before
        assert connection.execute(Review.__table__.select()).mappings().all() == reviews_before
        assert connection.scalar(text("SELECT COUNT(*) FROM analysis_labels")) == 0
        assert connection.execute(text("PRAGMA foreign_key_check")).all() == []
        connection.execute(AnalysisLabel.__table__.insert().values(
            analysis_id="legacy-analysis", revision=1, verdict="true_positive", source_kind="reference", source_ref="synthetic",
            created_by="synthetic-admin", attachment_id="synthetic-attachment", token_digest="a" * 64,
        ))
        with Operations.context(MigrationContext.configure(connection)):
            with pytest.raises(RuntimeError, match="^cannot_downgrade_with_evaluation_label_history$"):
                migration.downgrade()
        assert connection.scalar(text("SELECT COUNT(*) FROM analysis_labels")) == 1
        assert snapshot_history(connection) == before
        assert connection.execute(Review.__table__.select()).mappings().all() == reviews_before
    engine.dispose()


@pytest.mark.parametrize("route", ["list", "detail"])
def test_read_routes_keep_one_wal_snapshot_during_label_and_worker_updates(tmp_path, monkeypatch, route):
    from app.api.analyses import get_analysis, list_analyses
    from app.security import Principal
    from app.services import analysis as analysis_service, analysis_query
    from app.services.analysis_query import AnalysisFilters

    engine = build_engine(f"sqlite+pysqlite:///{tmp_path / 'read-snapshot.db'}")
    Base.metadata.create_all(engine)
    sessions = build_session_factory(engine)
    with sessions() as db:
        row = Analysis(
            source_system="synthetic-source", event_id="synthetic-event", company_name="Synthetic",
            src_ip="192.0.2.1", dest_ip="198.51.100.1", waf_vendor="generic", waf_action="D",
            payload_ciphertext="synthetic-encrypted-placeholder", encryption_key_version="synthetic",
            status="completed", completed_at=datetime.now(UTC), verdict="true_positive",
            result_json={"verdict": "true_positive", "agent": {"framework": "moduagent"}},
        )
        db.add(row)
        db.flush()
        analysis_id = row.id
        db.add(AnalysisLabel(
            analysis_id=analysis_id, revision=1, verdict="true_positive", source_kind="reference",
            source_ref="synthetic-v1", created_by="synthetic-admin", attachment_id="synthetic-first", token_digest="a" * 64,
        ))
        db.commit()

    module = analysis_query if route == "list" else analysis_service
    original = module.attach_evaluations
    updates = []

    def update_between_row_and_label_reads(db, rows):
        if not updates:
            with sessions() as writer:
                row = writer.get(Analysis, analysis_id)
                row.verdict = "false_positive"
                row.result_json = {"verdict": "false_positive", "agent": {"framework": "moduagent"}}
                writer.add(AnalysisLabel(
                    analysis_id=analysis_id, revision=2, verdict="false_positive", source_kind="reference",
                    source_ref="synthetic-v2", created_by="synthetic-admin", attachment_id="synthetic-next", token_digest="b" * 64,
                ))
                writer.commit()
            updates.append(True)
        original(db, rows)

    monkeypatch.setattr(module, "attach_evaluations", update_between_row_and_label_reads)
    principal = Principal(kind="service_api_key", scopes=frozenset({"ingest"}), source_system="synthetic-source")
    with sessions() as reader:
        if route == "list":
            response = list_analyses(reader, principal, AnalysisFilters(reference_label="true_positive"))
            assert response.total == response.evaluation_summary.total == 1
            assert response.evaluation_summary.matches == 1
            observed = response.items[0]
        else:
            observed = get_analysis(analysis_id, reader, principal)
        assert observed.verdict == "true_positive"
        assert observed.evaluation.outcome == "match"
        assert observed.evaluation.reference_label.revision == 1
        assert observed.evaluation.reference_label.source_ref == "synthetic-v1"
    assert updates == [True]
    with sessions() as reader:
        latest = get_analysis(analysis_id, reader, principal)
        assert latest.verdict == "false_positive"
        assert latest.evaluation.outcome == "match"
        assert latest.evaluation.reference_label.revision == 2
    engine.dispose()


@pytest.mark.parametrize("same_token", [False, True])
def test_simultaneous_confirmations_are_serialized_without_lost_revision(tmp_path, same_token):
    engine = build_engine(f"sqlite+pysqlite:///{tmp_path / 'label-concurrency.db'}")
    Base.metadata.create_all(engine)
    sessions = build_session_factory(engine)
    with sessions() as db:
        row = Analysis(
            source_system="synthetic-source", event_id="synthetic-event", company_name="Synthetic",
            src_ip="192.0.2.1", dest_ip="198.51.100.1", waf_vendor="generic", waf_action="D",
            payload_ciphertext="synthetic-encrypted-placeholder", encryption_key_version="synthetic",
        )
        db.add(row)
        db.commit()
    tokens = []
    for verdict in ("true_positive", "false_positive"):
        with sessions() as db:
            result = preview_labels(
                db, answers=[{"event_id": "synthetic-event", "expected_verdict": verdict}],
                source_system="synthetic-source", source_kind="reference", source_ref="synthetic-v1",
                ai_visible=None, actor="synthetic-admin", secret="synthetic-signing-key",
            )
            tokens.append(result.preview_token)
    if same_token:
        tokens[1] = tokens[0]
    barrier = Barrier(2)

    def run(token):
        with sessions() as db:
            barrier.wait(timeout=10)
            try:
                result = confirm_labels(db, token=token, actor="synthetic-admin", secret="synthetic-signing-key")
                return (200, result.duplicate, token)
            except LabelAttachmentError as exc:
                return (exc.status_code, exc.code, token)

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(run, tokens))
    assert sorted(result[0] for result in results) == ([200, 200] if same_token else [200, 409])
    if same_token:
        assert sorted(result[1] for result in results) == [False, True]
    with sessions() as db:
        assert db.query(AnalysisLabel).count() == 1
        assert db.query(AccessAudit).count() == 1
        assert db.query(AnalysisLabel).one().revision == 1
        winner = next(result[2] for result in results if result[0] == 200)
        assert confirm_labels(db, token=winner, actor="synthetic-admin", secret="synthetic-signing-key").duplicate is True
    engine.dispose()
