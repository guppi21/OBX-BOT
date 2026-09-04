import pytest
from unittest.mock import MagicMock, AsyncMock, patch
import discord

from apps.obx_core.services.wallet_service import WalletService
from apps.obx_tasks.services.task_service import TaskService
from apps.obx_tasks.services.leaderboard_service import (
    LeaderboardService,
    LeaderboardCategory,
)
from packages.database.models.wallet import Wallet
from packages.database.models.submission import TaskSubmission
from packages.database.models.ledger import LedgerEntry


def test_clear_leaderboard_data_resets_balances_and_history(db_session):
    ws = WalletService(db_session)
    ts = TaskService(db_session)
    lb_service = LeaderboardService(db_session)

    # 1. Setup users with balances
    u1, _, _ = ws.get_or_create_user("user_clear_1")
    u2, _, _ = ws.get_or_create_user("user_clear_2")
    ws.credit(discord_user_id="user_clear_1", amount=500, reference_type="test", idempotency_key="clear_k1")
    ws.credit(discord_user_id="user_clear_2", amount=1000, reference_type="test", idempotency_key="clear_k2")

    # 2. Setup task with submissions
    task = ts.create_task(
        title="Test Task For Clear",
        description="Description",
        task_type="LIKE",
        target_url="https://x.com/obx/status/123",
        reward_per_user=100,
        total_reward_pool=500,
        created_by="admin_test",
    )
    sub = ts.submit_task(
        task_id=task.id,
        discord_user_id="user_clear_1",
        x_username="user1_x",
        proof_url="https://x.com/user1_x/status/987654321",
        proof_text="done",
    )
    ts.approve_submission(submission_id=sub.id, reviewer_discord_id="admin_test")

    # Verify leaderboard has data
    entries_before, total_before = lb_service.get_leaderboard(category=LeaderboardCategory.TOTAL_OBX)
    assert total_before >= 2
    assert len(entries_before) >= 2

    # 3. Call clear_leaderboard_data
    stats = lb_service.clear_leaderboard_data()
    assert stats["wallets_reset"] >= 2
    assert stats["submissions_cleared"] >= 1
    assert stats["tasks_reset"] >= 1

    # 4. Verify leaderboard is now completely empty
    entries_after, total_after = lb_service.get_leaderboard(category=LeaderboardCategory.TOTAL_OBX)
    assert total_after == 0
    assert len(entries_after) == 0

    # Verify task earnings leaderboard is also empty
    entries_earn, total_earn = lb_service.get_leaderboard(category=LeaderboardCategory.TASK_EARNINGS)
    assert total_earn == 0
    assert len(entries_earn) == 0

    # Verify wallet balances in DB are 0
    w1 = db_session.query(Wallet).filter_by(user_id=u1.id).first()
    w2 = db_session.query(Wallet).filter_by(user_id=u2.id).first()
    assert w1.available_balance == 0
    assert w1.locked_balance == 0
    assert w2.available_balance == 0
    assert w2.locked_balance == 0

    # Verify task pool counters reset
    db_session.refresh(task)
    assert task.distributed_reward == 0
    assert task.approved_count == 0


@pytest.mark.asyncio
async def test_admin_clear_leaderboard_slash_command_flow(db_session):
    from apps.obx_tasks.bot.client import create_discord_bot

    bot = create_discord_bot()
    clear_cmd = None
    for cmd in bot.tree.get_commands():
        if cmd.name == "admin-clear-leaderboard":
            clear_cmd = cmd
            break

    assert clear_cmd is not None

    mock_interaction = MagicMock(spec=discord.Interaction)
    mock_interaction.guild = MagicMock(id=1527720394151170048)
    mock_interaction.user = MagicMock(id=999999)
    mock_interaction.response = MagicMock()
    mock_interaction.response.defer = AsyncMock()
    mock_interaction.followup = MagicMock()
    mock_interaction.followup.send = AsyncMock()

    with patch("apps.obx_tasks.bot.client.is_admin", return_value=True), \
         patch("apps.obx_tasks.bot.client.session_scope") as mock_scope, \
         patch("apps.obx_tasks.bot.announcement_service.deploy_or_update_leaderboard", new_callable=AsyncMock) as mock_deploy:
        from contextlib import contextmanager
        @contextmanager
        def _scope():
            yield db_session
        mock_scope.side_effect = _scope
        mock_deploy.return_value = (True, "Refreshed in #leaderboard")

        # Execute command callback
        await clear_cmd.callback(mock_interaction)

    mock_interaction.response.defer.assert_awaited_once_with(ephemeral=True)
    assert mock_interaction.followup.send.call_count == 1
    call_kwargs = mock_interaction.followup.send.call_args[1]
    assert "embed" in call_kwargs
    embed = call_kwargs["embed"]
    assert "CLEARED & RESET" in embed.title
    mock_deploy.assert_awaited_once_with(mock_interaction.guild, bot)


@pytest.mark.asyncio
async def test_on_ready_auto_clears_new_server_once(db_session):
    from apps.obx_tasks.bot.client import OBXTaskBot
    from packages.database.models.channel_config import GuildConfig
    from apps.obx_core.services.wallet_service import WalletService

    ws = WalletService(db_session)
    user, _, _ = ws.get_or_create_user("user_startup_test")
    ws.credit(discord_user_id="user_startup_test", amount=500, reference_type="test", idempotency_key="k_startup_1")

    bot = MagicMock(spec=OBXTaskBot)
    bot.user = MagicMock(id=123456, name="OBX")
    bot.guilds = []
    bot.tree = MagicMock()
    bot.tree.sync = AsyncMock(return_value=[])

    with patch("apps.obx_tasks.bot.client.session_scope") as mock_scope, \
         patch("apps.obx_tasks.bot.client.get_settings") as mock_settings:
        from contextlib import contextmanager
        @contextmanager
        def _scope():
            yield db_session
        mock_scope.side_effect = _scope

        st = MagicMock()
        st.DISCORD_GUILD_ID = "1527720394151170048"
        st.DISCORD_ADMIN_ROLE_IDS = []
        st.RAID_JOIN_CHANNEL_ID = None
        st.DISCORD_TASK_CHANNEL_ID = "1545220623322714236"
        st.DISCORD_AUCTION_CHANNEL_ID = "1545221157014474812"
        st.DISCORD_WINNERS_CHANNEL_ID = "1545221275608285276"
        st.DISCORD_LEADERBOARD_CHANNEL_ID = "1545221334668414996"
        st.DISCORD_ADMIN_LOG_CHANNEL_ID = "1545221416167940146"
        st.RAID_ROLE_ID = "1539356123553996913"
        mock_settings.return_value = st

        await OBXTaskBot.on_ready(bot)

    cfg = db_session.query(GuildConfig).filter_by(guild_id="1527720394151170048").first()
    assert cfg is not None
    assert cfg.updated_by == "INIT_CLEARED_1527720394151170048"

    from packages.database.models.wallet import Wallet
    w = db_session.query(Wallet).filter_by(user_id=user.id).first()
    assert w.available_balance == 0
