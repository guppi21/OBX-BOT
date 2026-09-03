import pytest
from unittest.mock import MagicMock, AsyncMock, patch
import discord

from packages.shared.enums import SubmissionStatus, TaskStatus
from packages.database.session import session_scope
from apps.obx_tasks.services.task_service import TaskService
from apps.obx_core.services.wallet_service import WalletService
from apps.obx_tasks.bot.notification_service import send_approval_dm


def mock_session_scope_for(session):
    class DummyContext:
        def __enter__(self):
            return session
        def __exit__(self, exc_type, exc_val, exc_tb):
            pass
    return DummyContext()


@pytest.mark.asyncio
async def test_approval_dm_detached_instance_resilience(db_session):
    """Test that detached TaskSubmission objects do not raise DetachedInstanceError."""
    task_service = TaskService(db_session)
    task = task_service.create_task(
        title="Detached Safety Task",
        description="Verify detached safety",
        task_type="LIKE",
        target_url="https://x.com/safe/1",
        reward_per_user=15,
        total_reward_pool=150,
        created_by="admin_safe",
    )

    sub = task_service.submit_task(
        task_id=str(task.id),
        discord_user_id="888111222",
        x_username="SafeUser",
        proof_url="https://x.com/SafeUser/status/1",
        proof_text="Proof Link",
    )
    sub_id = str(sub.id)
    db_session.commit()

    # Approve submission and commit
    approved = task_service.approve_submission(
        submission_id=sub_id,
        reviewer_discord_id="admin_safe",
    )
    db_session.commit()

    # Expunge object from session so it is 100% detached
    db_session.expunge(approved)

    mock_user = MagicMock(spec=discord.User)
    mock_user.id = 888111222
    mock_user.name = "SafeUser"
    mock_user.send = AsyncMock()

    mock_bot = MagicMock(spec=discord.Client)
    mock_bot.get_user = MagicMock(return_value=mock_user)
    mock_bot.fetch_user = AsyncMock(return_value=mock_user)

    with patch("apps.obx_tasks.bot.notification_service.session_scope", lambda: mock_session_scope_for(db_session)):
        ok = await send_approval_dm(mock_bot, approved, new_balance=75)
        assert ok is True

    mock_user.send.assert_awaited_once()
    embed = mock_user.send.call_args[1]["embed"]
    assert embed.title == "🎉 CONGRATULATIONS!"
    assert "Your submission has been approved." in embed.description
    assert "+15 OBX" in embed.description
    assert "75 OBX" in embed.description
    assert "Keep raiding. ⚡" in embed.description
    # Ensure no internal task title or IDs are present
    assert "Detached Safety Task" not in embed.description
    assert sub_id not in embed.description


@pytest.mark.asyncio
async def test_approval_dm_api_fetch_fallback(db_session):
    """When user is not cached in memory, fetch_user must be called."""
    mock_user = MagicMock(spec=discord.User)
    mock_user.id = 999333444
    mock_user.name = "UncachedUser"
    mock_user.send = AsyncMock()

    mock_bot = MagicMock(spec=discord.Client)
    # get_user returns None (not in cache)
    mock_bot.get_user = MagicMock(return_value=None)
    mock_bot.guilds = []
    # fetch_user returns the user from Discord REST API
    mock_bot.fetch_user = AsyncMock(return_value=mock_user)

    with patch("apps.obx_tasks.bot.notification_service.session_scope", lambda: mock_session_scope_for(db_session)):
        ok = await send_approval_dm(
            mock_bot,
            discord_user_id="999333444",
            approved_amount=20,
            new_balance=100,
            submission_id="test_uncached",
        )
        assert ok is True

    mock_bot.fetch_user.assert_awaited_once_with(999333444)
    mock_user.send.assert_awaited_once()


@pytest.mark.asyncio
async def test_test_dm_action_does_not_modify_state(db_session):
    """Test DM action sends DM without writing idempotency records or modifying state."""
    mock_user = MagicMock(spec=discord.User)
    mock_user.id = 777666555
    mock_user.name = "Tester"
    mock_user.send = AsyncMock()

    mock_bot = MagicMock(spec=discord.Client)
    mock_bot.get_user = MagicMock(return_value=mock_user)

    with patch("apps.obx_tasks.bot.notification_service.session_scope", lambda: mock_session_scope_for(db_session)):
        # Call with is_test=True multiple times: each one must send successfully (no idempotency suppression)
        ok1, msg1 = await send_approval_dm(
            mock_bot,
            discord_user_id="777666555",
            approved_amount=10,
            new_balance=50,
            is_test=True,
            return_detail=True,
        )
        assert ok1 is True
        assert msg1 == "DM sent successfully"

        ok2, msg2 = await send_approval_dm(
            mock_bot,
            discord_user_id="777666555",
            approved_amount=10,
            new_balance=50,
            is_test=True,
            return_detail=True,
        )
        assert ok2 is True
        assert msg2 == "DM sent successfully"

    assert mock_user.send.await_count == 2


@pytest.mark.asyncio
async def test_approval_dm_forbidden_reports_clear_error(db_session):
    """When user has DMs closed, send_approval_dm returns clear diagnostic message."""
    mock_user = MagicMock(spec=discord.User)
    mock_user.id = 444555666
    mock_user.name = "ClosedDMs"
    mock_user.send = AsyncMock(side_effect=discord.Forbidden(MagicMock(), "Cannot send messages to this user"))

    mock_bot = MagicMock(spec=discord.Client)
    mock_bot.get_user = MagicMock(return_value=mock_user)

    with patch("apps.obx_tasks.bot.notification_service.session_scope", lambda: mock_session_scope_for(db_session)):
        ok, detail = await send_approval_dm(
            mock_bot,
            discord_user_id="444555666",
            approved_amount=25,
            new_balance=125,
            is_test=True,
            return_detail=True,
        )
        assert ok is False
        assert "DMs closed" in detail or "Forbidden" in detail
