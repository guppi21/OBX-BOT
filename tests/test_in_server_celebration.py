import pytest
from unittest.mock import AsyncMock, MagicMock, patch
import discord
from apps.obx_tasks.bot.notification_service import (
    send_reward_notification,
    DismissRewardCelebrationView,
    build_in_server_celebration,
    _SENT_CELEBRATION_DMS,
)
from apps.obx_tasks.services.channel_service import ChannelService
from packages.database.models.task import Task
from packages.database.models.submission import TaskSubmission
from contextlib import contextmanager

@contextmanager
def mock_session_scope_for(db_session):
    yield db_session

@pytest.fixture(autouse=True)
def clear_celebration_cache():
    _SENT_CELEBRATION_DMS.clear()
    yield
    _SENT_CELEBRATION_DMS.clear()


def test_build_in_server_celebration_format():
    mock_task = MagicMock(spec=Task, title="Follow Monad on X", reward_per_user=50)
    mock_sub = MagicMock(spec=TaskSubmission, id="sub-123", discord_user_id="9876543210", reward_amount=50)

    content, embed, view = build_in_server_celebration(mock_task, mock_sub, 50)

    assert "<@9876543210>" in content
    assert "MISSION COMPLETE" in embed.title
    assert "50 OBX" in embed.description
    assert "<@9876543210>" in embed.description

    # Verify action buttons: View Wallet and Dismiss
    assert len(view.children) == 2
    wallet_btn = view.children[0]
    assert "View Wallet" in wallet_btn.label
    assert wallet_btn.custom_id == "obx:user:wallet"
    dismiss_btn = view.children[1]
    assert "Dismiss" in dismiss_btn.label
    assert dismiss_btn.custom_id == "obx:celebrate:dismiss:sub-123:9876543210"


@pytest.mark.asyncio
async def test_dismiss_view_auth_check():
    view = DismissRewardCelebrationView(submission_id="sub-123", user_id="9876543210")

    # 1. Non-rewarded user clicks dismiss -> denied ephemerally
    wrong_user_interaction = MagicMock(spec=discord.Interaction)
    wrong_user_interaction.user.id = "1111111111"
    wrong_user_interaction.response.send_message = AsyncMock()

    allowed = await view.interaction_check(wrong_user_interaction)
    assert allowed is False
    wrong_user_interaction.response.send_message.assert_called_once()
    args, kwargs = wrong_user_interaction.response.send_message.call_args
    assert "Only the rewarded member can dismiss" in args[0]
    assert kwargs.get("ephemeral") is True

    # 2. Rewarded user clicks dismiss -> message deleted
    right_user_interaction = MagicMock(spec=discord.Interaction)
    right_user_interaction.user.id = "9876543210"
    mock_msg = MagicMock(spec=discord.Message)
    mock_msg.delete = AsyncMock()
    right_user_interaction.message = mock_msg

    allowed_right = await view.interaction_check(right_user_interaction)
    assert allowed_right is False  # handled and consumed
    mock_msg.delete.assert_called_once()


@pytest.mark.asyncio
async def test_send_reward_notification_posts_to_tasks_channel_not_dm(db_session):
    ch_service = ChannelService(db_session)
    ch_service.update_guild_channel("guild_celeb_test", "tasks", "555123", "admin")

    mock_task = MagicMock(spec=Task, id="task-abc", title="Retweet Announcement", notification_type="DEFAULT", reward_per_user=15)
    mock_sub = MagicMock(spec=TaskSubmission, id="sub-abc", discord_user_id="777888999", reward_amount=15)

    mock_guild = MagicMock(spec=discord.Guild, id="guild_celeb_test")
    mock_ch = MagicMock(spec=discord.TextChannel, id=555123, name="1-tasks")
    mock_msg = MagicMock(id=999111)
    mock_ch.send = AsyncMock(return_value=mock_msg)
    mock_guild.get_channel.return_value = mock_ch

    mock_bot = MagicMock(spec=discord.Client)
    mock_bot.get_guild.return_value = mock_guild

    with patch("apps.obx_tasks.bot.notification_service.session_scope", lambda: mock_session_scope_for(db_session)):
        ok = await send_reward_notification(
            bot=mock_bot,
            task=mock_task,
            submission=mock_sub,
            new_balance=150,
            guild=mock_guild,
        )

    assert ok is True
    # Verify sent to Tasks channel
    mock_ch.send.assert_called_once()
    call_kw = mock_ch.send.call_args[1]
    assert "<@777888999>" in call_kw["content"]
    assert "MISSION COMPLETE" in call_kw["embed"].title
    assert "15 OBX" in call_kw["embed"].description

    # Verify no DM was sent
    mock_bot.get_user.assert_not_called()
    mock_bot.fetch_user.assert_not_called()

    # Verify idempotency via PublishedMessage
    pub = ch_service.get_published_message("guild_celeb_test", "REWARD_CELEBRATION", source_id="sub-abc")
    assert pub is not None
    assert pub.message_id == "999111"

    # Second call should skip duplicate
    with patch("apps.obx_tasks.bot.notification_service.session_scope", lambda: mock_session_scope_for(db_session)):
        ok_dup = await send_reward_notification(
            bot=mock_bot,
            task=mock_task,
            submission=mock_sub,
            new_balance=150,
            guild=mock_guild,
        )
    assert ok_dup is False
    assert mock_ch.send.call_count == 1  # No duplicate send
