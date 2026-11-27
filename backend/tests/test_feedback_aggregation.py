"""Synthetic-only administrator feedback aggregates and admission provenance."""
from datetime import UTC, datetime, timedelta
import importlib.util
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from alembic import command
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import MetaData, event, inspect, select, text
from sqlalchemy.exc import IntegrityError
from fastapi.testclient import TestClient

from app.config import Settings
from app.database import Base, build_engine
from app.models import AccessAudit, Analysis, ProductionPromotion, Review
from app.schemas import AnalysisInput
from app.services.analysis import enqueue_analysis

BASE = "/api/v1/admin/feedback"
DAY = {"date_from": "2026-11-25", "date_to": "2026-11-25"}
START = datetime(2026, 11, 24, 15, tzinfo=UTC)


def login(client):
    response = client.post("/api/v1/auth/login", json={"username": "admin", "password": "test-password"})
    assert response.status_code == 200


@pytest.fixture
def feedback_rows(client):
    with client.app.state.session_factory() as db:
        db.add_all([
            ProductionPromotion(id="production-old", kind="promotion", actor_id="synthetic",
                snapshot_json={"prompt": {"fixed_rules_version": "waf-system-v2.12"}},
                configuration_hash="a" * 64, created_at=START - timedelta(days=5)),
            ProductionPromotion(id="production-new", kind="promotion", actor_id="synthetic",
                snapshot_json={"prompt": {"fixed_rules_version": "waf-system-v2.32"}},
                configuration_hash="b" * 64, created_at=START - timedelta(days=1)),
        ])
        db.flush()
        for identifier, purpose, version in (
            ("old", "production", "production-old"), ("new", "production", "production-new"),
            ("unknown", "production", None), ("test", "test", None), ("legacy", "legacy_unknown", None),
        ):
            db.add(Analysis(id=identifier, source_system="synthetic", event_id=f"event-{identifier}",
                company_name="Literal_% Company" if identifier == "old" else "Other Synthetic Company",
                src_ip="192.0.2.1", dest_ip="198.51.100.1", waf_vendor="synthetic", waf_action="D",
                payload_ciphertext="SYNTHETIC_PAYLOAD_MUST_NOT_READ", encryption_key_version="test",
                result_json={"SYNTHETIC_RESULT_MUST_NOT_READ": True}, analysis_purpose=purpose,
                production_configuration_id=version, signature=f"signature-{identifier}"))
        db.flush()
        specifications = [
            ("r1", "old", ["insufficient_evidence", "exaggerated_impact"], "synthetic comment"),
            ("r2", "old", ["insufficient_evidence", "insufficient_evidence"], None),
            ("r3", "new", ["other"], "synthetic comment search excluded"),
            ("r4", "new", [], "comment-only"),
            ("r5", "unknown", [], None),
            ("r6", "unknown", [], "another comment-only"),
            ("r7", "test", ["evidence_mismatch"], None),
            ("r8", "legacy", ["explanation_error"], None),
        ]
        for index, (identifier, analysis_id, categories, comment) in enumerate(specifications):
            db.add(Review(id=identifier, analysis_id=analysis_id, source_system="synthetic",
                external_review_id=f"external-{identifier}", event_id=f"event-{analysis_id}",
                feedback_categories=categories, comment=comment, analyst_id="analyst-%",
                created_at=START + timedelta(hours=index)))
        for identifier, created_at in (("before", START - timedelta(microseconds=1)),
                                       ("after", START + timedelta(days=1))):
            db.add(Review(id=identifier, analysis_id="old", source_system="synthetic", event_id="event-old",
                external_review_id=f"external-{identifier}", feedback_categories=["explanation_error"], created_at=created_at))
        db.commit()
    login(client)


