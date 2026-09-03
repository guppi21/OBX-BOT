import pytest
import concurrent.futures
import uuid
import os
import tempfile
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

from packages.database.base import Base
from packages.shared.enums import TaskStatus, TaskType, SubmissionStatus
from packages.shared.exceptions import (
    InvalidRewardPoolError,
    TaskNotActiveError,
    RewardPoolExhaustedError,
)
from apps.obx_tasks.services.task_service import TaskService
from apps.obx_core.services.wallet_service import WalletService
from apps.obx_core.services.reconciliation import ReconciliationService


@pytest.fixture
def task_service(db_session):
    return TaskService(db_session)


@pytest.fixture
def wallet_service(db_session):
    return WalletService(db_session)


@pytest.fixture
def recon_service(db_session):
    return ReconciliationService(db_session)


def test_1_increase_reward_pool(task_service):
    task = task_service.create_task(
        title="Initial Task",
        description="Desc",
        task_type=TaskType.RETWEET,
        target_url="https://x.com/post",
        reward_per_user=100,
        total_reward_pool=10000,
        created_by="admin_1",
    )

    # Distribute 3000 OBX across 30 users
    for i in range(30):
        sub = task_service.submit_task(task.id, f"u_pool_{i}", f"x_{i}", f"https://p/{i}", "text")
        task_service.approve_submission(sub.id, "admin_1")

    refreshed = task_service.get_task(task.id)
    assert refreshed.distributed_reward == 3000
    assert refreshed.remaining_reward_pool == 7000

    # Admin increases pool to 20,000 OBX
    edited = task_service.edit_task(
        task_id=task.id,
        changed_by="super_admin",
        total_reward_pool=20000,
    )

    assert edited.total_reward_pool == 20000
    assert edited.distributed_reward == 3000
    assert edited.remaining_reward_pool == 17000


def test_2_and_3_decrease_reward_pool_safely_and_reject_below_distributed(task_service):
    task = task_service.create_task(
        title="Pool Resize Task",
        description="Desc",
        task_type=TaskType.RETWEET,
        target_url="https://x.com/post",
        reward_per_user=100,
        total_reward_pool=10000,
        created_by="admin_1",
    )

    # Distribute 3000 OBX
    for i in range(30):
        sub = task_service.submit_task(task.id, f"u_dec_{i}", f"x_{i}", f"https://p/{i}", "text")
        task_service.approve_submission(sub.id, "admin_1")

    # Safe reductions: 5,000 and 3,000
    edited1 = task_service.edit_task(task.id, changed_by="admin_1", total_reward_pool=5000)
    assert edited1.total_reward_pool == 5000
    assert edited1.remaining_reward_pool == 2000

    edited2 = task_service.edit_task(task.id, changed_by="admin_1", total_reward_pool=3000)
    assert edited2.total_reward_pool == 3000
    assert edited2.remaining_reward_pool == 0

    # Forbidden: Attempt to reduce below 3,000 OBX (e.g. 2,999)
    with pytest.raises(InvalidRewardPoolError) as exc_info:
        task_service.edit_task(task.id, changed_by="admin_1", total_reward_pool=2999)
    assert "Cannot reduce total_reward_pool" in exc_info.value.message


def test_4_change_reward_per_user_before_any_approvals(task_service):
    task = task_service.create_task(
        title="Early Rate Change",
        description="Desc",
        task_type=TaskType.RETWEET,
        target_url="https://x.com/post",
        reward_per_user=100,
        total_reward_pool=1000,
        created_by="admin_1",
    )

    edited = task_service.edit_task(task.id, changed_by="admin_1", reward_per_user=250)
    assert edited.reward_per_user == 250

    sub = task_service.submit_task(task.id, "u_early_1", "x1", "https://p1", "text")
    approved = task_service.approve_submission(sub.id, "admin_1")
    assert approved.reward_amount == 250


def test_5_6_7_change_reward_per_user_after_approvals_and_preserve_history(
    task_service, wallet_service, recon_service
):
    """Verifies:
    - User A approved at 100 OBX -> reward_amount = 100 OBX.
    - Admin changes reward_per_user to 250 OBX.
    - User B approved -> receives 250 OBX.
    - User A permanently remains recorded with reward_amount = 100 OBX.
    - User A wallet balance is 100, User B wallet balance is 250.
    """
    task = task_service.create_task(
        title="Dynamic Rate Task",
        description="Desc",
        task_type=TaskType.COMMENT,
        target_url="https://x.com/post",
        reward_per_user=100,
        total_reward_pool=1000,
        created_by="admin_1",
    )

    # User A approved at 100
    sub_a = task_service.submit_task(task.id, "user_A", "handle_a", "https://pa", "proof_a")
    app_a = task_service.approve_submission(sub_a.id, "admin_1")
    assert app_a.reward_amount == 100
    bal_a = wallet_service.get_balance("user_A")
    assert bal_a["available_balance"] == 100

    # Admin changes reward to 250
    task_service.edit_task(task.id, changed_by="admin_1", reward_per_user=250)

    # User B approved at 250
    sub_b = task_service.submit_task(task.id, "user_B", "handle_b", "https://pb", "proof_b")
    app_b = task_service.approve_submission(sub_b.id, "admin_1")
    assert app_b.reward_amount == 250
    bal_b = wallet_service.get_balance("user_B")
    assert bal_b["available_balance"] == 250

    # Verify User A historical record and balance remain untouched
    refreshed_sub_a = task_service.get_submission(sub_a.id)
    assert refreshed_sub_a.reward_amount == 100
    refreshed_bal_a = wallet_service.get_balance("user_A")
    assert refreshed_bal_a["available_balance"] == 100

    # Verify task balances
    refreshed_task = task_service.get_task(task.id)
    assert refreshed_task.distributed_reward == 350
    assert refreshed_task.remaining_reward_pool == 650

    # Verify ledger entries
    txs_a, _ = wallet_service.get_transactions("user_A")
    assert txs_a[0].amount == 100
    txs_b, _ = wallet_service.get_transactions("user_B")
    assert txs_b[0].amount == 250

    # Reconcile
    report = recon_service.reconcile_all()
    assert report.is_consistent is True


