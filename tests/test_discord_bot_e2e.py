import pytest
import uuid
from packages.shared.enums import TaskStatus, TaskType, SubmissionStatus
from packages.shared.exceptions import (
    TaskNotActiveError,
    DuplicateSubmissionError,
    UnauthorizedAdminError,
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


def test_22_step_discord_bot_e2e_lifecycle(task_service, wallet_service, recon_service):
    """Executes and verifies the complete 22-step Discord bot integration lifecycle."""

    # 1. Admin creates task (Pool = 1000 OBX, Reward = 100 OBX)
    admin_id = "discord_admin_123"
    task = task_service.create_task(
        title="Retweet Ecosystem Teaser",
        description="Retweet our teaser post and comment with #OBX",
        task_type=TaskType.RETWEET,
        target_url="https://x.com/obx/status/1001",
        reward_per_user=100,
        total_reward_pool=1000,
        created_by=admin_id,
        status=TaskStatus.ACTIVE,
    )
    assert task.id is not None
    assert task.status == TaskStatus.ACTIVE

    # 2. User A views /tasks
    active_tasks, count = task_service.list_tasks(status=TaskStatus.ACTIVE)
    assert count >= 1
    assert any(t.id == task.id for t in active_tasks)

    # 3. User A views /task
    detailed_task = task_service.get_task(task.id)
    assert detailed_task.title == "Retweet Ecosystem Teaser"
    assert detailed_task.remaining_reward_pool == 1000

    # 4. User A submits proof
    user_a = "discord_user_A_456"
    sub_a = task_service.submit_task(
        task_id=task.id,
        discord_user_id=user_a,
        x_username="alice_obx",
        proof_url="https://x.com/alice_obx/status/2001",
        proof_text="Here is my retweet!",
    )

    # 5. Verify submission is PENDING
    assert sub_a.status == SubmissionStatus.PENDING
    assert sub_a.reward_amount is None

    # 6. Admin reviews pending submissions
    pending_subs, p_count = task_service.list_submissions(task_id=task.id, status=SubmissionStatus.PENDING)
    assert p_count >= 1
    assert any(s.id == sub_a.id for s in pending_subs)

    # 7. Admin approves submission
    approved_a = task_service.approve_submission(
        submission_id=sub_a.id,
        reviewer_discord_id=admin_id,
    )
    assert approved_a.status == SubmissionStatus.APPROVED
    assert approved_a.reward_amount == 100

    # 8. Verify User A wallet receives exactly 100 OBX
    bal_a = wallet_service.get_balance(user_a)
    assert bal_a["available_balance"] == 100
    assert bal_a["locked_balance"] == 0

    # 9. Verify /my-submissions shows APPROVED
    user_a_subs, _ = task_service.list_submissions(discord_user_id=user_a)
    assert user_a_subs[0].status == SubmissionStatus.APPROVED
    assert user_a_subs[0].reward_amount == 100

    # 10. Verify duplicate submission is rejected
    with pytest.raises(DuplicateSubmissionError):
        task_service.submit_task(
            task_id=task.id,
            discord_user_id=user_a,
            x_username="alice_obx",
            proof_url="https://x.com/alice_obx/status/2002",
            proof_text="Duplicate try",
        )

    # 11. Verify duplicate approval does not duplicate OBX
    re_approved = task_service.approve_submission(sub_a.id, reviewer_discord_id=admin_id)
    assert re_approved.status == SubmissionStatus.APPROVED
    assert wallet_service.get_balance(user_a)["available_balance"] == 100

    # 12. Verify self-approval rejection (Admin submits proof -> cannot approve own proof)
    sub_admin = task_service.submit_task(
        task_id=task.id,
        discord_user_id=admin_id,
        x_username="admin_x",
        proof_url="https://x.com/admin_x/status/3001",
        proof_text="Admin test",
    )
    with pytest.raises(UnauthorizedAdminError) as exc_self:
        task_service.approve_submission(sub_admin.id, reviewer_discord_id=admin_id)
    assert "cannot approve their own" in exc_self.value.message

    # 13. Admin pauses task
    task_service.edit_task(task.id, changed_by=admin_id, status=TaskStatus.PAUSED)
    assert task_service.get_task(task.id).status == TaskStatus.PAUSED

    # 14. Verify new submission while paused is rejected
    user_b = "discord_user_B_789"
    with pytest.raises(TaskNotActiveError):
        task_service.submit_task(
            task_id=task.id,
            discord_user_id=user_b,
            x_username="bob_obx",
            proof_url="https://x.com/bob_obx/status/4001",
            proof_text="Submission during pause",
        )

    # 15. Admin resumes task
    task_service.edit_task(task.id, changed_by=admin_id, status=TaskStatus.ACTIVE)
    assert task_service.get_task(task.id).status == TaskStatus.ACTIVE

    # 16. Verify new submission works after resume
    sub_b = task_service.submit_task(
        task_id=task.id,
        discord_user_id=user_b,
        x_username="bob_obx",
        proof_url="https://x.com/bob_obx/status/4001",
        proof_text="Submission after resume",
    )
    assert sub_b.status == SubmissionStatus.PENDING

    # 17. Verify task reactivation fix: Mark completed -> expand pool -> remains COMPLETED
    task_service.edit_task(task.id, changed_by=admin_id, status=TaskStatus.COMPLETED)
    assert task_service.get_task(task.id).status == TaskStatus.COMPLETED

    # Admin increases pool from 1000 to 2500 on COMPLETED task
    expanded_task = task_service.edit_task(task.id, changed_by=admin_id, total_reward_pool=2500)
    assert expanded_task.total_reward_pool == 2500
    assert expanded_task.status == TaskStatus.COMPLETED  # Strict non-reactivation rule verified!

    # 18. Explicitly reactivate to ACTIVE
    reactivated_task = task_service.edit_task(task.id, changed_by=admin_id, status=TaskStatus.ACTIVE)
    assert reactivated_task.status == TaskStatus.ACTIVE

    # 19. Verify audit history contains full log
    logs, log_count = task_service.get_task_audit_logs(task.id)
    assert log_count >= 5

    # 20. Run system-wide reconciliation
    report = recon_service.reconcile_all()
    assert report.is_consistent is True