def test_full_scope_registration_denominator_deduplicates_each_review(client, feedback_rows):
    body = client.get(BASE + "/summary", params=DAY).json()
    assert (body["registration_count"], body["analysis_count"], body["multi_category_count"],
            body["comment_only_count"]) == (6, 3, 1, 2)
    counts = {item["category"]: item for item in body["category_counts"]}
    assert len(counts) == 6
    assert counts["insufficient_evidence"]["count"] == 2
    assert counts["insufficient_evidence"]["percent"] == pytest.approx(100 / 3)
    assert counts["exaggerated_impact"]["count"] == counts["other"]["count"] == 1
    assert counts["inappropriate_severity"]["count"] == 0
    assert body["search_scope"] == "metadata_only"
    page = client.get(BASE, params={**DAY, "page_size": 2, "page": 2}).json()
    assert page["total"] == 6 and [item["id"] for item in page["items"]] == ["r4", "r3"]
    assert page["page"] == 2 and page["page_size"] == 2
    assert page["items"][0]["comment"] == "comment-only"
    assert page["items"][0]["created_at"].endswith("Z")
    assert "SYNTHETIC_PAYLOAD_MUST_NOT_READ" not in str(page)
    assert "SYNTHETIC_RESULT_MUST_NOT_READ" not in str(page)


@pytest.mark.parametrize("query,total", [
    ({"analysis_purpose": "all"}, 8), ({"analysis_purpose": "test"}, 1),
    ({"production_configuration_id": "__unrecorded__", "analysis_purpose": "all"}, 2),
    ({"production_configuration_id": "production-old"}, 2),
    ({"production_configuration_id": "production-old", "analysis_purpose": "test"}, 0),
    ({"category": "insufficient_evidence"}, 2), ({"analysis_id": "old"}, 2),
    ({"q": "_%"}, 2), ({"q": "comment-only"}, 0), ({"q": "signature-new"}, 2),
    ({"q": "  signature-new\u200b "}, 2),
])
def test_list_and_summary_share_every_filter(client, feedback_rows, query, total):
    params = {**DAY, **query}
    listing = client.get(BASE, params={**params, "page_size": 1}).json()
    summary = client.get(BASE + "/summary", params=params).json()
    assert listing["total"] == summary["registration_count"] == total


def test_versions_keep_original_promotion_and_never_assign_tests_to_unknown(client, feedback_rows):
    response = client.get(BASE + "/versions", params={**DAY, "analysis_purpose": "all"})
    assert response.status_code == 200, response.text
    body = response.json()
    versions = {item["id"]: item for item in body["items"]}
    assert set(versions) == {"production-old", "production-new", "__unrecorded__"}
    assert versions["production-old"]["label"] == "v2.12 / 2026-11-20 00:00:00 KST"
    assert versions["production-new"]["label"] == "v2.32 / 2026-11-24 00:00:00 KST"
    assert [(row["count"], row["analysis_count"]) for row in versions.values()] == [(2, 1)] * 3
    assert body["unrecorded_count"] == 2 and body["unrecorded_analysis_count"] == 1
    assert client.get(BASE + "/versions", params={**DAY, "analysis_purpose": "test"}).json()["items"] == []
    # Version facets keep the other versions available while a version is selected.
    selected = client.get(BASE + "/versions", params={**DAY, "production_configuration_id": "production-new"}).json()
    assert len(selected["items"]) == 3
    detail = client.get(BASE + "/r1").json()
    assert detail["production_configuration_id"] == "production-old"
    assert detail["production_configuration_label"] == "v2.12 / 2026-11-20 00:00:00 KST"
    test = client.get(BASE + "/r7").json()
    assert test["production_configuration_id"] is test["production_configuration_label"] is None


