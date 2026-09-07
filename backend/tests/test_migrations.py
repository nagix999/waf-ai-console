import importlib.util
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, inspect, text

from app.config import Settings
from app.database import Base
from app.models import Analysis


def migration_module():
    path = Path(__file__).resolve().parents[1] / "alembic/versions/0003_analysis_search.py"
    spec = importlib.util.spec_from_file_location("analysis_search_migration", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_fresh_alembic_upgrade_handles_initial_current_metadata(tmp_path, monkeypatch):
    backend = Path(__file__).resolve().parents[1]
    url = f"sqlite+pysqlite:///{tmp_path / 'fresh.db'}"
    monkeypatch.setattr("app.config.get_settings", lambda: Settings(database_url=url))
    config = Config(str(backend / "alembic.ini"))
    config.set_main_option("script_location", str(backend / "alembic"))
    command.upgrade(config, "head")
    engine = create_engine(url)
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "0012_input_schemas"
        assert "analysis_purpose" in {column["name"] for column in inspect(connection).get_columns("analyses")}
    engine.dispose()


def test_upgrade_existing_schema_preserves_data_and_backfills_only_actual_severity(tmp_path):
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'legacy.db'}")
    Base.metadata.create_all(engine)
    migration = migration_module()
    legacy_result = {"schema_version": "waf-analysis-v1", "uncertainties": ["synthetic"], "threat_analysis": {"category": "legacy"}}
    modern_result = {"schema_version": "waf-analysis-v2", "threat_analysis": {"severity": "HIGH", "category": "sql_injection"}}
    with engine.begin() as connection:
        for row_id, result in (("legacy", legacy_result), ("modern", modern_result)):
            connection.execute(Analysis.__table__.insert().values(
                id=row_id, source_system="synthetic-source", event_id=row_id, company_name="Synthetic",
                src_ip="192.0.2.1", dest_ip="198.51.100.1", waf_vendor="generic", waf_action="D",
                payload_ciphertext="synthetic-encrypted-placeholder", encryption_key_version="test-key",
                result_json=result,
            ))
        with Operations.context(MigrationContext.configure(connection)):
            migration.downgrade()
        raw_before = connection.execute(text("SELECT id, payload_ciphertext, result_json FROM analyses ORDER BY id")).all()
        with Operations.context(MigrationContext.configure(connection)):
            migration.upgrade()
        raw_after = connection.execute(text("SELECT id, payload_ciphertext, result_json FROM analyses ORDER BY id")).all()
        assert raw_before == raw_after
        rows = connection.execute(text("SELECT id, analysis_purpose, ingest_channel, severity, event_fingerprint FROM analyses ORDER BY id")).all()
        assert rows == [
            ("legacy", "legacy_unknown", "legacy_unknown", None, None),
            ("modern", "legacy_unknown", "legacy_unknown", "HIGH", None),
        ]
        indexes = {index["name"] for index in inspect(connection).get_indexes("analyses")}
        assert {"ix_analyses_purpose_created", "ix_analyses_severity_created", "ix_analyses_category_created"} <= indexes
    engine.dispose()
