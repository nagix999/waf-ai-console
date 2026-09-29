"""Fresh + prior-revision upgrade: disposable synthetic SQLite only."""
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text

from app.config import Settings


@pytest.mark.parametrize("existing", [False, True])
def test_read_index_migration_preserves_data_and_is_reversible(tmp_path, monkeypatch, existing):
    url = f"sqlite+pysqlite:///{tmp_path / 'read-index.db'}"
    monkeypatch.setattr("app.config.get_settings", lambda: Settings(_env_file=None, database_url=url))
    root = Path(__file__).resolve().parents[1]
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "alembic"))
    engine = create_engine(url)
    if existing:
        command.upgrade(config, "0024_r5_contract")
        with engine.begin() as connection:
            # 0001 uses today's metadata; rehearse the actual old shape too.
            connection.execute(text("DROP INDEX IF EXISTS ix_analyses_created_id"))
            connection.execute(text("INSERT INTO analyses (id,source_system,event_id,company_name,src_ip,dest_ip,waf_vendor,waf_action,"
                "payload_ciphertext,encryption_key_version,extra_fields,status,attempt_count,input_truncated,created_at,updated_at) "
                "VALUES ('synthetic','fixture','event','fixture','192.0.2.1','198.51.100.1','generic','D',"
                "'encrypted-fixture','v1','{}','pending',0,0,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"))
            before = connection.execute(text("SELECT * FROM analyses")).all()
    command.upgrade(config, "head")
    command.upgrade(config, "head")
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "0025_analysis_read_index"
        assert any(index["name"] == "ix_analyses_created_id" and index["column_names"] == ["created_at", "id"] for index in inspect(connection).get_indexes("analyses"))
        plan = connection.execute(text("EXPLAIN QUERY PLAN SELECT id,status FROM analyses WHERE created_at >= '2020-01-01' ORDER BY created_at DESC,id DESC LIMIT 20")).all()
        assert any("ix_analyses_created_id" in row[3] for row in plan)
        assert not any("TEMP B-TREE" in row[3] for row in plan)
        assert connection.execute(text("PRAGMA foreign_key_check")).all() == []
        if existing:
            assert connection.execute(text("SELECT * FROM analyses")).all() == before
    command.downgrade(config, "0024_r5_contract")
    with engine.connect() as connection:
        assert all(index["name"] != "ix_analyses_created_id" for index in inspect(connection).get_indexes("analyses"))
        if existing:
            assert connection.execute(text("SELECT * FROM analyses")).all() == before
    engine.dispose()
