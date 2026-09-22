"""Rehearse a real pre-R5 schema on disposable data, never the operator DB."""
from pathlib import Path
import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from app.config import Settings
from app.models import Analysis


def test_r5_actual_upgrade_preserves_legacy_columns_and_new_history_blocks_downgrade(tmp_path, monkeypatch):
    url = f"sqlite+pysqlite:///{tmp_path / 'r5-upgrade.db'}"
    monkeypatch.setattr("app.config.get_settings", lambda: Settings(database_url=url))
    root = Path(__file__).resolve().parents[1]
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "alembic"))
    command.upgrade(config, "head")
    engine = create_engine(url)
    with engine.begin() as db:
        db.execute(Analysis.__table__.insert().values(id="synthetic", source_system="fixture", event_id="fixture-event",
            company_name="Example", src_ip="192.0.2.1", dest_ip="198.51.100.1", waf_vendor="generic", waf_action="D",
            payload_ciphertext="encrypted-fixture", encryption_key_version="fixture", result_json={"kept": True}))
    # Remove R5 columns, not just the Alembic marker: 0001 uses current metadata.
    command.downgrade(config, "0023_ground_truth_working")
    with engine.connect() as db:
        columns = {name: [c["name"] for c in inspect(db).get_columns(name)] for name in inspect(db).get_table_names() if name != "alembic_version"}
        assert "initial_verdict" not in columns["analyses"]
        assert "test_purpose" not in columns["test_runs"]
        before = {name: db.execute(text(f'SELECT {",".join(names)} FROM "{name}"')).all() for name, names in columns.items()}
    command.upgrade(config, "head")
    command.upgrade(config, "head")
    with engine.begin() as db:
        assert {name: db.execute(text(f'SELECT {",".join(names)} FROM "{name}"')).all() for name, names in columns.items()} == before
        assert db.execute(text("PRAGMA foreign_key_check")).all() == []
        assert db.scalar(text("SELECT count(*) FROM test_configuration_defaults")) == 0
        db.execute(text("UPDATE analyses SET initial_verdict='false_positive',initial_probability=0.7 WHERE id='synthetic'"))
    with pytest.raises(RuntimeError, match="r5_history_requires_pre_upgrade_backup"):
        command.downgrade(config, "0023_ground_truth_working")
    with engine.connect() as db:
        assert db.scalar(text("SELECT initial_probability FROM analyses WHERE id='synthetic'")) == .7
    engine.dispose()
