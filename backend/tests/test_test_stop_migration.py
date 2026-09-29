"""Fresh/real prior schema upgrade and guarded downgrade, disposable SQLite."""
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import inspect, text

from app.config import Settings
from app.database import build_engine


@pytest.mark.parametrize("existing", [False, True])
def test_stop_migration_additive_and_reversible_without_history(tmp_path, monkeypatch, existing):
    url = f"sqlite+pysqlite:///{tmp_path / 'migration.db'}"
    monkeypatch.setattr("app.config.get_settings", lambda: Settings(_env_file=None, database_url=url))
    root = Path(__file__).resolve().parents[1]
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "alembic"))
    engine = build_engine(url)
    if existing:
        command.upgrade(config, "0025_analysis_read_index")
        with engine.begin() as db:
            # 0001 uses current metadata. Reconstruct the actual pre-PR B table.
            for column in ("stopped_at", "stopped_by"):
                db.execute(text(f"ALTER TABLE test_runs DROP COLUMN {column}"))
            db.execute(text("INSERT INTO analyses (id,source_system,event_id,company_name,src_ip,dest_ip,waf_vendor,waf_action,"
                "payload_ciphertext,encryption_key_version,extra_fields,status,attempt_count,input_truncated,created_at,updated_at) "
                "VALUES ('fixture','fixture','fixture','fixture','192.0.2.1','198.51.100.1','generic','D',"
                "'encrypted-fixture','v1','{}','completed',0,0,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"))
            before = db.execute(text("SELECT * FROM analyses")).all()
    command.upgrade(config, "head")
    command.upgrade(config, "head")
    with engine.connect() as db:
        assert db.scalar(text("SELECT version_num FROM alembic_version")) == "0026_test_run_stop"
        fields = {row["name"]: row for row in inspect(db).get_columns("test_runs")}
        assert fields["stopped_at"]["nullable"] and fields["stopped_by"]["nullable"]
        assert db.execute(text("PRAGMA foreign_key_check")).all() == []
        if existing:
            assert db.execute(text("SELECT * FROM analyses")).all() == before
    command.downgrade(config, "0025_analysis_read_index")
    with engine.connect() as db:
        assert "stopped_at" not in {row["name"] for row in inspect(db).get_columns("test_runs")}
        if existing:
            assert db.execute(text("SELECT * FROM analyses")).all() == before
    command.upgrade(config, "head")
    if existing:
        with engine.begin() as db:
            db.execute(text("UPDATE analyses SET status='canceled' WHERE id='fixture'"))
        with pytest.raises(RuntimeError, match="test_stop_history_requires_current_schema"):
            command.downgrade(config, "0025_analysis_read_index")
        with engine.connect() as db:
            assert db.scalar(text("SELECT version_num FROM alembic_version")) == "0026_test_run_stop"
            assert db.scalar(text("SELECT status FROM analyses WHERE id='fixture'")) == "canceled"
    engine.dispose()
