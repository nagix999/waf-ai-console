from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text

from app.config import Settings


def test_pre_editor_upgrade_preserves_assignments_and_starts_disabled(tmp_path, monkeypatch):
    url = f"sqlite+pysqlite:///{tmp_path / 'editor-migration.db'}"
    monkeypatch.setattr("app.config.get_settings", lambda: Settings(database_url=url))
    backend = Path(__file__).resolve().parents[1]
    config = Config(str(backend / "alembic.ini"))
    config.set_main_option("script_location", str(backend / "alembic"))
    command.upgrade(config, "0015_concurrency")
    engine = create_engine(url)
    with engine.begin() as conn:
        # Reproduce a real pre-0016 schema in this empty temporary database:
        # 0001 uses current metadata and otherwise already includes new columns.
        conn.execute(text("DROP TABLE agent_configurations"))
        conn.execute(text("CREATE TABLE agent_configurations (id INTEGER PRIMARY KEY CHECK(id=1), revision INTEGER NOT NULL, production_verifier_profile_id VARCHAR(36) REFERENCES vllm_profiles(id), test_verifier_profile_id VARCHAR(36) REFERENCES vllm_profiles(id))"))
        conn.execute(text("INSERT INTO agent_configurations(id, revision) VALUES (1, 7)"))
        conn.execute(text("ALTER TABLE test_runs DROP COLUMN evidence_editor_snapshot_ciphertext"))
    command.upgrade(config, "head")
    command.upgrade(config, "head")
    with engine.begin() as conn:
        assert conn.scalar(text("SELECT revision FROM agent_configurations")) == 7
        assert conn.execute(text("SELECT production_evidence_editor_enabled, test_evidence_editor_enabled, production_evidence_editor_profile_id, test_evidence_editor_profile_id FROM agent_configurations")).one() == (0, 0, None, None)
        assert "evidence_editor_snapshot_ciphertext" in {c["name"] for c in inspect(conn).get_columns("test_runs")}
        for purpose in ("production", "test"):
            assert any(fk["constrained_columns"] == [purpose + "_evidence_editor_profile_id"] for fk in inspect(conn).get_foreign_keys("agent_configurations"))
        conn.execute(text("UPDATE agent_configurations SET production_evidence_editor_enabled=1"))
    with pytest.raises(RuntimeError, match="evidence_editor_downgrade_requires_pre_upgrade_backup"):
        command.downgrade(config, "0015_concurrency")
    engine.dispose()
