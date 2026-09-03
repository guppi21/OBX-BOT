import pytest
import uuid
from datetime import datetime, timezone
from unittest.mock import MagicMock, AsyncMock, patch

import discord

from packages.database.models.raider_profile import RaiderProfile
from packages.database.models.auction import Auction, AuctionBid
from packages.database.models.task import Task
from packages.database.models.submission import TaskSubmission
from packages.database.models.user import User
from packages.database.models.wallet import Wallet
from packages.shared.enums import AuctionType, AuctionStatus, SubmissionStatus
from apps.obx_tasks.bot.admin_members_views import (
    collect_all_raider_members,
    build_members_list_embed,
    build_member_detail_embed,
    AdminMembersListView,
    AdminMemberDetailView,
    RaiderMemberInfo,
    handle_admin_members,
)
from apps.obx_tasks.bot.announcement_service import announce_auction_winners
from apps.obx_tasks.bot.join_raid_views import SetTwitterModal
from apps.obx_tasks.services.channel_service import ChannelService
from tests.test_discord_channels import mock_session_scope_for


def test_collect_all_raider_members_and_build_embeds(db_session):
    u1_id = uuid.uuid4()
    u2_id = uuid.uuid4()
    user_1 = User(id=u1_id, discord_user_id="111222")
    user_2 = User(id=u2_id, discord_user_id="333444")
    db_session.add_all([user_1, user_2])
    db_session.flush()

    # Wallets
    w1 = Wallet(user_id=u1_id, available_balance=1500, locked_balance=0)
    w2 = Wallet(user_id=u2_id, available_balance=500, locked_balance=0)
    db_session.add_all([w1, w2])

    # Raider Profiles
    rp1 = RaiderProfile(
        discord_user_id="111222",
        twitter_handle="AlphaRaider",
        twitter_profile_url="https://x.com/AlphaRaider",
        twitter_avatar_url="https://pbs.twimg.com/avatar1.jpg",
    )
    db_session.add(rp1)

    # Submissions
    task = Task(
        title="Test Raid",
        description="Test",
        task_type="LIKE",
        target_url="https://x.com/post",
        reward_per_user=50,
        total_reward_pool=500,
        created_by="admin",
    )
    db_session.add(task)
    db_session.flush()

    sub = TaskSubmission(
        task_id=task.id,
        discord_user_id="111222",
        x_username="AlphaRaider",
        proof_url="https://x.com/proof/123",
        proof_text="Proof link",
        status=SubmissionStatus.APPROVED,
        reward_amount=50,
        reviewed_at=datetime.now(timezone.utc),
    )
    db_session.add(sub)
    db_session.commit()

    mock_guild = MagicMock(spec=discord.Guild)
    m1 = MagicMock(spec=discord.Member, id=111222, display_name="AlphaUser", roles=[])
    m2 = MagicMock(spec=discord.Member, id=333444, display_name="BetaUser", roles=[])
    mock_guild.get_member.side_effect = lambda uid: m1 if str(uid) == "111222" else (m2 if str(uid) == "333444" else None)

    members = collect_all_raider_members(db_session, mock_guild)
    assert len(members) >= 2

    alpha = [m for m in members if m.discord_user_id == "111222"][0]
    assert alpha.display_name == "AlphaUser"
    assert alpha.x_handle == "AlphaRaider"
    assert alpha.x_profile_url == "https://x.com/AlphaRaider"
    assert alpha.x_avatar_url == "https://pbs.twimg.com/avatar1.jpg"
    assert alpha.balance == 1500
    assert alpha.tasks_completed == 1
    assert alpha.task_earnings == 50
    assert alpha.rank == 1
    assert alpha.status == "Active"

    beta = [m for m in members if m.discord_user_id == "333444"][0]
    assert beta.display_name == "BetaUser"
    assert beta.x_handle is None
    assert beta.balance == 500
    assert beta.rank == 2

    # Verify List Embed
    list_embed = build_members_list_embed(members, page=0, page_size=10)
    assert list_embed.title == "👥 RAIDERS & MEMBERS"
    assert "AlphaUser" in list_embed.description
    assert "@AlphaRaider" in list_embed.description
    assert "1,500 OBX" in list_embed.description
    assert "#1" in list_embed.description
    assert "BetaUser" in list_embed.description

    # Verify Detail Embed for alpha
    detail_embed = build_member_detail_embed(alpha)
    assert detail_embed.title == "👤 MEMBER"
    assert "AlphaUser" in detail_embed.description
    assert "@AlphaRaider" in detail_embed.description
    assert "https://x.com/AlphaRaider" in detail_embed.description
    assert "1,500 OBX" in detail_embed.description
    assert "1" in detail_embed.description
    assert "#1" in detail_embed.description
    assert "Active" in detail_embed.description
    assert detail_embed.thumbnail.url == "https://pbs.twimg.com/avatar1.jpg"


