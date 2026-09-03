import pytest
from packages.database.models.submission import TaskSubmission
from packages.shared.enums import TaskType, SubmissionStatus, TransactionType
from packages.shared.exceptions import (
    RewardPoolExhaustedError,
    InvalidSubmissionStatusError,
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


def test_approve_submission_success_and_obx_credit(task_service, wallet_service, recon_service):
    task = task_service.create_task(
        title="RT Main Post",
        description="Retweet the main announcement",
        task_type=TaskType.RETWEET,
        target_url="https://x.com/obx/status/123",
        reward_per_user=150,
        total_reward_pool=1500,
        created_by="admin_1",
    )

    sub = task_service.submit_task(
        task_id=task.id,
        discord_user_id="user_reward_1",
        x_username="user1_x",
        proof_url="https://x.com/user1/status/456",
        proof_text="Here is my retweet",
    )

    # Approve
    approved_sub = task_service.approve_submission(
        submission_id=sub.id,
        reviewer_discord_id="admin_reviewer_1",
    )

    assert approved_sub.status == SubmissionStatus.APPROVED
    assert approved_sub.reviewed_by == "admin_reviewer_1"
    assert approved_sub.reward_amount == 150
    assert approved_sub.obx_transaction_id is not None

    # Check task pool
    refreshed_task = task_service.get_task(task.id)
    assert refreshed_task.distributed_reward == 150
    assert refreshed_task.remaining_reward_pool == 1350

    # Verify wallet balance
    bal = wallet_service.get_balance("user_reward_1")
    assert bal["available_balance"] == 150
    assert bal["locked_balance"] == 0
    assert bal["total_balance"] == 150

    # Verify ledger entry
    entries, total = wallet_service.get_transactions("user_reward_1")
    assert total == 1
    assert entries[0].amount == 150
    assert entries[0].transaction_type == TransactionType.CREDIT
    assert entries[0].reference_type == "task_reward"
    assert entries[0].reference_id == str(task.id)
    assert entries[0].id == approved_sub.obx_transaction_id

    # Verify reconciliation
    report = recon_service.reconcile_all()
    assert report.is_consistent is True


def test_reject_submission(task_service, wallet_service, recon_service):
    task = task_service.create_task("T1", "D1", TaskType.COMMENT, "https://x.com/1", 100, 1000, "adm")
    sub = task_service.submit_task(task.id, "user_rejected_1", "bad_x", "https://fake.url", "fake")

    rejected_sub = task_service.reject_submission(
        submission_id=sub.id,
        reviewer_discord_id="admin_mod",
        rejection_reason="Proof URL is broken",
    )

    assert rejected_sub.status == SubmissionStatus.REJECTED
    assert rejected_sub.rejection_reason == "Proof URL is broken"
    assert rejected_sub.reviewed_by == "admin_mod"
    assert rejected_sub.reward_amount is None
    assert rejected_sub.obx_transaction_id is None

    # Verify task pool did NOT change
    refreshed_task = task_service.get_task(task.id)
    assert refreshed_task.distributed_reward == 0
    assert refreshed_task.remaining_reward_pool == 1000

    # Verify user received 0 OBX
    with pytest.raises(Exception):
        wallet_service.get_balance("user_rejected_1")


def test_approval_idempotency_repeated_calls(task_service, wallet_service):
    task = task_service.create_task("T1", "D1", TaskType.RETWEET, "https://x.com/1", 200, 1000, "adm")
    sub = task_service.submit_task(task.id, "user_idem_sub", "idem_x", "https://proof", "text")

    # First approval
    sub1 = task_service.approve_submission(sub.id, "admin_1")
    assert sub1.status == SubmissionStatus.APPROVED

    # Second approval (re-sent)
    sub2 = task_service.approve_submission(sub.id, "admin_1")
    assert sub2.status == SubmissionStatus.APPROVED
    assert sub1.obx_transaction_id == sub2.obx_transaction_id

    # Balance credited exactly once
    bal = wallet_service.get_balance("user_idem_sub")
    assert bal["available_balance"] == 200

    # Task pool deducted only once
    refreshed_task = task_service.get_task(task.id)
    assert refreshed_task.distributed_reward == 200


def test_cannot_approve_or_reject_non_pending_submission(task_service):
    task = task_service.create_task("T1", "D1", TaskType.RETWEET, "https://x.com/1", 100, 1000, "adm")
    sub = task_service.submit_task(task.id, "user_state_test", "x_state", "https://proof", "text")

    task_service.reject_submission(sub.id, "admin_1", "Rejected reason")

    with pytest.raises(InvalidSubmissionStatusError):
        task_service.approve_submission(sub.id, "admin_1")


def test_pool_exhaustion_exact_ten_users_and_eleventh_fails(task_service, wallet_service, recon_service):
    """Scenario:
    Task total_reward_pool = 1000 OBX, reward_per_user = 100 OBX.
    10 submissions approved -> distributed = 1000, remaining = 0.
    11th approval fails with RewardPoolExhaustedError.
    Reconciliation passes for all 10 users.
    """
    total_pool = 1000
    reward_per_user = 100
    num_users = 10

    task = task_service.create_task(
        title="Mega Launch Retweet",
        description="Help us reach 10,000 retweets",
        task_type=TaskType.RETWEET,
        target_url="https://x.com/obx/mega-launch",
        reward_per_user=reward_per_user,
        total_reward_pool=total_pool,
        created_by="founder_admin",
    )

    submissions = []
    for i in range(num_users):
        u_id = f"launch_user_{i+1}"
        s = task_service.submit_task(
            task_id=task.id,
            discord_user_id=u_id,
            x_username=f"x_handle_{i+1}",
            proof_url=f"https://x.com/handle_{i+1}/status/{1000+i}",
            proof_text=f"Retweeted post #{i+1}",
        )
        submissions.append(s)

    # Approve all 10 submissions
    for i, s in enumerate(submissions):
        approved = task_service.approve_submission(s.id, "reviewer_admin")
        assert approved.status == SubmissionStatus.APPROVED
        assert approved.reward_amount == 100

    refreshed_task = task_service.get_task(task.id)
    assert refreshed_task.distributed_reward == 1000
    assert refreshed_task.remaining_reward_pool == 0
    assert refreshed_task.status == "COMPLETED"

    # Verify all 10 users received exactly 100 OBX
    for i in range(num_users):
        u_id = f"launch_user_{i+1}"
        bal = wallet_service.get_balance(u_id)
        assert bal["available_balance"] == 100
        assert bal["locked_balance"] == 0

    # Create an 11th submission before pool was exhausted or manually
    # Even if submission was pending, approval must fail with RewardPoolExhaustedError
    s11 = TaskSubmission(
        task_id=task.id,
        discord_user_id="launch_user_11",
        x_username="x_handle_11",
        proof_url="https://x.com/11",
        proof_text="11th proof",
        status=SubmissionStatus.PENDING,
    )
    task_service.session.add(s11)
    task_service.session.commit()
    task_service.session.refresh(s11)

    with pytest.raises(RewardPoolExhaustedError) as exc_info:
        task_service.approve_submission(s11.id, "reviewer_admin")

    assert exc_info.value.remaining == 0
    assert exc_info.value.required == 100

    # Reconcile system
    report = recon_service.reconcile_all()
    assert report.is_consistent is True
    assert report.total_users_checked == 10
    assert report.mismatched_users_count == 0