def test_8_and_9_prevent_negative_pool_and_overspending(task_service):
    task = task_service.create_task(
        title="Safety Task",
        description="Desc",
        task_type=TaskType.RETWEET,
        target_url="https://x.com/post",
        reward_per_user=200,
        total_reward_pool=500,
        created_by="admin_1",
    )

    # Submit 3 proofs while task is ACTIVE
    s1 = task_service.submit_task(task.id, "u_over_1", "x1", "https://p1", "txt")
    s2 = task_service.submit_task(task.id, "u_over_2", "x2", "https://p2", "txt")
    s3 = task_service.submit_task(task.id, "u_over_3", "x3", "https://p3", "txt")

    # Approve 2 users (400 distributed, 100 remaining)
    task_service.approve_submission(s1.id, "admin_1")
    task_service.approve_submission(s2.id, "admin_1")

    # 3rd submission approval: pool has 100 remaining, but reward_per_user is 200
    with pytest.raises(RewardPoolExhaustedError):
        task_service.approve_submission(s3.id, "admin_1")

    # Admin changes reward_per_user to 300 (still only 100 remaining)
    task_service.edit_task(task.id, changed_by="admin_1", reward_per_user=300)

    # 3rd submission approval still rejected
    with pytest.raises(RewardPoolExhaustedError):
        task_service.approve_submission(s3.id, "admin_1")


def test_10_audit_history_creation(task_service):
    task = task_service.create_task(
        title="Audited Task",
        description="Initial Description",
        task_type=TaskType.RETWEET,
        target_url="https://x.com/initial",
        reward_per_user=100,
        total_reward_pool=5000,
        created_by="creator_admin",
    )

    # Edit multiple attributes
    task_service.edit_task(
        task_id=task.id,
        changed_by="editor_admin_99",
        title="New Title",
        description="New Description",
        target_url="https://x.com/new",
        total_reward_pool=8000,
        reward_per_user=200,
        status=TaskStatus.PAUSED,
    )

    logs, total = task_service.get_task_audit_logs(task.id)
    assert total == 7  # 1 creation + 6 edited fields

    field_map = {l.field_name: l for l in logs}
    assert field_map["title"].old_value == "Audited Task"
    assert field_map["title"].new_value == "New Title"
    assert field_map["total_reward_pool"].old_value == "5000"
    assert field_map["total_reward_pool"].new_value == "8000"
    assert field_map["reward_per_user"].old_value == "100"
    assert field_map["reward_per_user"].new_value == "200"
    assert field_map["status"].old_value == "ACTIVE"
    assert field_map["status"].new_value == "PAUSED"


def test_12_concurrent_task_edits_and_approvals():
    """Verify that concurrent edits to total_reward_pool and concurrent approvals maintain strict database consistency."""
    pg_url = os.environ.get("TEST_DATABASE_URL")
    
    if pg_url and not pg_url.startswith("sqlite"):
        engine = create_engine(pg_url, poolclass=NullPool, echo=False)
        Base.metadata.create_all(bind=engine)
        SessionFactory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    else:
        tmpdir = tempfile.mkdtemp()
        db_path = os.path.join(tmpdir, "task_edit_concurrency.db")
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

    task_id = None
    submission_ids = []

    with SessionFactory() as s:
        srv = TaskService(s)
        task = srv.create_task(
            title=f"Concurrent Edit Task {uuid.uuid4().hex[:6]}",
            description="Concurrent test",
            task_type=TaskType.RETWEET,
            target_url="https://x.com/test",
            reward_per_user=100,
            total_reward_pool=500,
            created_by="admin_concurrent",
        )
        task_id = task.id

        for i in range(8):
            u_id = f"conc_edit_user_{i}_{uuid.uuid4().hex[:4]}"
            sub = srv.submit_task(
                task_id=task.id,
                discord_user_id=u_id,
                x_username=f"x_user_{i}",
                proof_url=f"https://x.com/u/{i}",
                proof_text="Proof text",
            )
            submission_ids.append(sub.id)

    def worker_approve(sub_id):
        with SessionFactory() as session:
            service = TaskService(session)
            try:
                service.approve_submission(sub_id, "admin_approver")
                return ("APPROVED", sub_id)
            except RewardPoolExhaustedError:
                return ("EXHAUSTED", sub_id)

    def worker_edit(new_pool):
        with SessionFactory() as session:
            service = TaskService(session)
            try:
                service.edit_task(task_id, changed_by="admin_resizer", total_reward_pool=new_pool)
                return ("EDIT_SUCCESS", new_pool)
            except Exception as e:
                return ("EDIT_FAILED", str(e))

    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as executor:
        futures = [executor.submit(worker_approve, sid) for sid in submission_ids]
        # Concurrently increase pool to 600
        futures.append(executor.submit(worker_edit, 600))

        results = [f.result() for f in concurrent.futures.as_completed(futures)]

    with SessionFactory() as s:
        srv = TaskService(s)
        final_task = srv.get_task(task_id)
        # Verify pool constraint: distributed <= total_reward_pool
        assert final_task.distributed_reward <= final_task.total_reward_pool
        assert final_task.remaining_reward_pool >= 0

        recon = ReconciliationService(s)
        report = recon.reconcile_all()
        assert report.is_consistent is True

    engine.dispose()
