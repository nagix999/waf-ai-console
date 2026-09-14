from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import VLLMProfile, VLLMTestRun
from app.services.vllm_profiles import profile_fingerprint


def test_migration_preserves_roles_and_verification_and_requires_safe_rollback(tmp_path, monkeypatch):
    url = f"sqlite+pysqlite:///{tmp_path / 'agent-migration.db'}"
    monkeypatch.setattr("app.config.get_settings", lambda: Settings(database_url=url))
    backend = Path(__file__).resolve().parents[1]
    config = Config(str(backend / "alembic.ini"))
    config.set_main_option("script_location", str(backend / "alembic"))
    command.upgrade(config, "0013_analysis_retries_keys")
    engine = create_engine(url)
    with Session(engine) as db:
        profile = VLLMProfile(name="fixture-before-upgrade", model_name="fixture-model",
                              base_url="http://10.0.0.10:8000/v1", status="production", is_test=True)
        db.add(profile); db.flush()
        fingerprint = profile_fingerprint(profile)
        db.add(VLLMTestRun(profile_id=profile.id, mode="full", status="passed", profile_fingerprint=fingerprint))
        db.commit(); identifier = profile.id
    command.upgrade(config, "head")
    command.upgrade(config, "head")
    with engine.begin() as connection:
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "0016_evidence_editor"
        assert "agent_configurations" in inspect(connection).get_table_names()
        assert connection.scalar(text("SELECT COUNT(*) FROM agent_configurations")) == 0
        assert connection.execute(text("SELECT id, status, is_test FROM vllm_profiles")).one() == (identifier, "production", 1)
        assert connection.scalar(text("SELECT profile_fingerprint FROM vllm_test_runs")) == fingerprint
        connection.execute(text("INSERT INTO agent_configurations (id, revision, production_verifier_profile_id) VALUES (1, 1, :id)"), {"id": identifier})
    with pytest.raises(RuntimeError, match="requires_pre_upgrade_backup"):
        command.downgrade(config, "0013_analysis_retries_keys")
    engine.dispose()