def test_aggregation_never_loads_event_or_model_content_or_decrypts(client, feedback_rows, monkeypatch):
    def forbidden(_value):
        raise AssertionError("aggregation must not decrypt")
    monkeypatch.setattr(client.app.state.crypto, "decrypt_text", forbidden)
    statements = []
    engine = client.app.state.session_factory.kw["bind"]
    def observe(_connection, _cursor, statement, _parameters, _context, _many):
        statements.append(statement.lower())
    event.listen(engine, "before_cursor_execute", observe)
    try:
        assert client.get(BASE + "/summary", params=DAY).status_code == 200
        assert client.get(BASE + "/versions", params=DAY).status_code == 200
        assert client.get(BASE, params={**DAY, "page_size": 1}).status_code == 200
    finally:
        event.remove(engine, "before_cursor_execute", observe)
    for statement in statements:
        assert "payload_ciphertext" not in statement and "result_json" not in statement
        assert "execution_snapshot_ciphertext" not in statement and "agent_steps" not in statement


def test_summary_is_not_capped_by_list_page_and_categories_can_overlap(client, feedback_rows):
    categories = ["insufficient_evidence", "evidence_mismatch", "explanation_error",
                  "exaggerated_impact", "inappropriate_severity", "other"]
    with client.app.state.session_factory() as db:
        db.add_all(Review(id=f"bulk-{index:03}", analysis_id="old", source_system="synthetic",
            external_review_id=f"bulk-{index:03}", event_id="event-old", feedback_categories=categories,
            created_at=START + timedelta(minutes=1)) for index in range(250))
        db.commit()
    params = {**DAY, "analysis_id": "old"}
    listing = client.get(BASE, params={**params, "page_size": 1}).json()
    summary = client.get(BASE + "/summary", params=params).json()
    assert len(listing["items"]) == 1 and listing["total"] == 252
    assert summary["registration_count"] == 252 and summary["analysis_count"] == 1
    assert summary["multi_category_count"] == 251
    assert sum(item["percent"] for item in summary["category_counts"]) > 100
    assert all(item["count"] >= 250 for item in summary["category_counts"])


@pytest.mark.parametrize("query", [
    {"date_from": "2026-11-26", "date_to": "2026-11-25"}, {"date_to": "9999-12-31"}, {"date_from": "0001-01-01"},
    {"date_from": "unknown"}, {"analysis_purpose": "legacy_unknown"}, {"category": "rating"},
    {"page": 0}, {"page_size": 101}, {"q": "x" * 201}, {"payload": "synthetic"},
])
def test_invalid_filters_rejected(client, feedback_rows, query):
    assert client.get(BASE, params=query).status_code == 422


def test_feedback_requires_admin_and_records_read_access(client, service_headers, feedback_rows):
    with client.app.state.session_factory() as db:
        assert db.scalar(select(AccessAudit.id).where(AccessAudit.action == "view_feedback_detail")) is None
    assert client.get(BASE + "/r1").status_code == 200
    with client.app.state.session_factory() as db:
        assert db.scalar(select(AccessAudit.id).where(AccessAudit.action == "view_feedback_detail"))
    client.post("/api/v1/auth/logout")
    assert client.get(BASE).status_code == 401
    assert client.get(BASE + "/summary", headers=service_headers).status_code == 403
    assert client.get(BASE + "/versions", headers=service_headers).status_code == 403
    assert client.get(BASE + "/r1", headers=service_headers).status_code == 403


def test_empty_summary_and_missing_detail(client):
    login(client)
    summary = client.get(BASE + "/summary").json()
    assert summary["registration_count"] == summary["analysis_count"] == 0
    assert len(summary["category_counts"]) == 6
    assert all(item["count"] == item["percent"] == 0 for item in summary["category_counts"])
    assert client.get(BASE + "/missing").status_code == 404


