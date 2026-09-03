import pytest
from unittest.mock import MagicMock, AsyncMock
import discord

from packages.database.models.task import Task
from packages.database.models.submission import TaskSubmission
from packages.shared.enums import TaskStatus, TaskType, SubmissionStatus
from apps.obx_tasks.bot.notification_service import (
    format_custom_template,
    build_reward_celebration_embed,
    send_reward_notification,
    _SENT_CELEBRATION_DMS,
)


def test_format_custom_template_whitelist():
    tpl = "Hey {display_name}! You finished {task_title} and earned {reward} OBX! Balance: {new_balance} (mention: {user})"
    res = format_custom_template(
        template=tpl,
        user_mention="<@123>",
        display_name="CryptoLegend",
        task_title="X Raid Utopia",
        reward=50,
        new_balance=1000,
    )
    assert res == "Hey CryptoLegend! You finished X Raid Utopia and earned 50 OBX! Balance: 1,000 (mention: <@123>)"


def test_build_reward_celebration_embed_default():
    task = MagicMock(spec=Task)
    task.title = "Utopia Social Raid"
    task.notification_type = "DEFAULT"
    task.custom_notification_template = None

    result = build_reward_celebration_embed(
        task=task,
        discord_user_id="998877",
        reward_amount=25,
        new_balance=500,
        display_name="Alice",
    )
    assert result is not None
    embed, view = result
    assert "MISSION COMPLETE" in embed.title
    assert "+25 OBX" in embed.description
    assert "500 OBX" in embed.description
    assert "Utopia Social Raid" in embed.description
    assert len(view.children) == 1
    assert view.children[0].label == "My Wallet"


def test_build_reward_celebration_embed_custom():
    task = MagicMock(spec=Task)
    task.title = "Utopia Social Raid"
    task.notification_type = "CUSTOM"
    task.custom_notification_template = "Salute {display_name}! {task_title} won you {reward} OBX."

    result = build_reward_celebration_embed(
        task=task,
        discord_user_id="998877",
        reward_amount=15,
        new_balance=250,
        display_name="Bob",
    )
    assert result is not None
    embed, view = result
    assert "Salute Bob! Utopia Social Raid won you 15 OBX." in embed.description


def test_build_reward_celebration_embed_disabled_none():
    task = MagicMock(spec=Task)
    task.notification_type = "NONE"

    result = build_reward_celebration_embed(
        task=task,
        discord_user_id="998877",
        reward_amount=15,
        new_balance=250,
    )
    assert result is None


@pytest.mark.asyncio
async def test_send_reward_notification_in_server_success():
    _SENT_CELEBRATION_DMS.clear()

    mock_bot = MagicMock(spec=discord.Client)
    mock_guild = MagicMock(spec=discord.Guild, id=1542965409383321660)
    mock_ch = MagicMock(spec=discord.TextChannel, id=999888, name="tasks")
    mock_msg = MagicMock(id=112233)
    mock_ch.send = AsyncMock(return_value=mock_msg)
    mock_guild.get_channel.return_value = mock_ch
    mock_bot.get_guild.return_value = mock_guild

    mock_task = MagicMock(spec=Task, id="t1", title="Raid Post", notification_type="DEFAULT", custom_notification_template=None)
    mock_sub = MagicMock(spec=TaskSubmission, id="s1", discord_user_id="123456", reward_amount=20)

    from unittest.mock import patch
    from contextlib import contextmanager

    mock_ch_service = MagicMock()
    mock_config = MagicMock(tasks_channel_id="999888")
    mock_ch_service.get_or_create_guild_config.return_value = mock_config
    mock_ch_service.get_published_message.return_value = None

    @contextmanager
    def dummy_scope():
        yield MagicMock()

    with patch("apps.obx_tasks.bot.notification_service.session_scope", dummy_scope), \
         patch("apps.obx_tasks.bot.notification_service.ChannelService", return_value=mock_ch_service):
        delivered = await send_reward_notification(
            bot=mock_bot,
            task=mock_task,
            submission=mock_sub,
            new_balance=100,
            guild=mock_guild,
        )
    assert delivered is True
    mock_ch.send.assert_called_once()
    assert "<@123456>" in mock_ch.send.call_args[1]["content"]


@pytest.mark.asyncio
async def test_send_reward_notification_failure_handled_safely():
    _SENT_CELEBRATION_DMS.clear()

    mock_bot = MagicMock(spec=discord.Client)
    mock_guild = MagicMock(spec=discord.Guild, id=1542965409383321660)
    mock_ch = MagicMock(spec=discord.TextChannel, id=999888, name="tasks")
    mock_ch.send = AsyncMock(side_effect=discord.Forbidden(MagicMock(), "Cannot send messages in channel"))
    mock_guild.get_channel.return_value = mock_ch
    mock_bot.get_guild.return_value = mock_guild

    mock_task = MagicMock(spec=Task, id="t2", title="Raid", notification_type="DEFAULT")
    mock_sub = MagicMock(spec=TaskSubmission, id="s2", discord_user_id="999", reward_amount=10)

    from unittest.mock import patch
    from contextlib import contextmanager

    mock_ch_service = MagicMock()
    mock_config = MagicMock(tasks_channel_id="999888")
    mock_ch_service.get_or_create_guild_config.return_value = mock_config
    mock_ch_service.get_published_message.return_value = None

    @contextmanager
    def dummy_scope():
        yield MagicMock()

    with patch("apps.obx_tasks.bot.notification_service.session_scope", dummy_scope), \
         patch("apps.obx_tasks.bot.notification_service.ChannelService", return_value=mock_ch_service):
        delivered = await send_reward_notification(
            bot=mock_bot,
            task=mock_task,
            submission=mock_sub,
            new_balance=50,
            guild=mock_guild,
        )
    assert delivered is False


@pytest.mark.asyncio
async def test_send_reward_notification_idempotent_no_duplicates():
    _SENT_CELEBRATION_DMS.clear()

    mock_bot = MagicMock(spec=discord.Client)
    mock_guild = MagicMock(spec=discord.Guild, id=1542965409383321660)
    mock_ch = MagicMock(spec=discord.TextChannel, id=999888, name="tasks")
    mock_msg = MagicMock(id=445566)
    mock_ch.send = AsyncMock(return_value=mock_msg)
    mock_guild.get_channel.return_value = mock_ch
    mock_bot.get_guild.return_value = mock_guild

    mock_task = MagicMock(spec=Task, id="t3", title="Raid", notification_type="DEFAULT")
    mock_sub = MagicMock(spec=TaskSubmission, id="s3", discord_user_id="888", reward_amount=10)

    from unittest.mock import patch
    from contextlib import contextmanager

    mock_ch_service = MagicMock()
    mock_config = MagicMock(tasks_channel_id="999888")
    mock_ch_service.get_or_create_guild_config.return_value = mock_config
    mock_ch_service.get_published_message.return_value = None

    @contextmanager
    def dummy_scope():
        yield MagicMock()

    with patch("apps.obx_tasks.bot.notification_service.session_scope", dummy_scope), \
         patch("apps.obx_tasks.bot.notification_service.ChannelService", return_value=mock_ch_service):
        # First call
        d1 = await send_reward_notification(bot=mock_bot, task=mock_task, submission=mock_sub, new_balance=100, guild=mock_guild)
        assert d1 is True
        assert mock_ch.send.call_count == 1

        # Second call (retry / duplicate)
        d2 = await send_reward_notification(bot=mock_bot, task=mock_task, submission=mock_sub, new_balance=100, guild=mock_guild)
        assert d2 is False
        assert mock_ch.send.call_count == 1  # No duplicate send
