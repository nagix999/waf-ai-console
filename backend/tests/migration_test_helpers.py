"""Invoke one historical migration without bypassing a newer CLI guard."""
import importlib.util
from pathlib import Path

from alembic.migration import MigrationContext
from alembic.operations import Operations


def run_isolated_migration(connection, revision, direction):
    path = Path(__file__).resolve().parents[1] / "alembic" / "versions" / f"{revision}.py"
    spec = importlib.util.spec_from_file_location(f"isolated_{revision}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    with Operations.context(MigrationContext.configure(connection)):
        getattr(module, direction)()