def test_parallel_list_summary_and_versions_do_not_upgrade_stale_wal_reads(tmp_path, settings, monkeypatch):
    from app.main import create_app
    from app.services import feedback_queries
    app = create_app(settings.model_copy(update={
        "database_url": f"sqlite+pysqlite:///{tmp_path / 'synthetic-parallel.db'}"}), create_schema=True)
    with TestClient(app, headers={"Origin": "http://testserver"}) as client:
        login(client)
        with app.state.session_factory() as db:
            db.add(Analysis(id="parallel", source_system="synthetic", event_id="parallel",
                company_name="Synthetic", src_ip="192.0.2.1", dest_ip="198.51.100.1", waf_vendor="synthetic",
                waf_action="D", payload_ciphertext="synthetic-encrypted", encryption_key_version="test",
                analysis_purpose="production"))
            db.flush()
            db.add(Review(analysis_id="parallel", source_system="synthetic", external_review_id="parallel",
                event_id="parallel", feedback_categories=["other"]))
            db.commit()
        barrier = Barrier(3)
        def synchronized(original):
            def read(*args, **kwargs):
                result = original(*args, **kwargs)
                # Force all three requests to finish reading before any audit
                # write. Reusing a read snapshot would deterministically fail.
                barrier.wait(timeout=15)
                return result
            return read
        for name in ("feedback_list", "feedback_summary", "feedback_versions"):
            monkeypatch.setattr(feedback_queries, name, synchronized(getattr(feedback_queries, name)))
        with ThreadPoolExecutor(max_workers=3) as pool:
            responses = list(pool.map(client.get, (BASE, BASE + "/summary", BASE + "/versions")))
        assert [response.status_code for response in responses] == [200, 200, 200]
        assert responses[0].json()["total"] == responses[1].json()["registration_count"] == 1
        assert responses[2].json()["unrecorded_count"] == 1
        with app.state.session_factory() as db:
            assert len(db.scalars(select(AccessAudit).where(AccessAudit.action.in_(
                ("view_feedback_list", "view_feedback_summary", "view_feedback_versions")))).all()) == 3


def test_admission_pins_only_new_production_without_changing_old_rows(client, event_payload):
    crypto = client.app.state.crypto
    with client.app.state.session_factory() as db:
        # The real baseline has a valid prompt snapshot, no model call.
        from app.services.production_configurations import ensure_baseline
        ensure_baseline(db, crypto, client.app.state.settings)
        baseline = db.scalar(select(ProductionPromotion))
        db.commit()
        prod, _ = enqueue_analysis(db, crypto, "synthetic", AnalysisInput.model_validate(event_payload))
        assert prod.production_configuration_id == baseline.id
        test, _ = enqueue_analysis(db, crypto, "synthetic", AnalysisInput.model_validate({**event_payload, "event_id": "test"}),
                                   analysis_purpose="test")
        assert test.production_configuration_id is None
        prod.production_configuration_id = None
        db.commit()
        duplicate, reused = enqueue_analysis(db, crypto, "synthetic", AnalysisInput.model_validate(event_payload))
        assert reused and duplicate.production_configuration_id is None
    with pytest.raises(ValueError, match="server_control_fields_not_allowed"):
        AnalysisInput.model_validate({**event_payload, "production_configuration_id": "forged"})
    with pytest.raises(ValueError, match="server_control_fields_not_allowed"):
        AnalysisInput.model_validate({**event_payload, "extra_fields": {"production_configuration_id": "forged"}})


@pytest.mark.usefixtures("legacy_v220_admissions")
def test_retry_keeps_original_production_configuration_after_new_promotion(client, registered_vllm_target, event_payload):
    from test_analysis_retries_keys import failed_fixture, profile_fixture, retry
    login(client)
    client.app.state.settings.agent_mode = "moduagent"
    profile_id = profile_fixture(client)
    identifier = failed_fixture(client, event_payload, profile_id)
    with client.app.state.session_factory() as db:
        db.add_all([ProductionPromotion(id=version, kind="promotion", actor_id="synthetic",
            snapshot_json={}, configuration_hash=character * 64, created_at=START + timedelta(days=index))
            for index, (version, character) in enumerate((("old-version", "a"), ("latest-version", "b")))])
        db.flush()
        db.get(Analysis, identifier).production_configuration_id = "old-version"
        db.commit()
    response = retry(client, identifier)
    assert response.status_code == 202, response.text
    with client.app.state.session_factory() as db:
        assert db.get(Analysis, response.json()["analysis_id"]).production_configuration_id == "old-version"
        assert db.get(Analysis, identifier).production_configuration_id == "old-version"


