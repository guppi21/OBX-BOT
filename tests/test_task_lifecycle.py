import pytest
import uuid
import os
import shutil
from pathlib import Path
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, AsyncMock, patch
import discord

from packages.database.models.task import Task
from packages.database.models.submission import TaskSubmission
from packages.database.models.submission_audit_log import SubmissionAuditLog
from packages.database.models.ledger import LedgerEntry
from packages.shared.enums import TaskStatus, SubmissionStatus
from packages.shared.exceptions import TaskExpiredError, UnauthorizedAdminError
from apps.obx_tasks.services.task_service import TaskService, validate_and_save_proof_image


def test_task_time_limits_and_auto_expiry(db_session):
    service = TaskService(db_session)
    now = datetime.now(timezone.utc)

    # 1. Create a task with a deadline in the past
    past_time = now - timedelta(hours=2)
    task_expired = service.create_task(
        title="Expired Bounty",
        description="Expired task instructions",
        task_type="RETWEET",
        target_url="https://x.com/past",
        reward_per_user=10,
        total_reward_pool=100,
        created_by="admin_1",
        ends_at=past_time,
    )

    # Submitting to an expired task raises TaskExpiredError
    with pytest.raises(TaskExpiredError):
        service.submit_task(
            task_id=task_expired.id,
            discord_user_id="user_123",
            x_username="user123",
            proof_url="https://x.com/proof",
            proof_text="Did it earlier",
        )

    # Task status is updated to EXPIRED
    db_session.refresh(task_expired)
    assert task_expired.status == TaskStatus.EXPIRED

    # 2. Test auto_expire_tasks service method
    task_auto = service.create_task(
        title="Auto Expire Task",
        description="Instructions",
        task_type="LIKE",
        target_url="https://x.com/auto",
        reward_per_user=20,
        total_reward_pool=200,
        created_by="admin_1",
        ends_at=now - timedelta(minutes=5),
        status=TaskStatus.ACTIVE,
    )

    expired_list = service.auto_expire_tasks()
    assert any(t.id == task_auto.id for t in expired_list)
    db_session.refresh(task_auto)
    assert task_auto.status == TaskStatus.EXPIRED


def test_optional_proof_image_validation_and_storage(tmp_path):
    sub_id = uuid.uuid4()
    test_dir = tmp_path / "proof_uploads"

    # 1. Valid image upload (.png)
    valid_bytes = b"\x89PNG\r\n\x1a\n" + b"dummy image data"
    path_png = validate_and_save_proof_image(
        file_bytes=valid_bytes,
        original_filename="screenshot.png",
        submission_id=sub_id,
        upload_dir=str(test_dir),
        max_size=1024 * 1024,
    )
    assert os.path.exists(path_png)
    assert path_png.endswith(".png")
    assert str(sub_id) in path_png

    # 2. Invalid file extension rejected
    with pytest.raises(ValueError, match="Unsupported image type"):
        validate_and_save_proof_image(
            file_bytes=b"malicious payload",
            original_filename="script.exe",
            submission_id=sub_id,
            upload_dir=str(test_dir),
        )

    # 3. Size limit exceeded rejected
    with pytest.raises(ValueError, match="exceeds maximum allowed size"):
        validate_and_save_proof_image(
            file_bytes=b"A" * 200,
            original_filename="huge.jpg",
            submission_id=sub_id,
            upload_dir=str(test_dir),
            max_size=100,
        )


def test_proof_image_retention_and_safe_cleanup(db_session, tmp_path):
    service = TaskService(db_session)
    upload_dir = tmp_path / "proof_retention"
    upload_dir.mkdir()

    task = service.create_task(
        title="Retention Task",
        description="Verify retention cleanup",
        task_type="RETWEET",
        target_url="https://x.com/ret",
        reward_per_user=15,
        total_reward_pool=150,
        created_by="admin_1",
    )

    # Create dummy proof file on disk
    proof_file = upload_dir / f"test_proof_{uuid.uuid4().hex[:8]}.png"
    proof_file.write_bytes(b"dummy proof image")

    sub = service.submit_task(
        task_id=task.id,
        discord_user_id="user_ret_1",
        x_username="user_ret",
        proof_url="https://x.com/proof_ret",
        proof_text="Proof with image",
        proof_screenshot_url=str(proof_file),
    )

    assert os.path.exists(proof_file)
    assert sub.proof_media_deleted is False

    # 1. Approval triggers media cleanup when retention is 0 (immediate)
    with patch("packages.shared.config.get_settings") as mock_settings:
        mock_settings.return_value.PROOF_RETENTION_MINUTES = 0
        service.approve_submission(sub.id, reviewer_discord_id="admin_reviewer")

    db_session.refresh(sub)
    assert sub.status == SubmissionStatus.APPROVED
    assert sub.proof_media_deleted is True
    assert sub.proof_media_deleted_at is not None
    assert not os.path.exists(proof_file)  # Image file safely removed!

    # 2. Check SubmissionAuditLog record
    logs, count = service.get_submission_audit_logs(submission_id=sub.id)
    assert count >= 1
    approve_log = [l for l in logs if l.action == "APPROVE"][0]
    assert approve_log.admin_id == "admin_reviewer"
    assert approve_log.discord_user_id == "user_ret_1"
    assert approve_log.reward_amount == 15

    # 3. Idempotent cleanup call
    cleaned = service.cleanup_proof_media(submission_id=sub.id)
    assert cleaned == 0  # Already deleted, zero impact


