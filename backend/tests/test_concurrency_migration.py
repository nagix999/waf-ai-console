from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text

from app.config import Settings


def test_migration_preserves_existing_assignments_and_protects_configured_rollback(tmp_path, monkeypatch):
    url = f"sqlite+pysqlite:///{tmp_path / 'concurrency-migration.db'}"
    monkeypatch.setattr("app.config.get_settings", lambda: Settings(database_url=url))
    backend = Path(__file__).resolve().parents[1]
    config = Config(str(backend / "alembic.ini"))
    config.set_main_option("script_location", str(backend / "alembic"))
    command.upgrade(config, "0014_agent_configuration")
    engine = create_engine(url)
    with engine.begin() as connection:
        connection.execute(text("INSERT INTO agent_configurations(id, revision) VALUES (1, 7)"))
        # Reproduce an actual pre-0015 installation, not 0001's current metadata.
        connection.execute(text("DROP TABLE llm_call_slots"))
        connection.execute(text("DROP TABLE concurrency_configuration"))
    command.upgrade(config, "head")
    command.upgrade(config, "head")
    with engine.begin() as connection:
        assert connection.scalar(text("SELECT revision FROM agent_configurations")) == 7
        assert connection.scalar(text("SELECT COUNT(*) FROM concurrency_configuration")) == 0
        assert "llm_call_slots" in inspect(connection).get_table_names()
    command.downgrade(config, "0014_agent_configuration")
    command.upgrade(config, "head")
    with engine.begin() as connection:
        connection.execute(text("INSERT INTO concurrency_configuration(id, revision, production, test, server_limits) VALUES (1, 1, 10, 2, '{}')"))
    with pytest.raises(RuntimeError, match="concurrency_downgrade_requires_pre_upgrade_backup"):
        command.downgrade(config, "0014_agent_configuration")
    with engine.begin() as connection:
        assert connection.scalar(text("SELECT production FROM concurrency_configuration")) == 10
        assert connection.scalar(text("SELECT revision FROM agent_configurations")) == 7
    engine.dispose()
