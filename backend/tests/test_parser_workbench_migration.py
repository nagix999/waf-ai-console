"""Fresh and populated SQLite upgrades preserve prior execution records."""
import importlib.util
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
import pytest
from sqlalchemy import create_engine, inspect, text

from app.config import Settings


def config_for(tmp_path, monkeypatch):
    backend = Path(__file__).resolve().parents[1]
    url = f"sqlite+pysqlite:///{tmp_path / 'workbench.db'}"
    monkeypatch.setattr("app.config.get_settings", lambda: Settings(database_url=url))
    config = Config(str(backend / "alembic.ini"))
    config.set_main_option("script_location", str(backend / "alembic"))
    return config, create_engine(url)


def test_fresh_workbench_migration(tmp_path, monkeypatch):
    config, engine = config_for(tmp_path, monkeypatch)
    command.upgrade(config, "head")
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "0033_feedback_provenance"
        assert {"parser_workbenches", "parser_workbench_jobs"} <= set(inspect(connection).get_table_names())
        indexes = {entry["name"] for entry in inspect(connection).get_indexes("parser_workbench_jobs")}
        assert "ix_parser_workbench_jobs_workbench_created" in indexes
    engine.dispose()


def test_existing_upgrade_preserves_history_and_blocks_destructive_downgrade(tmp_path, monkeypatch):
    config, engine = config_for(tmp_path, monkeypatch)
    command.upgrade(config, "0030_evidence_display_jobs")
    with engine.begin() as connection:
        # Initial metadata may already contain additive current-model tables.
        connection.exec_driver_sql("DROP TABLE IF EXISTS parser_workbench_jobs")
        connection.exec_driver_sql("DROP TABLE IF EXISTS parser_workbenches")
        connection.exec_driver_sql("INSERT INTO parser_versions(id,name,engine_version,options_json,content_hash,created_by,created_at) VALUES ('legacy','Legacy','generic-http-v3','{}','hash','admin',CURRENT_TIMESTAMP)")
        before = connection.execute(text("SELECT * FROM parser_versions")).all()
    command.upgrade(config, "head")
    with engine.begin() as connection:
        assert connection.execute(text("SELECT * FROM parser_versions")).all() == before
        connection.exec_driver_sql("INSERT INTO parser_workbenches(id,name,status,revision,source_kind,baseline_version_id,input_ciphertext,state_ciphertext,encryption_key_version,metadata_json,created_by,created_at,updated_at) VALUES ('work','Review','draft',1,'analyses','legacy','encrypted-input','encrypted-state','test','{}','admin',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)")
        path = Path(__file__).resolve().parents[1] / "alembic/versions/0031_parser_rule_workbench.py"
        spec = importlib.util.spec_from_file_location("parser_workbench_migration", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with Operations.context(MigrationContext.configure(connection)):
            with pytest.raises(RuntimeError, match="parser_workbench_history_requires_current_schema"):
                module.downgrade()
        assert connection.scalar(text("SELECT COUNT(*) FROM parser_workbenches")) == 1
    engine.dispose()
