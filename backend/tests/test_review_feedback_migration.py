"""Fresh install, real pre-feedback schema upgrade and lossless rollback guard."""
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError

from app.config import Settings
from app.database import build_engine
from app.models import Analysis, Review

LEGACY_COLUMNS = "id,analysis_id,source_system,external_review_id,event_id,decision,analyst_id,comment,ai_visible,created_at"


def config_for(tmp_path, monkeypatch):
    url = f"sqlite+pysqlite:///{tmp_path / 'feedback-migration.db'}"
    monkeypatch.setattr("app.config.get_settings", lambda: Settings(_env_file=None, database_url=url))
    root = Path(__file__).resolve().parents[1]
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "alembic"))
    return config, build_engine(url)


def seed_analysis(connection):
    connection.execute(Analysis.__table__.insert().values(id="synthetic-analysis", source_system="synthetic-source",
        event_id="synthetic-event", company_name="Synthetic", src_ip="192.0.2.1", dest_ip="198.51.100.1",
        waf_vendor="generic", waf_action="D", payload_ciphertext="synthetic-encrypted-placeholder", encryption_key_version="v1"))


@pytest.mark.parametrize("existing", [False, True])
def test_upgrade_preserves_legacy_decisions_ciphertext_indexes_and_constraints(tmp_path, monkeypatch, existing):
    config, engine = config_for(tmp_path, monkeypatch)
    if existing:
        command.upgrade(config, "0026_test_run_stop")
        with engine.begin() as connection:
            # 0001 bootstraps today's metadata. Rehearse a real deployed v26
            # table, which had no rating and required a decision.
            with Operations(MigrationContext.configure(connection)).batch_alter_table("reviews") as batch:
                batch.drop_constraint("ck_review_rating", type_="check")
                batch.drop_column("rating")
                batch.alter_column("decision", existing_type=Review.__table__.c.decision.type, nullable=False)
            seed_analysis(connection)
            connection.execute(Review.__table__.insert().values(id="legacy-review", analysis_id="synthetic-analysis",
                source_system="synthetic-source", external_review_id="legacy-external", event_id="synthetic-event",
                decision="deferred", analyst_id="synthetic-analyst", comment="Legacy opinion", ai_visible=True))
            reviews_before = connection.execute(text(f"SELECT {LEGACY_COLUMNS} FROM reviews")).all()
            analysis_before = connection.execute(text("SELECT * FROM analyses")).all()
            fields = {column["name"]: column for column in inspect(connection).get_columns("reviews")}
            assert "rating" not in fields and not fields["decision"]["nullable"]
    command.upgrade(config, "head")
    command.upgrade(config, "head")
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "0027_review_feedback"
        fields = {column["name"]: column for column in inspect(connection).get_columns("reviews")}
        assert fields["decision"]["nullable"] and fields["rating"]["nullable"]
        assert "ck_review_rating" in {item["name"] for item in inspect(connection).get_check_constraints("reviews")}
        assert "uq_review_source_external" in {item["name"] for item in inspect(connection).get_unique_constraints("reviews")}
        assert "ix_reviews_analysis_created" in {item["name"] for item in inspect(connection).get_indexes("reviews")}
        assert connection.execute(text("PRAGMA foreign_key_check")).all() == []
        if existing:
            assert connection.execute(text(f"SELECT {LEGACY_COLUMNS} FROM reviews")).all() == reviews_before
            assert connection.scalar(text("SELECT rating FROM reviews")) is None
            assert connection.execute(text("SELECT * FROM analyses")).all() == analysis_before
    command.downgrade(config, "0026_test_run_stop")
    with engine.connect() as connection:
        fields = {column["name"]: column for column in inspect(connection).get_columns("reviews")}
        assert "rating" not in fields and not fields["decision"]["nullable"]
        if existing:
            assert connection.execute(text(f"SELECT {LEGACY_COLUMNS} FROM reviews")).all() == reviews_before
    command.upgrade(config, "head")
    engine.dispose()


@pytest.mark.parametrize("rating", [None, "like", "dislike"])
def test_downgrade_refuses_to_lose_new_feedback_even_when_both_fields_omitted(tmp_path, monkeypatch, rating):
    config, engine = config_for(tmp_path, monkeypatch)
    command.upgrade(config, "head")
    with engine.begin() as connection:
        seed_analysis(connection)
        connection.execute(Review.__table__.insert().values(id="new-feedback", analysis_id="synthetic-analysis",
            source_system="synthetic-source", external_review_id="new-external", event_id="synthetic-event", rating=rating))
        before = connection.execute(text("SELECT * FROM reviews")).all()
    with pytest.raises(RuntimeError, match="review_feedback_history_requires_current_schema"):
        command.downgrade(config, "0026_test_run_stop")
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "0027_review_feedback"
        assert connection.execute(text("SELECT * FROM reviews")).all() == before
    with engine.begin() as connection:
        with pytest.raises(IntegrityError):
            with connection.begin_nested():
                connection.execute(text("UPDATE reviews SET rating='true_positive'"))
        connection.execute(text("DELETE FROM analyses WHERE id='synthetic-analysis'"))
        assert connection.scalar(text("SELECT COUNT(*) FROM reviews")) == 0
    engine.dispose()