@pytest.mark.asyncio
async def test_admin_members_view_pagination_and_detail(db_session):
    members = [
        RaiderMemberInfo(
            discord_user_id=f"user_{i}",
            display_name=f"Member_{i}",
            x_handle=f"x_{i}",
            x_profile_url=f"https://x.com/x_{i}",
            x_avatar_url=None,
            balance=100 * (10 - i),
            task_earnings=50,
            tasks_completed=i,
            rank=i,
            status="Active",
            created_at=datetime.now(timezone.utc),
        )
        for i in range(1, 15)
    ]

    view = AdminMembersListView(members, page=0, page_size=5)
    assert view.total_pages == 3
    # Check select dropdown is present
    select_items = [c for c in view.children if isinstance(c, discord.ui.Select)]
    assert len(select_items) == 1
    assert len(select_items[0].options) == 5

    # Test callback on select
    mock_intr = MagicMock(spec=discord.Interaction)
    mock_intr.response = AsyncMock()
    select_items[0]._values = ["user_1"]
    await select_items[0].callback(mock_intr)

    mock_intr.response.edit_message.assert_awaited_once()
    kw = mock_intr.response.edit_message.call_args[1]
    assert kw["embed"].title == "👤 MEMBER"
    assert "Member_1" in kw["embed"].description
    assert isinstance(kw["view"], AdminMemberDetailView)


@pytest.mark.asyncio
async def test_winner_announcement_reuses_auction_preview_and_winner_x_handles(db_session):
    """Winner announcement reuses stored auction project preview data & winner X accounts."""
    ch_service = ChannelService(db_session)
    ch_service.update_guild_channel("101", "winners", "9901", "admin")

    # 1. Create Auction with stored project preview data
    auc = Auction(
        title="Celestial Pass",
        reward_title="Genesis WL",
        description="Exclusive generative pass on Robinhood Chain.",
        auction_type=AuctionType.GTD,
        total_slots=3,
        allocated_slots=2,
        price_or_min_bid=100,
        status=AuctionStatus.COMPLETED,
        project_x_url="https://x.com/CelestialPass",
        preview_image_url="https://pbs.twimg.com/celestial_banner.jpg",
        preview_x_display_name="Celestial Pass Official",
        preview_x_bio="The premier guardians on Robinhood Chain.",
        created_by="admin_1",
    )
    db_session.add(auc)

    # 2. Winner 1 has linked X account, Winner 2 does not
    rp_w1 = RaiderProfile(
        discord_user_id="9911",
        twitter_handle="CelestialWhale",
        twitter_profile_url="https://x.com/CelestialWhale",
    )
    db_session.add(rp_w1)
    db_session.commit()

    w1 = MagicMock(spec=AuctionBid, discord_user_id="9911", bid_amount=800)
    w2 = MagicMock(spec=AuctionBid, discord_user_id="9922", bid_amount=600)
    winners = [w1, w2]

    mock_guild = MagicMock(spec=discord.Guild, id=101)
    mock_ch = MagicMock(spec=discord.TextChannel, id=9901)
    mock_ch.permissions_for.return_value = MagicMock(view_channel=True, send_messages=True, embed_links=True)
    mock_msg = MagicMock(id=8888)
    mock_ch.send = AsyncMock(return_value=mock_msg)
    mock_guild.get_channel.return_value = mock_ch

    m1 = MagicMock(spec=discord.Member, id=9911, display_name="WhaleLord")
    m2 = MagicMock(spec=discord.Member, id=9922, display_name="DiamondHands")
    mock_guild.get_member.side_effect = lambda uid: m1 if str(uid) == "9911" else (m2 if str(uid) == "9922" else None)

    mock_bot = MagicMock(spec=discord.Client)
    mock_guild.me = MagicMock()

    with patch("apps.obx_tasks.bot.announcement_service.session_scope", lambda: mock_session_scope_for(db_session)), \
         patch("apps.obx_tasks.bot.announcement_service.announce_auction", AsyncMock(return_value=(True, "OK"))):
        ok, msg = await announce_auction_winners(auc, winners, 2, mock_guild, mock_bot)

    assert ok is True
    mock_ch.send.assert_awaited_once()
    kw = mock_ch.send.call_args[1]
    embed = kw["embed"]

    # 1. Inbuilt title
    assert embed.title == "🏆 AUCTION RESULTS"

    # 2. Reused project data
    assert "Celestial Pass Official" in embed.description
    assert "The premier guardians on Robinhood Chain." in embed.description

    # 3. Attached large preview image from stored auction
    assert embed.image.url == "https://pbs.twimg.com/celestial_banner.jpg"

    # 4. Winners display Discord tags only, no X handles
    assert "🥇 @WhaleLord" in embed.description
    assert "🥈 @DiamondHands" in embed.description
    assert "🐦 @" not in embed.description

    # 6. Spots awarded
    assert "🎟️ **SPOTS AWARDED**" in embed.description
    assert "2 / 3" in embed.description
