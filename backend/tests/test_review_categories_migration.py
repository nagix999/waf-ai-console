"""Fresh install and real v27 upgrade preserve all historical feedback."""
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

HISTORICAL_COLUMNS = (
    "id,analysis_id,source_system,external_review_id,event_id,decision,rating,"
    "analyst_id,comment,ai_visible,created_at"
)


def config_for(tmp_path, monkeypatch):
    url = f"sqlite+pysqlite:///{tmp_path / 'category-migration.db'}"
    monkeypatch.setattr("app.config.get_settings", lambda: Settings(_env_file=None, database_url=url))
    root = Path(__file__).resolve().parents[1]
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "alembic"))
    return config, build_engine(url)


def seed_analysis(connection):
    connection.execute(Analysis.__table__.insert().values(
        id="synthetic-analysis", source_system="synthetic-source", event_id="synthetic-event",
        company_name="Synthetic", src_ip="192.0.2.1", dest_ip="198.51.100.1", waf_vendor="generic",
        waf_action="D", payload_ciphertext="synthetic-encrypted-placeholder", encryption_key_version="v1",
        result_json={"verdict": "true_positive", "synthetic": True},
    ))


def check_structure(connection):
    fields = {column["name"]: column for column in inspect(connection).get_columns("reviews")}
    assert not fields["feedback_categories"]["nullable"]
    assert fields["feedback_categories"]["default"] == "'[]'"
    checks = {item["name"] for item in inspect(connection).get_check_constraints("reviews")}
    assert {"ck_review_feedback_categories", "ck_review_rating"} <= checks
    assert "uq_review_source_external" in {item["name"] for item in inspect(connection).get_unique_constraints("reviews")}
    assert "ix_reviews_analysis_created" in {item["name"] for item in inspect(connection).get_indexes("reviews")}
    assert connection.execute(text("PRAGMA foreign_key_check")).all() == []


@pytest.mark.parametrize("existing", [False, True])
def test_fresh_and_existing_upgrade_preserves_reviews_and_ciphertext(tmp_path, monkeypatch, existing):
    config, engine = config_for(tmp_path, monkeypatch)
    if existing:
        command.upgrade(config, "0027_review_feedback")
        with engine.begin() as connection:
            # 0001 bootstraps current metadata, so remove the new column to
            # rehearse the actual deployed v27 schema, not today's metadata.
            with Operations(MigrationContext.configure(connection)).batch_alter_table("reviews") as batch:
                batch.drop_constraint("ck_review_feedback_categories", type_="check")
                batch.drop_column("feedback_categories")
            seed_analysis(connection)
            for identifier, decision, rating, comment in (
                ("legacy-decision", "deferred", None, "과거 판정"),
                ("legacy-like", None, "like", "과거 좋아요"),
                ("legacy-dislike", None, "dislike", "과거 싫어요"),
                ("legacy-comment", None, None, "의견만 남김"),
                ("legacy-empty", None, None, None),
            ):
                connection.execute(text(f"INSERT INTO reviews ({HISTORICAL_COLUMNS}) VALUES "
                    "(:id,'synthetic-analysis','synthetic-source',:id,'synthetic-event',:decision,:rating,"
                    "'synthetic-analyst',:comment,1,'2026-10-01 00:00:00')"),
                    {"id": identifier, "decision": decision, "rating": rating, "comment": comment})
            before_reviews = connection.execute(text(f"SELECT {HISTORICAL_COLUMNS} FROM reviews ORDER BY id")).all()
            before_analysis = connection.execute(text("SELECT * FROM analyses")).all()
    command.upgrade(config, "head")
    command.upgrade(config, "head")
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "0029_parser_management"
        check_structure(connection)
        if existing:
            assert connection.execute(text(f"SELECT {HISTORICAL_COLUMNS} FROM reviews ORDER BY id")).all() == before_reviews
            assert connection.execute(text("SELECT DISTINCT feedback_categories FROM reviews")).all() == [("[]",)]
            assert connection.execute(text("SELECT * FROM analyses")).all() == before_analysis
    command.downgrade(config, "0027_review_feedback")
    with engine.connect() as connection:
        assert "feedback_categories" not in {column["name"] for column in inspect(connection).get_columns("reviews")}
        assert connection.execute(text("PRAGMA foreign_key_check")).all() == []
        if existing:
            assert connection.execute(text(f"SELECT {HISTORICAL_COLUMNS} FROM reviews ORDER BY id")).all() == before_reviews
            assert connection.execute(text("SELECT * FROM analyses")).all() == before_analysis
    command.upgrade(config, "head")
    with engine.connect() as connection:
        check_structure(connection)
    engine.dispose()


def test_nonempty_categories_prevent_lossy_downgrade(tmp_path, monkeypatch):
    config, engine = config_for(tmp_path, monkeypatch)
    command.upgrade(config, "head")
    with engine.begin() as connection:
        seed_analysis(connection)
        connection.execute(Review.__table__.insert().values(
            id="new-categories", analysis_id="synthetic-analysis", source_system="synthetic-source",
            event_id="synthetic-event", external_review_id="synthetic-categories",
            feedback_categories=["insufficient_evidence", "other"], comment="설명 보완 요청",
        ))
        before = connection.execute(text("SELECT * FROM reviews")).all()
    with pytest.raises(RuntimeError, match="review_categories_history_requires_current_schema"):
        command.downgrade(config, "0027_review_feedback")
    with engine.connect() as connection:
        # The unused parser migration can downgrade, but feedback history must
        # still stop the next step at its own revision rather than the new head.
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "0028_review_categories"
        assert connection.execute(text("SELECT * FROM reviews")).all() == before
        assert connection.execute(text("PRAGMA foreign_key_check")).all() == []
    engine.dispose()


@pytest.mark.parametrize("categories", [None, "null", "{}", '"other"', "true", '["other","other","other","other","other","other","other"]'])
def test_database_rejects_null_nonarray_and_overlong_categories(tmp_path, monkeypatch, categories):
    config, engine = config_for(tmp_path, monkeypatch)
    command.upgrade(config, "head")
    with engine.begin() as connection:
        seed_analysis(connection)
        connection.execute(Review.__table__.insert().values(
            id="synthetic-empty", analysis_id="synthetic-analysis", source_system="synthetic-source",
            event_id="synthetic-event", external_review_id="synthetic-empty",
        ))
        with pytest.raises(IntegrityError):
            with connection.begin_nested():
                connection.execute(text("UPDATE reviews SET feedback_categories=:categories"), {"categories": categories})
        assert connection.scalar(text("SELECT feedback_categories FROM reviews")) == "[]"
    engine.dispose()
