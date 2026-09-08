import pytest
from unittest.mock import MagicMock, AsyncMock, patch
import discord
import uuid

from packages.shared.enums import AuctionType, AuctionStatus
from packages.database.session import session_scope
from apps.obx_tasks.bot.notification_service import send_auction_winner_dm


def mock_session_scope_for(session):
    class DummyContext:
        def __enter__(self):
            return session
        def __exit__(self, exc_type, exc_val, exc_tb):
            pass
    return DummyContext()


@pytest.mark.asyncio
async def test_auction_winner_dm_gtd_format(db_session):
    """Verify GTD winner DM has exact title, project name, (GTD) bracket, image banner, and NO buttons."""
    mock_auction = MagicMock()
    mock_auction.id = uuid.uuid4()
    mock_auction.title = "Monad Whitelist"
    mock_auction.preview_x_display_name = "Monad"
    mock_auction.auction_type = AuctionType.GTD
    mock_auction.preview_image_url = "https://pbs.twimg.com/banner.jpg"

    mock_user = MagicMock(spec=discord.User)
    mock_user.id = 123456789
    mock_user.send = AsyncMock()

    mock_bot = MagicMock(spec=discord.Client)
    mock_bot.get_user = MagicMock(return_value=mock_user)

    with patch("apps.obx_tasks.bot.notification_service.session_scope", lambda: mock_session_scope_for(db_session)):
        ok = await send_auction_winner_dm(
            bot=mock_bot,
            auction=mock_auction,
            discord_user_id="123456789",
        )
        assert ok is True

    mock_user.send.assert_awaited_once()
    call_kwargs = mock_user.send.call_args[1]
    embed = call_kwargs["embed"]

    # Strictly verify title and description
    assert embed.title == "🎉 CONGRATULATIONS!"
    assert embed.description == "You have won the auction for **Monad (GTD)**!"

    # Verify image banner is set
    assert embed.image.url == "https://pbs.twimg.com/banner.jpg"

    # Strictly verify NO buttons attached
    assert "view" not in call_kwargs or call_kwargs["view"] is None


@pytest.mark.asyncio
async def test_auction_winner_dm_fcfs_format(db_session):
    """Verify FCFS winner DM has (FCFS) bracket in project description."""
    mock_auction = MagicMock()
    mock_auction.id = uuid.uuid4()
    mock_auction.title = "Berachain Access"
    mock_auction.preview_x_display_name = "Berachain"
    mock_auction.auction_type = AuctionType.FCFS
    mock_auction.preview_image_url = "https://example.com/bera.png"

    mock_user = MagicMock(spec=discord.User)
    mock_user.id = 987654321
    mock_user.send = AsyncMock()

    mock_bot = MagicMock(spec=discord.Client)
    mock_bot.get_user = MagicMock(return_value=mock_user)

    with patch("apps.obx_tasks.bot.notification_service.session_scope", lambda: mock_session_scope_for(db_session)):
        ok = await send_auction_winner_dm(
            bot=mock_bot,
            auction=mock_auction,
            discord_user_id="987654321",
        )
        assert ok is True

    mock_user.send.assert_awaited_once()
    embed = mock_user.send.call_args[1]["embed"]
    assert embed.title == "🎉 CONGRATULATIONS!"
    assert embed.description == "You have won the auction for **Berachain (FCFS)**!"
    assert embed.image.url == "https://example.com/bera.png"


@pytest.mark.asyncio
async def test_auction_winner_dm_idempotency(db_session):
    """Ensure duplicate winner DMs are never sent if called again."""
    mock_auction = MagicMock()
    mock_auction.id = uuid.uuid4()
    mock_auction.title = "Idempotent Auction"
    mock_auction.preview_x_display_name = "IdemProject"
    mock_auction.auction_type = AuctionType.GTD
    mock_auction.preview_image_url = None
    mock_auction.preview_x_banner_url = None
    mock_auction.image_url = None

    mock_user = MagicMock(spec=discord.User)
    mock_user.id = 555666777
    mock_user.send = AsyncMock()

    mock_bot = MagicMock(spec=discord.Client)
    mock_bot.get_user = MagicMock(return_value=mock_user)

    with patch("apps.obx_tasks.bot.notification_service.session_scope", lambda: mock_session_scope_for(db_session)):
        ok1 = await send_auction_winner_dm(
            bot=mock_bot,
            auction=mock_auction,
            discord_user_id="555666777",
        )
        assert ok1 is True

        # Second call should be skipped due to idempotency
        ok2 = await send_auction_winner_dm(
            bot=mock_bot,
            auction=mock_auction,
            discord_user_id="555666777",
        )
        assert ok2 is False

    assert mock_user.send.await_count == 1


