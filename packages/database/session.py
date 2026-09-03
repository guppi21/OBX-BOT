from contextlib import contextmanager
from typing import Generator
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker
from packages.shared.config import get_settings


def create_db_engine(database_url: str | None = None) -> Engine:
    settings = get_settings()
    url = database_url or settings.DATABASE_URL
    
    connect_args = {}
    if url.startswith("sqlite"):
        connect_args = {"check_same_thread": False, "timeout": 30}
        
    engine = create_engine(
        url,
        echo=False,
        future=True,
        pool_pre_ping=True,
        connect_args=connect_args,
    )

    if url.startswith("sqlite"):
        @event.listens_for(engine, "connect")
        def set_sqlite_pragma(dbapi_connection, connection_record):
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

        @event.listens_for(engine, "begin")
        def do_begin(conn):
            # Enforce immediate transaction mode on SQLite to prevent write-skew / race condition
            conn.exec_driver_sql("BEGIN IMMEDIATE")

    return engine


_engine: Engine | None = None
_session_factory: sessionmaker[Session] | None = None


def get_engine() -> Engine:
    global _engine
    if _engine is None:
        _engine = create_db_engine()
    return _engine


def get_session_factory(engine: Engine | None = None) -> sessionmaker[Session]:
    global _session_factory
    if engine is not None:
        return sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
    if _session_factory is None:
        _session_factory = sessionmaker(bind=get_engine(), autoflush=False, autocommit=False, expire_on_commit=False)
    return _session_factory


def get_db() -> Generator[Session, None, None]:
    """FastAPI dependency for database session."""
    factory = get_session_factory()
    session = factory()
    try:
        yield session
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


@contextmanager
def session_scope(factory: sessionmaker[Session] | None = None) -> Generator[Session, None, None]:
    """Provide a transactional scope around a series of operations."""
    sf = factory or get_session_factory()
    session = sf()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
