import pytest
import discord
from unittest.mock import MagicMock, AsyncMock, patch

from apps.obx_core.services.wallet_service import WalletService
from apps.obx_tasks.bot.client import create_discord_bot


def mock_session_scope_for(session):
    from contextlib import contextmanager
    @contextmanager
    def _scope():
        yield session
    return _scope()


@pytest.mark.asyncio
async def test_admin_bulk_restore_credits_multiple_users(db_session):
    bot = create_discord_bot()
    bulk_cmd = None
    for cmd in bot.tree.get_commands():
        if cmd.name == "admin-bulk-restore":
            bulk_cmd = cmd
            break

    assert bulk_cmd is not None

    mock_interaction = MagicMock(spec=discord.Interaction)
    mock_interaction.guild = MagicMock(id=1527720394151170048)
    mock_interaction.user = MagicMock(id=999999)
    mock_interaction.response.defer = AsyncMock()
    mock_interaction.followup.send = AsyncMock()

    entries_input = """
    <@1344974194768740364> 14
    <@1298929333213073451> 10
    111222333444555666 25
    """

    with patch("apps.obx_tasks.bot.client.is_admin", return_value=True), \
         patch("apps.obx_tasks.bot.client.session_scope", lambda: mock_session_scope_for(db_session)), \
         patch("apps.obx_tasks.bot.announcement_service.deploy_or_update_leaderboard", new_callable=AsyncMock) as mock_deploy:
        await bulk_cmd.callback(mock_interaction, entries=entries_input)

    assert mock_interaction.followup.send.called
    call_kwargs = mock_interaction.followup.send.call_args[1]
    embed = call_kwargs["embed"]
    assert "Bulk OBX Restored" in embed.title
    assert "49 OBX" in embed.description

    ws = WalletService(db_session)
    _, w1, _ = ws.get_or_create_user("1344974194768740364")
    _, w2, _ = ws.get_or_create_user("1298929333213073451")
    _, w3, _ = ws.get_or_create_user("111222333444555666")

    assert w1.available_balance == 14
    assert w2.available_balance == 10
    assert w3.available_balance == 25
    assert mock_deploy.called


@pytest.mark.asyncio
async def test_admin_recover_from_logs_scans_and_reconstructs(db_session):
    bot = create_discord_bot()
    recover_cmd = None
    for cmd in bot.tree.get_commands():
        if cmd.name == "admin-recover-from-logs":
            recover_cmd = cmd
            break

    assert recover_cmd is not None

    mock_interaction = MagicMock(spec=discord.Interaction)
    mock_guild = MagicMock(id=1527720394151170048)
    mock_interaction.guild = mock_guild
    mock_interaction.user = MagicMock(id=999999)
    mock_interaction.response.defer = AsyncMock()
    mock_interaction.followup.send = AsyncMock()

    mock_channel = MagicMock(spec=discord.TextChannel)
    mock_channel.mention = "#obx-admin-logs"
    mock_guild.get_channel.return_value = mock_channel

    # Create mock message history with approval embeds
    msg1 = MagicMock()
    emb1 = MagicMock()
    emb1.description = "<@admin_1> approved submission for <@998877665544>!\n**Task:** Like Post\n**Reward Credited:** `+20 OBX`"
    msg1.embeds = [emb1]

    msg2 = MagicMock()
    emb2 = MagicMock()
    emb2.description = "<@admin_1> approved submission for <@998877665544>!\n**Task:** Retweet\n**Reward Credited:** `+10 OBX`"
    msg2.embeds = [emb2]

    msg3 = MagicMock()
    emb3 = MagicMock()
    emb3.description = "Successfully credited **15 OBX** to <@112233445566>"
    msg3.embeds = [emb3]

    async def mock_history(limit=500):
        for m in [msg1, msg2, msg3]:
            yield m

    mock_channel.history = mock_history

    from apps.obx_tasks.services.channel_service import ChannelService
    ch_s = ChannelService(db_session)
    ch_s.update_guild_channel("1527720394151170048", "admin", "1234567890", "test")

    with patch("apps.obx_tasks.bot.client.is_admin", return_value=True), \
         patch("apps.obx_tasks.bot.client.session_scope", lambda: mock_session_scope_for(db_session)), \
         patch("apps.obx_tasks.bot.announcement_service.deploy_or_update_leaderboard", new_callable=AsyncMock) as mock_deploy:
        await recover_cmd.callback(mock_interaction, limit=500)

    assert mock_interaction.followup.send.called
    call_kwargs = mock_interaction.followup.send.call_args[1]
    embed = call_kwargs["embed"]
    assert "Balances Reconstructed" in embed.title

    ws = WalletService(db_session)
    _, w_user1, _ = ws.get_or_create_user("998877665544")
    _, w_user2, _ = ws.get_or_create_user("112233445566")

    # user 1 got 20 + 10 = 30
    assert w_user1.available_balance == 30
    # user 2 got 15
    assert w_user2.available_balance == 15
    assert mock_deploy.called