@pytest.mark.asyncio
async def test_admin_review_queue_view_and_navigation(db_session):
    from apps.obx_tasks.bot.dashboard_views import AdminReviewQueueView

    service = TaskService(db_session)
    task = service.create_task(
        title="Queue Task",
        description="Queue instructions",
        task_type="RETWEET",
        target_url="https://x.com/queue",
        reward_per_user=25,
        total_reward_pool=250,
        created_by="admin_q",
    )

    # Create 3 pending submissions
    s1 = service.submit_task(task.id, "user_q1", "handle1", "https://x.com/p1", "text 1")
    s2 = service.submit_task(task.id, "user_q2", "handle2", "https://x.com/p2", "text 2")
    s3 = service.submit_task(task.id, "user_q3", "handle3", "https://x.com/p3", "text 3")

    queue_view = AdminReviewQueueView(submissions=[s1, s2, s3], current_index=0)
    embed1 = queue_view.get_current_embed()
    assert "Submission 1 of 3" in embed1.description
    assert "handle1" in embed1.fields[0].value

    # Test Skip button
    mock_int = MagicMock(spec=discord.Interaction)
    mock_int.user = MagicMock(id="admin_reviewer")
    mock_int.response = AsyncMock()

    with patch("apps.obx_tasks.bot.dashboard_views.is_admin", return_value=True):
        await queue_view.skip_btn.callback(mock_int)

    assert queue_view.current_index == 1
    embed2 = queue_view.get_current_embed()
    assert "Submission 2 of 3" in embed2.description


@pytest.mark.asyncio
async def test_admin_review_queue_approve_and_anti_self_approval(db_session):
    from apps.obx_tasks.bot.dashboard_views import AdminReviewQueueView
    from contextlib import contextmanager

    @contextmanager
    def mock_scope():
        yield db_session

    service = TaskService(db_session)
    task = service.create_task(
        title="Self Approval Task",
        description="Test anti-self-approval",
        task_type="RETWEET",
        target_url="https://x.com/self",
        reward_per_user=50,
        total_reward_pool=500,
        created_by="admin_creator",
    )

    sub = service.submit_task(task.id, "admin_user", "admin_handle", "https://x.com/p_self", "text self")
    queue_view = AdminReviewQueueView(submissions=[sub], current_index=0)

    # 1. Admin attempts to approve their own submission -> Rejected
    mock_int_self = MagicMock(spec=discord.Interaction)
    mock_int_self.user = MagicMock(id="admin_user")
    mock_int_self.response = AsyncMock()

    with patch("apps.obx_tasks.bot.dashboard_views.is_admin", return_value=True):
        await queue_view.approve_btn.callback(mock_int_self)

    mock_int_self.response.send_message.assert_awaited_once()
    assert "Anti-Self-Approval Rule" in mock_int_self.response.send_message.call_args[0][0]

    # 2. Distinct admin approves -> Success
    mock_int_other = MagicMock(spec=discord.Interaction)
    mock_int_other.user = MagicMock(id="other_admin")
    mock_int_other.guild = None
    mock_int_other.response = AsyncMock()
    mock_int_other.edit_original_response = AsyncMock()

    with patch("apps.obx_tasks.bot.dashboard_views.is_admin", return_value=True), \
         patch("apps.obx_tasks.bot.dashboard_views.session_scope", mock_scope):
        await queue_view.approve_btn.callback(mock_int_other)

    db_session.refresh(sub)
    assert sub.status == SubmissionStatus.APPROVED
    assert len(queue_view.submissions) == 0  # Removed from queue!
    assert "Caught Up" in queue_view.get_current_embed().title
