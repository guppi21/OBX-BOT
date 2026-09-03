import concurrent.futures
import uuid
import tempfile
import os
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

from packages.database.base import Base
from apps.obx_core.services.wallet_service import WalletService
from apps.obx_core.services.reconciliation import ReconciliationService


def test_concurrent_credits_and_debits():
    """Verify that multiple concurrent threads modifying the same wallet produce exact expected balances without race condition drift."""
    pg_url = os.environ.get("TEST_DATABASE_URL") or os.environ.get("DATABASE_URL")
    
    if pg_url and not pg_url.startswith("sqlite"):
        engine = create_engine(pg_url, poolclass=NullPool, echo=False)
        Base.metadata.create_all(bind=engine)
        SessionFactory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    else:
        tmpdir = tempfile.mkdtemp()
        db_path = os.path.join(tmpdir, "concurrency_test.db")
        db_url = f"sqlite:///{db_path}"

        engine = create_engine(
            db_url,
            connect_args={"timeout": 60, "check_same_thread": False},
            echo=False,
        )

        @event.listens_for(engine, "connect")
        def set_sqlite_pragma(dbapi_connection, connection_record):
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA journal_mode=WAL;")
            cursor.execute("PRAGMA busy_timeout=60000;")
            cursor.close()

        @event.listens_for(engine, "begin")
        def do_begin(conn):
            conn.exec_driver_sql("BEGIN IMMEDIATE")

        Base.metadata.create_all(bind=engine)
        SessionFactory = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    discord_user_id = f"user_concurrent_{uuid.uuid4().hex[:8]}"

    # Initialize user with 10,000 OBX
    with SessionFactory() as init_session:
        service = WalletService(init_session)
        service.credit(
            discord_user_id=discord_user_id,
            amount=10000,
            reference_type="initial_seed",
            idempotency_key=f"initial_seed_concurrent_{uuid.uuid4()}",
        )

    # We run 30 parallel operations across threads:
    # 15 credits of 100 each (+1500)
    # 15 debits of 50 each (-750)
    # Net change = +750 -> Expected final available balance = 10750
    num_credits = 15
    num_debits = 15

    def do_credit(i: int):
        with SessionFactory() as s:
            srv = WalletService(s)
            srv.credit(
                discord_user_id=discord_user_id,
                amount=100,
                reference_type="thread_credit",
                idempotency_key=f"thread_credit_{i}_{uuid.uuid4()}",
            )

    def do_debit(i: int):
        with SessionFactory() as s:
            srv = WalletService(s)
            srv.debit(
                discord_user_id=discord_user_id,
                amount=50,
                reference_type="thread_debit",
                idempotency_key=f"thread_debit_{i}_{uuid.uuid4()}",
            )

    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as executor:
        futures = []
        for i in range(num_credits):
            futures.append(executor.submit(do_credit, i))
        for i in range(num_debits):
            futures.append(executor.submit(do_debit, i))

        for f in concurrent.futures.as_completed(futures):
            f.result()

    with SessionFactory() as verify_session:
        service = WalletService(verify_session)
        balance = service.get_balance(discord_user_id)
        expected_balance = 10000 + (num_credits * 100) - (num_debits * 50)
        assert balance["available_balance"] == expected_balance
        assert balance["locked_balance"] == 0

        # Reconciliation check must also be 100% consistent
        recon = ReconciliationService(verify_session)
        disc = recon.reconcile_user(discord_user_id)
        assert disc is None

    engine.dispose()
