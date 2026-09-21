"""Rehearse the actual preceding table shape using isolated synthetic history."""
import importlib.util
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import inspect, text

from test_validation_data import add_item, create_dataset, dataset_run, forbid_model_calls, login


def test_candidate_upgrade_keeps_historical_run_snapshots_unclassified(client, event_payload):
    login(client)
    dataset = create_dataset(client)
    add_item(client, dataset, event_payload, "inconclusive")
    run = dataset_run(client, dataset)
    path = Path(__file__).resolve().parents[1] / "alembic/versions/0020_test_candidate_configuration.py"
    spec = importlib.util.spec_from_file_location("candidate_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    with client.app.state.engine.begin() as connection:
        before = connection.execute(text("SELECT id,prompt_snapshot_ciphertext,input_schema_snapshot_ciphertext,dataset_version_id FROM test_runs")).all()
        with Operations.context(MigrationContext.configure(connection)):
            migration.downgrade()
            assert "configuration_hash" not in {c["name"] for c in inspect(connection).get_columns("test_runs")}
            migration.upgrade()
            migration.upgrade()
        assert connection.execute(text("SELECT id,prompt_snapshot_ciphertext,input_schema_snapshot_ciphertext,dataset_version_id FROM test_runs")).all() == before
        assert connection.execute(text("SELECT configuration_hash,configuration_snapshot_json FROM test_runs")).one() == (None, None)
        assert connection.execute(text("PRAGMA foreign_key_check")).all() == []
    current = client.get(f"/api/v1/test-runs/{run['id']}").json()
    assert current["items"] == run["items"] and current["configuration_snapshot"] is None
    with client.app.state.engine.begin() as connection:
        connection.execute(text("UPDATE test_runs SET configuration_hash=:hash"), {"hash": "a" * 64})
        with Operations.context(MigrationContext.configure(connection)):
            with pytest.raises(RuntimeError, match="candidate_configuration_downgrade_requires_pre_upgrade_backup"):
                migration.downgrade()
