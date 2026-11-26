"""The timing benchmark must remain synthetic, read-only and reproducible."""
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError

from scripts.benchmark_performance_summary import (
    expected_summary, measure_database, seed_database,
)


@pytest.mark.parametrize("count", [1, 11, 120, 1000])
def test_frozen_and_current_aggregates_match_independent_oracle(tmp_path, count):
    path = tmp_path / "synthetic.sqlite"
    seed_database(path, count)
    expected = expected_summary(count)
    for mode in ("baseline", "current"):
        report = measure_database(path, mode)
        assert report["oracle_matches"] is True
        assert report["cases"] == count
        assert report["completed_cases"] == expected["completed_cases"]
        assert report["completed_timings"] == expected["completed_timings"]
        assert report["sql_count"] <= 4


def test_refuses_existing_database_and_unmarked_database(tmp_path):
    path = tmp_path / "not-a-benchmark.sqlite"
    engine = create_engine(f"sqlite+pysqlite:///{path}")
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE untouched (value TEXT)"))
        connection.execute(text("INSERT INTO untouched VALUES ('preserve')"))
    with pytest.raises(ValueError, match="Refusing to overwrite"):
        seed_database(path, 10)
    with pytest.raises(OperationalError, match="benchmark_marker"):
        measure_database(path, "current")
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT value FROM untouched")) == "preserve"
    engine.dispose()


def test_wall_time_mode_supports_production_materialized_scope_without_tracing(tmp_path):
    path = tmp_path / "synthetic.sqlite"
    seed_database(path, 120)
    reports = [measure_database(path, mode, measure_memory=False, materialized_scope=True)
        for mode in ("baseline", "current")]
    assert reports[0]["completed_timings"] == reports[1]["completed_timings"]
    for report in reports:
        assert report["oracle_matches"] is True
        assert report["measurement"] == "wall_time_only"
        assert report["python_peak_mib"] is None
        assert report["materialized_scope"] is True
