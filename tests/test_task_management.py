import pytest
import uuid
from unittest.mock import MagicMock, AsyncMock, patch
import discord

from packages.shared.enums import TaskStatus, TaskType, SubmissionStatus
from packages.shared.exceptions import TaskNotFoundError, TaskNotActiveError, InvalidRewardPoolError
from apps.obx_tasks.services.task_service import TaskService
from apps.obx_tasks.services.channel_service import ChannelService
from apps.obx_tasks.bot.task_management_views import AdminTaskManageBrowserView, handle_admin_manage_tasks


def mock_session_scope_for(session):
    from contextlib import contextmanager
    @contextmanager
    def _scope():
        yield session
    return _scope()


def test_admin_edit_task_content_and_audit(db_session):
    service = TaskService(db_session)
    task = service.create_task(
        title="Original Task",
        description="Original instructions",
        task_type=TaskType.LIKE,
        target_url="https://x.com/original/1",
        reward_per_user=10,
        total_reward_pool=100,
        created_by="admin_test",
    )

    # Edit task content
    updated = service.edit_task(
        task_id=task.id,
        changed_by="admin_editor",
        title="Updated Task Title",
        description="Updated instructions for members",
        target_url="https://x.com/updated/2",
        task_type=TaskType.RETWEET,
        proof_required=False,
        allow_image_proof=False,
        notification_type="CUSTOM",
        custom_notification_template="Congrats {display_name} on {task_title}!",
    )

    assert updated.title == "Updated Task Title"
    assert updated.description == "Updated instructions for members"
    assert updated.target_url == "https://x.com/updated/2"
    assert updated.task_type == TaskType.RETWEET
    assert updated.proof_required is False
    assert updated.allow_image_proof is False
    assert updated.notification_type == "CUSTOM"
    assert updated.custom_notification_template == "Congrats {display_name} on {task_title}!"

    # Verify audit logs created
    logs, total = service.get_task_audit_logs(task.id)
    assert total >= 7
    field_names = [l.field_name for l in logs]
    assert "title" in field_names
    assert "description" in field_names
    assert "target_url" in field_names
    assert "task_type" in field_names
    assert "proof_required" in field_names
    assert "notification_type" in field_names


def test_admin_edit_task_rejects_invalid_pool_reduction(db_session):
    service = TaskService(db_session)
    task = service.create_task(
        title="Pool Reduction Test",
        description="Testing pool limits",
        task_type=TaskType.LIKE,
        target_url="https://x.com/pool/1",
        reward_per_user=10,
        total_reward_pool=100,
        created_by="admin_test",
    )
    # Simulate a distributed reward
    task.distributed_reward = 50
    db_session.commit()

    # Attempt to reduce pool below distributed amount -> must fail
    with pytest.raises(InvalidRewardPoolError) as exc_info:
        service.edit_task(
            task_id=task.id,
            changed_by="admin_test",
            total_reward_pool=40,
        )
    assert "below already distributed amount" in str(exc_info.value)


@pytest.mark.asyncio
async def test_task_edit_updates_public_announcement_in_place(db_session):
    from apps.obx_tasks.bot.announcement_service import announce_task

    ch_service = ChannelService(db_session)
    ch_service.update_guild_channel("guild_edit_ann", "tasks", "888", "admin")

    service = TaskService(db_session)
    task = service.create_task(
        title="Card In Place Test",
        description="Original card",
        task_type=TaskType.LIKE,
        target_url="https://x.com/card/1",
        reward_per_user=10,
        total_reward_pool=100,
        created_by="admin_test",
    )
    ch_service.record_published_message("guild_edit_ann", "TASK_ANNOUNCEMENT", "888", "554433", source_id=str(task.id))

    mock_guild = MagicMock(spec=discord.Guild, id="guild_edit_ann")
    mock_msg = MagicMock(id=554433)
    mock_msg.edit = AsyncMock()
    mock_ch = MagicMock(spec=discord.TextChannel, id=888)
    mock_ch.permissions_for.return_value = MagicMock(view_channel=True, send_messages=True, embed_links=True)
    mock_ch.fetch_message = AsyncMock(return_value=mock_msg)
    mock_guild.get_channel.return_value = mock_ch

    mock_bot = MagicMock(spec=discord.Client)

    # Edit task
    service.edit_task(task.id, changed_by="admin", title="Brand New Title", reward_per_user=15)

    with patch("apps.obx_tasks.bot.announcement_service.session_scope", lambda: mock_session_scope_for(db_session)):
        ok, msg = await announce_task(task, mock_guild, mock_bot)

    assert ok is True
    # Message edited in place, no duplicate send
    mock_msg.edit.assert_called_once()
    mock_ch.send.assert_not_called()
    edit_embed = mock_msg.edit.call_args[1]["embed"]
    assert "15 OBX" in edit_embed.description


