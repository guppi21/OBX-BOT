import concurrent.futures
import uuid
import tempfile
import os
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

from packages.database.base import Base
from packages.shared.enums import TaskType, SubmissionStatus
from packages.shared.exceptions import RewardPoolExhaustedError
from apps.obx_tasks.services.task_service import TaskService
from apps.obx_core.services.wallet_service import WalletService
from apps.obx_core.services.reconciliation import ReconciliationService


def test_concurrent_task_approvals_prevent_overspend():
    """Verify that multiple concurrent threads approving submissions simultaneously cannot overspend the task reward pool."""
    pg_url = os.environ.get("TEST_DATABASE_URL")
    
    if pg_url and not pg_url.startswith("sqlite"):
        engine = create_engine(pg_url, poolclass=NullPool, echo=False)
        Base.metadata.create_all(bind=engine)
        SessionFactory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    else:
        tmpdir = tempfile.mkdtemp()
        db_path = os.path.join(tmpdir, "task_concurrency_test.db")
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

    # Create task with total pool 300 OBX, reward per user 100 OBX (Max 3 approvals)
    # We will submit 8 submissions and attempt concurrent approval on all 8
    task_id = None
    submission_ids = []

    with SessionFactory() as s:
        srv = TaskService(s)
        task = srv.create_task(
            title=f"Concurrent RT {uuid.uuid4().hex[:6]}",
            description="Concurrent test",
            task_type=TaskType.RETWEET,
            target_url="https://x.com/test",
            reward_per_user=100,
            total_reward_pool=300,
            created_by="admin_concurrent",
        )
        task_id = task.id

        for i in range(8):
            u_id = f"conc_user_{i}_{uuid.uuid4().hex[:4]}"
            sub = srv.submit_task(
                task_id=task.id,
                discord_user_id=u_id,
                x_username=f"x_user_{i}",
                proof_url=f"https://x.com/u/{i}",
                proof_text="Proof text",
            )
            submission_ids.append(sub.id)

    successful_approvals = []
    failed_approvals = []

    def approve_worker(sub_id):
        with SessionFactory() as session:
            service = TaskService(session)
            try:
                service.approve_submission(sub_id, "admin_approver")
                return ("SUCCESS", sub_id)
            except RewardPoolExhaustedError:
                return ("EXHAUSTED", sub_id)

    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as executor:
        futures = [executor.submit(approve_worker, sid) for sid in submission_ids]
        for f in concurrent.futures.as_completed(futures):
            res_type, sid = f.result()
            if res_type == "SUCCESS":
                successful_approvals.append(sid)
            else:
                failed_approvals.append(sid)

    # Exactly 3 approvals must have succeeded
    assert len(successful_approvals) == 3
    assert len(failed_approvals) == 5

    with SessionFactory() as s:
        srv = TaskService(s)
        final_task = srv.get_task(task_id)
        assert final_task.distributed_reward == 300
        assert final_task.remaining_reward_pool == 0
        assert final_task.status == "COMPLETED"

        recon = ReconciliationService(s)
        report = recon.reconcile_all()
        assert report.is_consistent is True

    engine.dispose()
