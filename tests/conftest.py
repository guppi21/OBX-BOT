import os
import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool, NullPool
from fastapi.testclient import TestClient

from packages.database.base import Base
from packages.database.session import get_db
from apps.obx_core.main import create_app
from apps.obx_core.services.wallet_service import WalletService


@pytest.fixture(scope="session")
def test_engine():
    db_url = os.environ.get("TEST_DATABASE_URL")
    if not db_url or "sqlite" in db_url:
        engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
            echo=False,
        )

        @event.listens_for(engine, "connect")
        def set_sqlite_pragma(dbapi_connection, connection_record):
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

        @event.listens_for(engine, "begin")
        def do_begin(conn):
            conn.exec_driver_sql("BEGIN IMMEDIATE")
    else:
        # PostgreSQL engine for isolated testing database
        engine = create_engine(
            db_url,
            poolclass=NullPool,
            echo=False,
        )

    Base.metadata.create_all(bind=engine)
    yield engine
    Base.metadata.drop_all(bind=engine)
    engine.dispose()


@pytest.fixture(scope="function")
def db_session(test_engine):
    """Provides an isolated database session per test with clean tables."""
    Base.metadata.drop_all(bind=test_engine)
    Base.metadata.create_all(bind=test_engine)
    
    SessionLocal = sessionmaker(
        bind=test_engine,
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
    )
    session = SessionLocal()

    yield session

    session.close()


@pytest.fixture(scope="function")
def client(test_engine, db_session):
    """FastAPI TestClient with overridden DB dependency."""
    app = create_app()

    SessionLocal = sessionmaker(
        bind=test_engine,
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
    )

    def override_get_db():
        session = SessionLocal()
        try:
            yield session
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


@pytest.fixture
def wallet_service(db_session):
    return WalletService(db_session)
