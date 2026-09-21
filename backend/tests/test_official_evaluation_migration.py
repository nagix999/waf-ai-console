"""Rehearse old schema without touching runtime data."""
import importlib.util
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import inspect, text

from test_validation_data import add_item, create_dataset, dataset_run, forbid_model_calls, login


def test_upgrade_preserves_old_tests_without_official_backfill(client, event_payload):
    login(client)
    dataset = create_dataset(client)
    add_item(client, dataset, event_payload, "inconclusive")
    run = dataset_run(client, dataset)
    path = Path(__file__).resolve().parents[1] / "alembic/versions/0021_official_evaluation.py"
    spec = importlib.util.spec_from_file_location("official_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    with client.app.state.engine.begin() as connection:
        before = connection.execute(text("SELECT id,prompt_snapshot_ciphertext,input_schema_snapshot_ciphertext,dataset_version_id FROM test_runs")).all()
        with Operations.context(MigrationContext.configure(connection)):
            migration.downgrade()
            assert "evaluation_mode" not in {column["name"] for column in inspect(connection).get_columns("test_runs")}
            migration.upgrade()
            migration.upgrade()
        assert connection.execute(text("SELECT id,prompt_snapshot_ciphertext,input_schema_snapshot_ciphertext,dataset_version_id FROM test_runs")).all() == before
        assert connection.execute(text("SELECT evaluation_mode,approved_item_version_ids,official_evaluation_pending FROM test_runs")).one() == ("reference", None, 0)
        assert connection.execute(text("PRAGMA foreign_key_check")).all() == []
    current = client.get(f"/api/v1/test-runs/{run['id']}").json()
    assert current["items"] == run["items"] and current["ground_truth"] is None
    with client.app.state.engine.begin() as connection:
        connection.execute(text("UPDATE test_runs SET evaluation_mode='ground_truth'"))
        with Operations.context(MigrationContext.configure(connection)):
            with pytest.raises(RuntimeError, match="official_evaluation_downgrade_requires_pre_upgrade_backup"):
                migration.downgrade()
