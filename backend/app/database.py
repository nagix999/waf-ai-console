from collections.abc import Generator
import json

from fastapi import Request
from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.pool import StaticPool


class Base(DeclarativeBase):
    pass


def case_metadata_search(name, category, difficulty, tags, query):
    """Keep Unicode casefold/tag search semantics while paging inside SQLite."""
    tag_values = json.loads(tags) if tags else []
    text = " ".join(str(value or "") for value in (name, category, difficulty, tag_values))
    return int(query.casefold() in text.casefold())


def build_engine(database_url: str) -> Engine:
    kwargs: dict = {}
    if database_url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False, "timeout": 30}
        if database_url.endswith(":memory:"):
            kwargs["poolclass"] = StaticPool
    engine = create_engine(database_url, **kwargs)
    if database_url.startswith("sqlite"):
        @event.listens_for(engine, "connect")
        def configure_sqlite(dbapi_connection, _connection_record) -> None:
            dbapi_connection.create_function("waf_case_search", 5, case_metadata_search, deterministic=True)
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA busy_timeout=30000")
            cursor.close()
    return engine


def build_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)


def get_db(request: Request) -> Generator[Session, None, None]:
    session = request.app.state.session_factory()
    try:
        yield session
    finally:
        session.close()
