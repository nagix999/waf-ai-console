import importlib.util
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import inspect, text

from app.database import Base, build_engine, build_session_factory
from app.models import PromptPolicyVersion
from app.services.crypto import CryptoService
from app.services.prompt_policies import get_active_policy
from test_provider_migrations import seed_history, snapshot_history


def migration_module():
    path = Path(__file__).resolve().parents[1] / "alembic/versions/0006_prompt_policies.py"
    spec = importlib.util.spec_from_file_location("prompt_policy_migration", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_prompt_migration_is_additive_and_preserves_legacy_history(tmp_path):
    engine = build_engine(f"sqlite+pysqlite:///{tmp_path / 'prompt-legacy.db'}")
    Base.metadata.create_all(engine)
    migration = migration_module()
    with engine.begin() as connection:
        with Operations.context(MigrationContext.configure(connection)):
            migration.downgrade()
        seed_history(connection)
        before = snapshot_history(connection)
        tables_before = set(inspect(connection).get_table_names())
        with Operations.context(MigrationContext.configure(connection)):
            migration.upgrade()
            migration.upgrade()
        after = snapshot_history(connection)
        assert after["analyses"][0].pop("prompt_policy_version_id") is None
        assert after["analyses"][0].pop("prompt_snapshot_ciphertext") is None
        assert after == before
        assert set(inspect(connection).get_table_names()) - tables_before == {"prompt_policy_versions", "prompt_policy_state"}
        assert connection.scalar(text("SELECT COUNT(*) FROM prompt_policy_versions")) == 0
        assert connection.execute(text("PRAGMA foreign_key_check")).all() == []
        foreign_keys = connection.execute(text("PRAGMA foreign_key_list(analyses)")).mappings().all()
        assert any(fk["from"] == "prompt_policy_version_id" and fk["on_delete"] == "RESTRICT" for fk in foreign_keys)
        with Operations.context(MigrationContext.configure(connection)):
            with pytest.raises(RuntimeError, match="^cannot_downgrade_prompt_schema_with_analysis_history$"):
                migration.downgrade()
    engine.dispose()


def test_prompt_downgrade_refuses_saved_history(tmp_path, settings):
    engine = build_engine(f"sqlite+pysqlite:///{tmp_path / 'prompt-downgrade.db'}")
    Base.metadata.create_all(engine)
    crypto = CryptoService(settings.data_encryption_key, settings.encryption_key_version)
    with build_session_factory(engine)() as db:
        get_active_policy(db, crypto)
        db.commit()
    with engine.begin() as connection:
        before = connection.execute(PromptPolicyVersion.__table__.select()).mappings().all()
        with Operations.context(MigrationContext.configure(connection)):
            with pytest.raises(RuntimeError, match="^cannot_downgrade_with_prompt_policy_history$"):
                migration_module().downgrade()
        assert connection.execute(PromptPolicyVersion.__table__.select()).mappings().all() == before
    engine.dispose()