def test_task_cancellation_blocks_new_submissions(db_session):
    service = TaskService(db_session)
    task = service.create_task(
        title="Cancel Submission Test",
        description="Will be cancelled",
        task_type=TaskType.LIKE,
        target_url="https://x.com/cancel/1",
        reward_per_user=10,
        total_reward_pool=100,
        created_by="admin_test",
    )

    # Cancel task
    cancelled = service.cancel_task(
        task_id=task.id,
        cancelled_by="admin_1",
        reason="Campaign concluded early",
    )
    assert cancelled.status == TaskStatus.CANCELLED
    assert cancelled.cancellation_reason == "Campaign concluded early"

    # Submissions on cancelled task must be rejected immediately
    with pytest.raises(TaskNotActiveError):
        service.submit_task(
            task_id=task.id,
            discord_user_id="user_cancel_1",
            x_username="userx",
            proof_url="https://x.com/userx/status/123",
            proof_text="Did it",
        )


def test_task_cancellation_handles_pending_disposition(db_session):
    service = TaskService(db_session)
    task = service.create_task(
        title="Pending Disposition Test",
        description="Has pending subs",
        task_type=TaskType.LIKE,
        target_url="https://x.com/pending/1",
        reward_per_user=10,
        total_reward_pool=100,
        created_by="admin_test",
    )

    # User submits
    sub1 = service.submit_task(
        task_id=task.id,
        discord_user_id="user_p1",
        x_username="user1",
        proof_url="https://x.com/p1",
        proof_text="proof 1",
    )
    sub2 = service.submit_task(
        task_id=task.id,
        discord_user_id="user_p2",
        x_username="user2",
        proof_url="https://x.com/p2",
        proof_text="proof 2",
    )
    assert sub1.status == SubmissionStatus.PENDING
    assert sub2.status == SubmissionStatus.PENDING

    # Cancel with REJECT disposition
    cancelled = service.cancel_task(
        task_id=task.id,
        cancelled_by="admin_reviewer",
        reason="Quality issues",
        pending_action="REJECT",
    )
    assert cancelled.status == TaskStatus.CANCELLED

    # Pending submissions should now be REJECTED
    s1 = service.get_submission(sub1.id)
    s2 = service.get_submission(sub2.id)
    assert s1.status == SubmissionStatus.REJECTED
    assert s2.status == SubmissionStatus.REJECTED
    assert "Quality issues" in (s1.rejection_reason or "")


def test_safe_delete_blocks_task_with_history(db_session):
    service = TaskService(db_session)
    task = service.create_task(
        title="Unsafe Delete Test",
        description="Has submissions",
        task_type=TaskType.LIKE,
        target_url="https://x.com/unsafe/1",
        reward_per_user=10,
        total_reward_pool=100,
        created_by="admin_test",
    )
    # Add a submission
    service.submit_task(
        task_id=task.id,
        discord_user_id="user_u1",
        x_username="user1",
        proof_url="https://x.com/u1",
        proof_text="proof",
    )

    # Deletion must be blocked
    with pytest.raises(ValueError) as exc_info:
        service.safe_delete_task(task.id, deleted_by="admin_1")
    assert "cannot be deleted because it has" in str(exc_info.value)


def test_safe_delete_succeeds_for_unused_task(db_session):
    service = TaskService(db_session)
    ch_service = ChannelService(db_session)

    task = service.create_task(
        title="Unused Task",
        description="Zero activity",
        task_type=TaskType.LIKE,
        target_url="https://x.com/unused/1",
        reward_per_user=10,
        total_reward_pool=100,
        created_by="admin_test",
    )
    task_id_str = str(task.id)
    ch_service.record_published_message("guild_del", "TASK_ANNOUNCEMENT", "123", "999", source_id=task_id_str)

    # Safe delete unused task
    ok = service.safe_delete_task(task.id, deleted_by="admin_cleaner")
    assert ok is True

    # Task should no longer exist
    with pytest.raises(TaskNotFoundError):
        service.get_task(task_id_str)

    # PublishedMessage tracking should be cleared
    pub = ch_service.get_published_message("guild_del", "TASK_ANNOUNCEMENT", source_id=task_id_str)
    assert pub is None


@pytest.mark.asyncio
async def test_non_admin_blocked_from_manage_tasks():
    mock_interaction = MagicMock(spec=discord.Interaction)
    mock_interaction.user = MagicMock(id=111222)
    mock_interaction.guild = MagicMock(id=999888)
    mock_interaction.response = AsyncMock()

    with patch("apps.obx_tasks.bot.task_management_views.is_admin", return_value=False):
        await handle_admin_manage_tasks(mock_interaction)

    mock_interaction.response.send_message.assert_awaited_once()
    msg = mock_interaction.response.send_message.call_args[0][0]
    assert "Permission Denied" in msg