@pytest.mark.asyncio
async def test_auction_winner_dm_forbidden_graceful(db_session):
    """Verify that discord.Forbidden (DMs closed) does not raise an unhandled exception."""
    mock_auction = MagicMock()
    mock_auction.id = uuid.uuid4()
    mock_auction.title = "Closed DMs Auction"
    mock_auction.preview_x_display_name = "ClosedDM"
    mock_auction.auction_type = AuctionType.GTD
    mock_auction.preview_image_url = None
    mock_auction.preview_x_banner_url = None
    mock_auction.image_url = None

    mock_user = MagicMock(spec=discord.User)
    mock_user.id = 444333222
    mock_user.send = AsyncMock(side_effect=discord.Forbidden(MagicMock(), "Cannot send messages to this user"))

    mock_bot = MagicMock(spec=discord.Client)
    mock_bot.get_user = MagicMock(return_value=mock_user)

    with patch("apps.obx_tasks.bot.notification_service.session_scope", lambda: mock_session_scope_for(db_session)):
        ok, detail = await send_auction_winner_dm(
            bot=mock_bot,
            auction=mock_auction,
            discord_user_id="444333222",
            return_detail=True,
        )
        assert ok is False
        assert "DMs closed" in detail or "Forbidden" in detail


@pytest.mark.asyncio
async def test_announce_auction_winners_dispatches_winner_dms(db_session):
    """Test that announce_auction_winners invokes send_auction_winner_dm for each winner."""
    from apps.obx_tasks.bot.announcement_service import announce_auction_winners
    from apps.obx_tasks.services.channel_service import ChannelService

    ch_service = ChannelService(db_session)
    ch_service.update_guild_channel("guild_dm_test", "winners", "9090", "admin")

    mock_auction = MagicMock()
    mock_auction.id = uuid.uuid4()
    mock_auction.title = "Test Auction"
    mock_auction.description = "Test Description"
    mock_auction.preview_x_display_name = "Monad"
    mock_auction.preview_x_bio = "Monad is fast"
    mock_auction.total_slots = 2
    mock_auction.auction_type = AuctionType.GTD
    mock_auction.preview_image_url = "https://example.com/banner.png"
    mock_auction.preview_x_avatar_url = None

    w1 = MagicMock()
    w1.discord_user_id = "11111"
    w2 = MagicMock()
    w2.discord_user_id = "22222"
    winners = [w1, w2]

    mock_guild = MagicMock(spec=discord.Guild)
    mock_guild.id = "guild_dm_test"
    mock_guild.me = MagicMock()

    mock_msg = MagicMock(id=88888)
    mock_ch = MagicMock(spec=discord.TextChannel, id=9090, name="winners")
    mock_ch.permissions_for.return_value = MagicMock(view_channel=True, send_messages=True, embed_links=True)
    mock_ch.send = AsyncMock(return_value=mock_msg)
    mock_guild.get_channel.return_value = mock_ch

    mock_bot = MagicMock(spec=discord.Client)

    with patch("apps.obx_tasks.bot.announcement_service.session_scope", lambda: mock_session_scope_for(db_session)), \
         patch("apps.obx_tasks.bot.announcement_service.announce_auction", AsyncMock(return_value=(True, "OK"))), \
         patch("apps.obx_tasks.bot.notification_service.send_auction_winner_dm", AsyncMock(return_value=True)) as mock_send_dm:
        ok, res = await announce_auction_winners(mock_auction, winners, 2, mock_guild, mock_bot)
        assert ok is True
        assert mock_send_dm.await_count == 2
