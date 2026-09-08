import importlib.util
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError

from app.config import Settings
from app.database import Base, build_engine
from app.models import InternalEgressTarget
from test_provider_migrations import seed_history, snapshot_history


def migration_module():
    path = Path(__file__).resolve().parents[1] / "alembic/versions/0007_internal_egress.py"
    spec = importlib.util.spec_from_file_location("internal_egress_migration", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_egress_migration_preserves_all_existing_history_and_creates_empty_table(tmp_path, monkeypatch):
    monkeypatch.setenv("WAF_VLLM_ALLOWED_TARGETS", "10.0.0.10:8000")
    engine = build_engine(f"sqlite+pysqlite:///{tmp_path / 'egress-legacy.db'}")
    Base.metadata.create_all(engine)
    migration = migration_module()
    try:
        with engine.begin() as connection:
            with Operations.context(MigrationContext.configure(connection)):
                migration.downgrade()
            seed_history(connection)
            before = snapshot_history(connection)
            tables_before = set(inspect(connection).get_table_names())
            with Operations.context(MigrationContext.configure(connection)):
                migration.upgrade()
                migration.upgrade()
            assert snapshot_history(connection) == before
            assert set(inspect(connection).get_table_names()) - tables_before == {"internal_egress_targets"}
            assert connection.scalar(text("SELECT COUNT(*) FROM internal_egress_targets")) == 0
            assert connection.execute(text("PRAGMA foreign_key_check")).all() == []
            with Operations.context(MigrationContext.configure(connection)):
                migration.downgrade()
            assert snapshot_history(connection) == before
    finally:
        engine.dispose()


def test_egress_downgrade_refuses_registered_configuration(tmp_path):
    engine = build_engine(f"sqlite+pysqlite:///{tmp_path / 'egress-saved.db'}")
    Base.metadata.create_all(engine)
    try:
        with engine.begin() as connection:
            connection.execute(InternalEgressTarget.__table__.insert().values(ip_address="10.0.0.10", port=8000))
            before = connection.execute(InternalEgressTarget.__table__.select()).all()
            with Operations.context(MigrationContext.configure(connection)):
                with pytest.raises(RuntimeError, match="^cannot_downgrade_with_internal_egress_configuration$"):
                    migration_module().downgrade()
            assert connection.execute(InternalEgressTarget.__table__.select()).all() == before
    finally:
        engine.dispose()


@pytest.mark.parametrize("values", [{"port": 0}, {"port": 65536}, {"revision": 0}])
def test_egress_migration_enforces_database_constraints(tmp_path, values):
    engine = build_engine(f"sqlite+pysqlite:///{tmp_path / 'egress-constraints.db'}")
    Base.metadata.create_all(engine)
    try:
        with engine.begin() as connection:
            with Operations.context(MigrationContext.configure(connection)):
                migration_module().downgrade()
                migration_module().upgrade()
            with pytest.raises(IntegrityError):
                connection.execute(InternalEgressTarget.__table__.insert().values(ip_address="10.0.0.10", **{"port": 8000, **values}))
    finally:
        engine.dispose()


def test_fresh_head_contains_empty_egress_table(tmp_path, monkeypatch):
    backend = Path(__file__).resolve().parents[1]
    url = f"sqlite+pysqlite:///{tmp_path / 'egress-fresh.db'}"
    monkeypatch.setattr("app.config.get_settings", lambda: Settings(database_url=url))
    config = Config(str(backend / "alembic.ini"))
    config.set_main_option("script_location", str(backend / "alembic"))
    command.upgrade(config, "head")
    engine = build_engine(url)
    try:
        with engine.connect() as connection:
            assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "0013_analysis_retries_keys"
            assert connection.scalar(text("SELECT COUNT(*) FROM internal_egress_targets")) == 0
    finally:
        engine.dispose()
