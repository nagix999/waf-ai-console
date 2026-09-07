"""No production database or deployed credential is accessed by these tests."""
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
from app.models import ServiceApiKey, utcnow
from test_provider_migrations import seed_history, snapshot_history


def migration_module():
    path = Path(__file__).resolve().parents[1] / "alembic/versions/0008_service_api_keys.py"
    spec = importlib.util.spec_from_file_location("service_api_keys_migration", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def synthetic_row(**overrides):
    return {
        "id": "synthetic-key-id", "name": "Synthetic key", "key_prefix": "synthetic-public-prefix",
        "key_hash": "0" * 64, "source_system": "synthetic", "scopes_json": ["ingest"],
        "created_by": "synthetic-admin", **overrides,
    }


def test_upgrade_is_empty_additive_and_preserves_history(tmp_path, monkeypatch):
    monkeypatch.setenv("WAF_BOOTSTRAP_API_KEY", "synthetic-environment-key-never-imported")
    engine = build_engine(f"sqlite+pysqlite:///{tmp_path / 'service-keys-legacy.db'}")
    Base.metadata.create_all(engine)
    migration = migration_module()
    try:
        with engine.begin() as connection:
            with Operations.context(MigrationContext.configure(connection)):
                migration.downgrade()
            seed_history(connection)
            before = snapshot_history(connection)
            tables = set(inspect(connection).get_table_names())
            with Operations.context(MigrationContext.configure(connection)):
                migration.upgrade()
                migration.upgrade()
            assert snapshot_history(connection) == before
            assert set(inspect(connection).get_table_names()) - tables == {"service_api_keys"}
            assert connection.scalar(text("SELECT COUNT(*) FROM service_api_keys")) == 0
            columns = {column["name"] for column in inspect(connection).get_columns("service_api_keys")}
            assert "key_hash" in columns and not {"api_key", "key_ciphertext", "expires_at"} & columns
            assert connection.execute(text("PRAGMA foreign_key_check")).all() == []
            with Operations.context(MigrationContext.configure(connection)):
                migration.downgrade()
            assert snapshot_history(connection) == before
    finally:
        engine.dispose()


@pytest.mark.parametrize("revoked", [False, True])
def test_downgrade_refuses_any_issued_key_history(tmp_path, revoked):
    engine = build_engine(f"sqlite+pysqlite:///{tmp_path / 'service-keys-history.db'}")
    Base.metadata.create_all(engine)
    try:
        with engine.begin() as connection:
            connection.execute(ServiceApiKey.__table__.insert().values(**synthetic_row(revoked_at=utcnow() if revoked else None)))
            before = connection.execute(ServiceApiKey.__table__.select()).all()
            with Operations.context(MigrationContext.configure(connection)):
                with pytest.raises(RuntimeError, match="^cannot_downgrade_with_service_api_key_history$"):
                    migration_module().downgrade()
            assert connection.execute(ServiceApiKey.__table__.select()).all() == before
    finally:
        engine.dispose()


@pytest.mark.parametrize("duplicate", ["name", "key_hash"])
def test_migration_enforces_unique_name_and_hash(tmp_path, duplicate):
    engine = build_engine(f"sqlite+pysqlite:///{tmp_path / 'service-keys-unique.db'}")
    Base.metadata.create_all(engine)
    try:
        with engine.begin() as connection:
            with Operations.context(MigrationContext.configure(connection)):
                migration_module().downgrade()
                migration_module().upgrade()
            connection.execute(ServiceApiKey.__table__.insert().values(**synthetic_row()))
            row = synthetic_row(id="second-synthetic", name="Second synthetic", key_hash="1" * 64)
            row[duplicate] = synthetic_row()[duplicate]
            with pytest.raises(IntegrityError):
                connection.execute(ServiceApiKey.__table__.insert().values(**row))
    finally:
        engine.dispose()


def test_fresh_0008_has_no_bootstrap_import(tmp_path, monkeypatch):
    backend = Path(__file__).resolve().parents[1]
    url = f"sqlite+pysqlite:///{tmp_path / 'service-keys-fresh.db'}"
    monkeypatch.setattr("app.config.get_settings", lambda: Settings(database_url=url))
    config = Config(str(backend / "alembic.ini"))
    config.set_main_option("script_location", str(backend / "alembic"))
    command.upgrade(config, "0008_service_api_keys")
    engine = build_engine(url)
    try:
        with engine.connect() as connection:
            assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "0008_service_api_keys"
            assert connection.scalar(text("SELECT COUNT(*) FROM service_api_keys")) == 0
    finally:
        engine.dispose()
