"""Fresh/upgrade rehearsal on disposable SQLite, never deployment data."""
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import VLLMProfile, ProductionPromotion
from app.services.crypto import CryptoService
from app.services.input_schemas import get_schema_state
from app.services.prompt_policies import get_policy_state
from app.services.production_configurations import ensure_baseline, current_configuration


def test_upgrade_preserves_every_existing_column_and_captures_only_a_baseline(tmp_path, monkeypatch):
    settings = Settings(database_url=f"sqlite+pysqlite:///{tmp_path / 'lifecycle.db'}",
        data_encryption_key="AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=")
    monkeypatch.setattr("app.config.get_settings", lambda: settings)
    root = Path(__file__).resolve().parents[1]
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "alembic"))
    command.upgrade(config, "0021_official_evaluation")
    engine = create_engine(settings.database_url)
    crypto = CryptoService(settings.data_encryption_key, settings.encryption_key_version)
    with Session(engine) as db:
        db.add(VLLMProfile(name="existing-production", model_name="fixture", base_url="http://10.0.0.10:8000/v1",
            status="production", is_test=True, api_key_ciphertext=crypto.encrypt_text("DO-NOT-EXPOSE-KEY")))
        get_policy_state(db, crypto)
        get_schema_state(db, crypto)
        db.commit()
    with engine.connect() as connection:
        columns = {name: [c["name"] for c in inspect(connection).get_columns(name)]
            for name in inspect(connection).get_table_names() if name != "alembic_version"}
        before = {name: connection.execute(text(f'SELECT {",".join(names)} FROM "{name}"')).all() for name, names in columns.items()}
    command.upgrade(config, "head")
    command.upgrade(config, "head")
    with engine.connect() as connection:
        assert {name: connection.execute(text(f'SELECT {",".join(names)} FROM "{name}"')).all() for name, names in columns.items()} == before
        assert connection.execute(text("PRAGMA foreign_key_check")).all() == []
        assert connection.scalar(text("SELECT COUNT(*) FROM production_promotions")) == 0
    with Session(engine) as db:
        ensure_baseline(db, crypto, settings)
        db.commit()
        ensure_baseline(db, crypto, settings)
        db.commit()
        rows = list(db.scalars(select(ProductionPromotion)))
        assert len(rows) == 1 and rows[0].kind == "baseline"
        assert "DO-NOT-EXPOSE-KEY" not in str(rows[0].snapshot_json)
        assert current_configuration(db, crypto, settings)["configuration_id"] is None
    for table in ("production_promotions", "change_events"):
        for sql in (f"DELETE FROM {table}", f"UPDATE {table} SET id=id"):
            with pytest.raises(IntegrityError, match="immutable_history"), engine.begin() as connection:
                connection.execute(text(sql))
    with pytest.raises(RuntimeError, match="lifecycle_history_must_be_preserved"):
        command.downgrade(config, "0021_official_evaluation")
    engine.dispose()


def test_real_app_start_records_one_baseline_without_promoting(tmp_path, monkeypatch, settings):
    from fastapi.testclient import TestClient
    from app.main import create_app
    settings.database_url = f"sqlite+pysqlite:///{tmp_path / 'startup.db'}"
    monkeypatch.setattr("app.config.get_settings", lambda: settings)
    root = Path(__file__).resolve().parents[1]
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "alembic"))
    command.upgrade(config, "head")
    for _ in range(2):
        application = create_app(settings, create_schema=False)
        with TestClient(application) as client:
            assert client.get("/health/ready").status_code == 200
            with application.state.session_factory() as db:
                records = list(db.scalars(select(ProductionPromotion)))
                assert len(records) == 1 and records[0].kind == "baseline"
                assert db.scalar(select(VLLMProfile.id).where(VLLMProfile.status == "production")) is None
