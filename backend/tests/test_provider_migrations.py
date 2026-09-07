"""Synthetic legacy upgrade checks, including encrypted history preservation."""

import importlib.util
from datetime import UTC, datetime
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, inspect, text

from app.database import Base
from app.models import AgentRun, AgentStep, Analysis, VLLMProfile, VLLMTestRun
from app.services.vllm_profiles import profile_fingerprint


def migration_module():
    path = Path(__file__).resolve().parents[1] / "alembic/versions/0004_model_providers.py"
    spec = importlib.util.spec_from_file_location("model_provider_migration", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def seed_history(connection):
    verified_at = datetime(2026, 1, 2, tzinfo=UTC)
    config = dict(
        id="legacy-profile", name="synthetic-legacy", base_url="http://vllm.internal:8000/v1",
        model_name="synthetic-model", api_key_ciphertext="synthetic-encrypted-key", encryption_key_version="test-key",
        timeout_seconds=120, context_window=32768, max_output_tokens=3072, test_concurrency=3,
        tls_verify=False, status="production", last_verified_at=verified_at,
    )
    fingerprint = profile_fingerprint(VLLMProfile(**config))
    connection.execute(VLLMProfile.__table__.insert().values(**config))
    connection.execute(VLLMTestRun.__table__.insert().values(
        id="legacy-test", profile_id="legacy-profile", mode="full", status="passed", profile_fingerprint=fingerprint,
        checks_json=[{"name": "synthetic-check", "passed": True}], metrics_json={"synthetic_duration": 1.25},
        completed_at=verified_at,
    ))
    connection.execute(Analysis.__table__.insert().values(
        id="legacy-analysis", source_system="synthetic-source", event_id="legacy-event", company_name="Synthetic",
        src_ip="192.0.2.1", dest_ip="198.51.100.1", waf_vendor="generic", waf_action="D",
        payload_ciphertext="synthetic-encrypted-payload", encryption_key_version="test-key",
        result_json={"schema_version": "waf-analysis-v1", "uncertainties": ["synthetic-legacy"]},
        model_profile="synthetic-legacy", status="completed",
    ))
    connection.execute(AgentRun.__table__.insert().values(
        id="legacy-run", analysis_id="legacy-analysis", status="completed", fingerprint="synthetic-run-fingerprint",
    ))
    connection.execute(AgentStep.__table__.insert().values(
        id="legacy-step", run_id="legacy-run", sequence=1, step_type="llm", name="synthetic-primary", status="completed",
        input_ciphertext="synthetic-encrypted-input", output_ciphertext="synthetic-encrypted-output",
        encryption_key_version="test-key",
    ))
    return fingerprint


def snapshot_history(connection):
    # Constant table names only; no input data is interpolated into SQL.
    return {
        table: [dict(row) for row in connection.execute(text(f"SELECT * FROM {table} ORDER BY id")).mappings()]
        for table in ("vllm_profiles", "vllm_test_runs", "analyses", "agent_runs", "agent_steps")
    }


def test_additive_provider_upgrade_preserves_legacy_keys_results_and_verification(tmp_path):
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'provider-legacy.db'}")
    Base.metadata.create_all(engine)
    migration = migration_module()
    with engine.begin() as connection:
        connection.execute(text("PRAGMA foreign_keys=ON"))
        fingerprint = seed_history(connection)
        with Operations.context(MigrationContext.configure(connection)):
            migration.downgrade()
        before = snapshot_history(connection)
        assert "provider" not in before["vllm_profiles"][0]
        with Operations.context(MigrationContext.configure(connection)):
            migration.upgrade()
            migration.upgrade()  # Fresh/current metadata and repeated upgrade are safe.
        after = snapshot_history(connection)
        profile = after["vllm_profiles"][0]
        assert profile.pop("provider") == "vllm"
        assert profile.pop("external_data_approved") == 0
        assert before == after
        restored = VLLMProfile(**dict(connection.execute(VLLMProfile.__table__.select()).mappings().one()))
        assert profile_fingerprint(restored) == fingerprint
        assert connection.scalar(text("SELECT profile_fingerprint FROM vllm_test_runs")) == fingerprint
        indexes = {index["name"]: index for index in inspect(connection).get_indexes("vllm_profiles")}
        assert indexes["uq_vllm_single_production"]["unique"] == 1
        assert connection.execute(text("PRAGMA foreign_key_check")).all() == []
    engine.dispose()


@pytest.mark.parametrize(("provider", "approved"), [("openai", True), ("openai", False), ("unknown", False), ("vllm", True)])
def test_downgrade_refuses_to_reinterpret_external_profiles_or_keys(tmp_path, provider, approved):
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'provider-downgrade.db'}")
    Base.metadata.create_all(engine)
    migration = migration_module()
    with engine.begin() as connection:
        seed_history(connection)
        connection.execute(VLLMProfile.__table__.update().values(provider=provider, external_data_approved=approved, status="disabled"))
        before = snapshot_history(connection)
        with Operations.context(MigrationContext.configure(connection)):
            with pytest.raises(RuntimeError, match="^cannot_downgrade_model_providers_with_external_profiles$"):
                migration.downgrade()
        assert snapshot_history(connection) == before
        assert {"provider", "external_data_approved"} <= {
            column["name"] for column in inspect(connection).get_columns("vllm_profiles")
        }
    engine.dispose()