def migration():
    path = Path(__file__).resolve().parents[1] / "alembic/versions/0033_feedback_provenance.py"
    spec = importlib.util.spec_from_file_location("feedback_migration", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_migration_preserves_history_and_restricts_configuration_deletion(tmp_path):
    engine = build_engine(f"sqlite+pysqlite:///{tmp_path / 'synthetic-history.db'}")
    with engine.begin() as connection:
        connection.exec_driver_sql("CREATE TABLE production_promotions (id TEXT PRIMARY KEY)")
        connection.exec_driver_sql("CREATE TABLE analyses (id TEXT PRIMARY KEY, ciphertext TEXT NOT NULL)")
        connection.exec_driver_sql("CREATE TABLE reviews (id TEXT PRIMARY KEY, analysis_id TEXT REFERENCES analyses(id) ON DELETE CASCADE)")
        connection.exec_driver_sql("INSERT INTO analyses VALUES ('legacy','synthetic-history')")
        connection.exec_driver_sql("INSERT INTO reviews VALUES ('legacy-review','legacy')")
        with Operations.context(MigrationContext.configure(connection)):
            migration().upgrade()
            migration().upgrade()
        assert connection.execute(text("SELECT id,ciphertext,production_configuration_id FROM analyses")).all() == [
            ("legacy", "synthetic-history", None)]
        assert connection.scalar(text("SELECT count(*) FROM reviews")) == 1
        with Operations.context(MigrationContext.configure(connection)):
            migration().downgrade()
            assert "production_configuration_id" not in {column["name"] for column in inspect(connection).get_columns("analyses")}
            assert connection.scalar(text("SELECT count(*) FROM reviews")) == 1
            migration().upgrade()
        connection.exec_driver_sql("INSERT INTO production_promotions VALUES ('synthetic-version')")
        connection.exec_driver_sql("UPDATE analyses SET production_configuration_id='synthetic-version'")
        with pytest.raises(IntegrityError):
            connection.exec_driver_sql("DELETE FROM production_promotions WHERE id='synthetic-version'")
        with Operations.context(MigrationContext.configure(connection)):
            with pytest.raises(RuntimeError, match="production_provenance_requires_current_schema"):
                migration().downgrade()
        assert connection.execute(text("PRAGMA foreign_key_check")).all() == []
    engine.dispose()


def test_fresh_populated_downgrade_requires_backup_without_touching_history(tmp_path):
    engine = build_engine(f"sqlite+pysqlite:///{tmp_path / 'synthetic-fresh-history.db'}")
    with engine.begin() as connection:
        connection.exec_driver_sql("CREATE TABLE production_promotions (id TEXT PRIMARY KEY)")
        connection.exec_driver_sql("CREATE TABLE analyses (id TEXT PRIMARY KEY, ciphertext TEXT NOT NULL, "
            "production_configuration_id TEXT, FOREIGN KEY (production_configuration_id) REFERENCES production_promotions(id))")
        connection.exec_driver_sql("CREATE TABLE reviews (id TEXT PRIMARY KEY, analysis_id TEXT REFERENCES analyses(id) ON DELETE CASCADE)")
        connection.exec_driver_sql("INSERT INTO analyses VALUES ('legacy','synthetic-history',NULL)")
        connection.exec_driver_sql("INSERT INTO reviews VALUES ('legacy-review','legacy')")
        with Operations.context(MigrationContext.configure(connection)):
            migration().upgrade()
            with pytest.raises(RuntimeError, match="production_provenance_downgrade_requires_pre_upgrade_backup"):
                migration().downgrade()
        assert connection.execute(text("SELECT * FROM analyses")).all() == [("legacy", "synthetic-history", None)]
        assert connection.execute(text("SELECT * FROM reviews")).all() == [("legacy-review", "legacy")]
        assert "ix_analyses_production_configuration_id" in {item["name"] for item in inspect(connection).get_indexes("analyses")}
        assert connection.execute(text("PRAGMA foreign_key_check")).all() == []
    engine.dispose()


@pytest.mark.parametrize("upgrade_existing", [False, True])
def test_real_fresh_and_previous_alembic_upgrade(tmp_path, monkeypatch, upgrade_existing):
    import app.config
    settings = Settings(database_url=f"sqlite+pysqlite:///{tmp_path / 'synthetic.db'}", admin_username="admin",
        admin_password="test-password", session_secret="synthetic-session-secret",
        data_encryption_key="AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=")
    monkeypatch.setattr(app.config, "get_settings", lambda: settings)
    root = Path(__file__).resolve().parents[1]
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "alembic"))
    before_analysis = before_review = None
    if upgrade_existing:
        # 0001 creates CURRENT metadata, so upgrading to 0032 alone would
        # already contain today's new column. Clone the full real prior shape
        # without that FK/index/column, then stamp its actual prior revision.
        legacy = MetaData()
        for table in Base.metadata.tables.values():
            table.to_metadata(legacy)
        analyses = legacy.tables["analyses"]
        projection = analyses.c.production_configuration_id
        for constraint in list(analyses.foreign_key_constraints):
            if "production_configuration_id" in constraint.columns:
                analyses.constraints.remove(constraint)
                for foreign_key in constraint.elements:
                    analyses.foreign_keys.discard(foreign_key)
                    projection.foreign_keys.discard(foreign_key)
        for index in list(analyses.indexes):
            if "production_configuration_id" in index.columns:
                analyses.indexes.remove(index)
        analyses._columns.remove(projection)
        engine = build_engine(settings.database_url)
        legacy.create_all(engine)
        with engine.begin() as connection:
            assert "production_configuration_id" not in {column["name"] for column in inspect(connection).get_columns("analyses")}
            connection.execute(analyses.insert().values(id="synthetic-existing", source_system="synthetic",
                event_id="synthetic-existing", company_name="Synthetic Company", src_ip="192.0.2.1",
                dest_ip="198.51.100.1", waf_vendor="synthetic", waf_action="D", analysis_purpose="production",
                payload_ciphertext="synthetic-encrypted-history", encryption_key_version="synthetic-version",
                result_json={"legacy": "synthetic-preserved-result"}))
            connection.execute(legacy.tables["reviews"].insert().values(id="synthetic-review",
                analysis_id="synthetic-existing", source_system="synthetic", external_review_id="synthetic-review",
                event_id="synthetic-existing", decision="deferred", comment="synthetic-preserved-opinion"))
            before_analysis = dict(connection.execute(select(analyses)).mappings().one())
            before_review = dict(connection.execute(select(legacy.tables["reviews"])).mappings().one())
        engine.dispose()
        command.stamp(config, "0032_signature_explanations")
    command.upgrade(config, "head")
    command.upgrade(config, "head")
    engine = build_engine(settings.database_url)
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "0034_analysis_concurrency_512"
        assert "production_configuration_id" in {column["name"] for column in inspect(connection).get_columns("analyses")}
        assert connection.execute(text("PRAGMA foreign_key_check")).all() == []
        if upgrade_existing:
            after = dict(connection.execute(select(Analysis.__table__)).mappings().one())
            assert after.pop("production_configuration_id") is None
            assert after == before_analysis
            assert dict(connection.execute(select(Review.__table__)).mappings().one()) == before_review
            assert any(foreign_key["constrained_columns"] == ["production_configuration_id"]
                and foreign_key["referred_table"] == "production_promotions"
                for foreign_key in inspect(connection).get_foreign_keys("analyses"))
    engine.dispose()
