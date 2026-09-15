import importlib.util
from pathlib import Path
import pytest
from alembic import command
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import Session
from app.config import Settings
from app.models import Analysis, AnalysisLabel, ServiceApiKey


def migration():
    path = Path(__file__).resolve().parents[1] / "alembic/versions/0017_validation_data.py"
    spec = importlib.util.spec_from_file_location("validation_migration", path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def test_actual_pre_validation_schema_preserves_data_and_allows_reference_hold(tmp_path, monkeypatch):
    url = f"sqlite+pysqlite:///{tmp_path / 'validation-migration.db'}"
    monkeypatch.setattr("app.config.get_settings", lambda: Settings(database_url=url))
    root = Path(__file__).resolve().parents[1]
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "alembic"))
    command.upgrade(config, "0016_evidence_editor")
    engine = create_engine(url)
    with Session(engine) as db:
        key = ServiceApiKey(name="fixture-key", key_prefix="fixture", key_hash="a" * 64,
            source_system="fixture", scopes_json=["ingest"], created_by="fixture")
        db.add(key); db.flush()
        row = Analysis(source_system="fixture", event_id="fixture-id", company_name="fixture", src_ip="192.0.2.1",
            dest_ip="198.51.100.1", waf_vendor="fixture", waf_action="D", payload_ciphertext="fixture-encrypted-preserved",
            encryption_key_version="v1", service_api_key_id=key.id)
        db.add(row); db.flush()
        db.add(AnalysisLabel(analysis_id=row.id, revision=1, verdict="false_positive", source_kind="reference",
            source_ref="fixture", created_by="fixture", attachment_id="fixture", token_digest="b" * 64))
        db.commit()
    # 0001 uses current metadata. Rehearse on the real preceding shape, not just
    # a fresh database that already contains the fields being tested.
    with engine.begin() as connection:
        with Operations.context(MigrationContext.configure(connection)):
            migration().downgrade()
        assert "internal_only" not in {c["name"] for c in inspect(connection).get_columns("analyses")}
        assert "validation_datasets" not in inspect(connection).get_table_names()
    command.upgrade(config, "head")
    command.upgrade(config, "head")
    with engine.begin() as connection:
        assert connection.scalar(text("SELECT purpose FROM service_api_keys")) == "production"
        assert connection.scalar(text("SELECT payload_ciphertext FROM analyses")) == "fixture-encrypted-preserved"
        assert connection.scalar(text("SELECT internal_only FROM analyses")) == 0
        assert connection.scalar(text("SELECT count(*) FROM analysis_labels")) == 1
        assert not any(c["name"] == "ck_analysis_label_abstention" for c in inspect(connection).get_check_constraints("analysis_labels"))
        assert connection.execute(text("PRAGMA foreign_key_check")).all() == []
        connection.execute(text("UPDATE analysis_labels SET verdict='inconclusive'"))
    with pytest.raises(RuntimeError, match="requires_pre_upgrade_backup"):
        command.downgrade(config, "0016_evidence_editor")
    engine.dispose()
