import pytest
import uuid
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, AsyncMock, patch
from contextlib import contextmanager

import discord
from packages.database.session import session_scope
from packages.database.models.auction import Auction, AuctionBid, AuctionClaim
from packages.shared.enums import AuctionType, AuctionStatus
from apps.obx_tasks.services.auction_service import AuctionService, AuctionError
from apps.obx_tasks.services.channel_service import ChannelService
from apps.obx_tasks.services.url_preview_service import UrlPreviewService, URLPreviewMetadata
from apps.obx_tasks.bot.auction_views import (
    build_auction_notification_embed,
    AuctionNotificationCardView,
    AdminCreateAuctionModal,
    AdminEditAuctionModal,
)
from apps.obx_tasks.bot.announcement_service import announce_auction


@contextmanager
def mock_session_scope_for(session):
    yield session


def test_auction_can_store_project_x_url(db_session):
    """Auction model and service correctly persist project_x_url."""
    service = AuctionService(db_session)
    auc = service.create_auction(
        title="Astral Sentinels",
        reward_title="Guaranteed WL",
        description="First generative art on Robinhood Chain",
        total_slots=5,
        price_or_min_bid=10,
        project_x_url="https://x.com/AstralSentinels",
        created_by="admin_test",
    )
    assert auc.project_x_url == "https://x.com/AstralSentinels"
    assert auc.title == "Astral Sentinels"

    # Reload from DB
    reloaded = service.get_auction(auc.id)
    assert reloaded.project_x_url == "https://x.com/AstralSentinels"


@pytest.mark.asyncio
async def test_x_profile_metadata_fetched_safely():
    """UrlPreviewService safely extracts profile metadata for public X handles."""
    fake_json = {
        "code": 200,
        "message": "OK",
        "user": {
            "name": "Astral Sentinels",
            "screen_name": "AstralSentinels",
            "description": "4,444 celestial guardians on Robinhood Chain.",
            "avatar_url": "https://pbs.twimg.com/profile_images/astral_avatar.png",
            "banner_url": "https://pbs.twimg.com/profile_banners/astral_banner.jpg",
        }
    }

    with patch("apps.obx_tasks.services.url_preview_service._safe_fetch_bytes") as mock_fetch:
        import json
        mock_fetch.return_value = json.dumps(fake_json).encode("utf-8")

        meta = await UrlPreviewService.fetch_preview("https://x.com/AstralSentinels")
        assert meta.status == "SUCCESS"
        assert meta.author == "Astral Sentinels"
        assert meta.handle == "@AstralSentinels"
        assert meta.banner_url == "https://pbs.twimg.com/profile_banners/astral_banner.jpg"
        assert meta.avatar_url == "https://pbs.twimg.com/profile_images/astral_avatar.png"
        assert "celestial guardians" in meta.description


def test_large_project_image_priority_and_storage(db_session):
    """Verify strict priority:
    1. X banner -> 2. OpenGraph image -> 3. Avatar fallback -> 4. None.
    Selected image is stored as auction.preview_image_url and rendered as embed.image.
    """
    from apps.obx_tasks.services.auction_service import resolve_auction_preview_image

    # Priority 1: Banner wins over OG and Avatar
    assert resolve_auction_preview_image(
        banner_url="https://img.com/banner.jpg",
        og_image_url="https://img.com/og.png",
        avatar_url="https://img.com/avatar.jpg",
    ) == "https://img.com/banner.jpg"

    # Priority 2: OG image wins when banner is None/empty
    assert resolve_auction_preview_image(
        banner_url=None,
        og_image_url="https://img.com/og.png",
        avatar_url="https://img.com/avatar.jpg",
    ) == "https://img.com/og.png"

    # Priority 3: Avatar is fallback when banner and OG are None
    assert resolve_auction_preview_image(
        banner_url=None,
        og_image_url=None,
        avatar_url="https://img.com/avatar.jpg",
    ) == "https://img.com/avatar.jpg"

    # Priority 4: None when none safely available
    assert resolve_auction_preview_image(
        banner_url=None,
        og_image_url=None,
        avatar_url=None,
    ) is None

    # Invalid URLs safely rejected
    assert resolve_auction_preview_image(
        banner_url="javascript:alert(1)",
        og_image_url="ftp://files.com/img.jpg",
        avatar_url="invalid_url",
    ) is None

    # Test update_auction_preview stores preview_image_url
    service = AuctionService(db_session)
    auc = service.create_auction(
        title="Priority Test Auction",
        reward_title="Pass",
        description="Testing priority",
        created_by="admin",
    )

    # 1. Update with banner
    service.update_auction_preview(
        auction_id=auc.id,
        banner_url="https://pbs.twimg.com/banner.jpg",
        og_image_url="https://pbs.twimg.com/og.png",
        avatar_url="https://pbs.twimg.com/avatar.jpg",
    )
    reloaded = service.get_auction(auc.id)
    assert reloaded.preview_image_url == "https://pbs.twimg.com/banner.jpg"

    embed1 = build_auction_notification_embed(reloaded)
    assert embed1.image.url == "https://pbs.twimg.com/banner.jpg"

    # 2. Update with OG image only (banner missing)
    service.update_auction_preview(
        auction_id=auc.id,
        banner_url=None,
        og_image_url="https://pbs.twimg.com/og.png",
        avatar_url="https://pbs.twimg.com/avatar.jpg",
    )
    reloaded = service.get_auction(auc.id)
    assert reloaded.preview_image_url == "https://pbs.twimg.com/og.png"

    embed2 = build_auction_notification_embed(reloaded)
    assert embed2.image.url == "https://pbs.twimg.com/og.png"

    # 3. Update with avatar only (banner & OG missing)
    service.update_auction_preview(
        auction_id=auc.id,
        banner_url=None,
        og_image_url=None,
        avatar_url="https://pbs.twimg.com/avatar.jpg",
    )
    reloaded = service.get_auction(auc.id)
    assert reloaded.preview_image_url == "https://pbs.twimg.com/avatar.jpg"

    embed3 = build_auction_notification_embed(reloaded)
    assert embed3.image.url == "https://pbs.twimg.com/avatar.jpg"

    # 4. No images available
    service.update_auction_preview(
        auction_id=auc.id,
        banner_url="",
        og_image_url="",
        avatar_url="",
    )
    reloaded = service.get_auction(auc.id)
    assert reloaded.preview_image_url is None

    embed4 = build_auction_notification_embed(reloaded)
    assert embed4.image.url is None


def test_x_banner_and_raw_url_hidden():
    """X banner appears as large embed image, raw URL is hidden."""
    auc = MagicMock(spec=Auction)
    auc.id = uuid.uuid4()
    auc.title = "Astral Sentinels (GTD-DTC)"
    auc.reward_title = "WL"
    auc.description = "Astral Sentinels is the first generative art collection on Robinhood Chain."
    auc.auction_type = AuctionType.GTD
    auc.total_slots = 5
    auc.price_or_min_bid = 10
    auc.status = AuctionStatus.ACTIVE
    auc.ends_at = datetime.now(timezone.utc) + timedelta(hours=19)
    auc.image_url = None
    auc.project_x_url = "https://x.com/AstralSentinels"
    auc.preview_x_handle = "@AstralSentinels"
    auc.preview_x_display_name = "Astral Sentinels"
    auc.preview_image_url = "https://pbs.twimg.com/profile_banners/astral_banner.jpg"
    auc.preview_x_banner_url = "https://pbs.twimg.com/profile_banners/astral_banner.jpg"
    auc.preview_x_avatar_url = "https://pbs.twimg.com/profile_images/astral_avatar.png"

    embed = build_auction_notification_embed(auc)

    # 1. Headline and hierarchy
    assert embed.title == "🎟️ WHITELIST AUCTION"
    assert "ASTRAL SENTINELS (GTD-DTC)" in embed.description
    assert "Astral Sentinels is the first generative art collection" in embed.description
    assert "@AstralSentinels" in embed.description

    # 2. Raw URL MUST NOT be visible anywhere in the embed
    assert "https://x.com/AstralSentinels" not in embed.description
    assert "https://x.com" not in embed.description

    # 3. Large Project Banner is set as embed.image
    assert embed.image.url == "https://pbs.twimg.com/profile_banners/astral_banner.jpg"

    # 4. Clean compact table
    assert "WINNERS" in embed.description
    assert "MIN BID" in embed.description
    assert "10 OBX" in embed.description

    # 5. Clean footer: zero backend IDs
    assert str(auc.id) not in embed.description
    assert embed.footer.text is None or not embed.footer.text


@pytest.mark.asyncio
async def test_preview_failure_does_not_block_auction_creation(db_session):
    """When preview fetch fails or times out, auction is still created and clean fallback is used."""
    mock_intr = MagicMock(spec=discord.Interaction)
    mock_intr.user.id = 123456
    mock_intr.response = AsyncMock()
    mock_intr.followup = AsyncMock()
    mock_intr.guild = None

    modal = AdminCreateAuctionModal()
    modal.project_x_url._value = "https://x.com/NonExistentOrFailedProfile"
    modal.slots_and_price._value = "3 / 15"
    modal.duration._value = "24h"

    with patch("apps.obx_tasks.bot.auction_views.session_scope", lambda: mock_session_scope_for(db_session)), \
         patch("apps.obx_tasks.services.url_preview_service.UrlPreviewService.fetch_preview", side_effect=Exception("Network timeout")):

        await modal.on_submit(mock_intr)

    mock_intr.followup.send.assert_awaited_once()
    embed = mock_intr.followup.send.call_args[1]["embed"]
    view = mock_intr.followup.send.call_args[1]["view"]
    assert "COULD NOT RETRIEVE PROJECT METADATA" in embed.title
    assert "NonExistentOrFailedProfile" in embed.description
    labels = [b.label for b in view.children if hasattr(b, "label")]
    assert "RETRY FETCH" in labels
    assert "ENTER DESCRIPTION MANUALLY" in labels


def test_editing_x_url_updates_auction_and_refreshes_preview(db_session):
    """Editing project_x_url updates the auction model cleanly."""
    service = AuctionService(db_session)
    auc = service.create_auction(
        title="Initial Drop",
        reward_title="Pass",
        description="Initial description",
        total_slots=2,
        price_or_min_bid=20,
        project_x_url="https://x.com/OldHandle",
        created_by="admin",
    )
    assert auc.project_x_url == "https://x.com/OldHandle"

    # Edit X URL
    updated = service.edit_auction(
        auction_id=auc.id,
        changed_by="admin",
        project_x_url="https://x.com/NewHandle",
    )
    assert updated.project_x_url == "https://x.com/NewHandle"

    # Update preview metadata
    updated_preview = service.update_auction_preview(
        auction_id=auc.id,
        project_x_url="https://x.com/NewHandle",
        handle="@NewHandle",
        display_name="New Handle Official",
        avatar_url="https://img.com/avatar.png",
        banner_url="https://img.com/banner.png",
    )
    assert updated_preview.preview_x_handle == "@NewHandle"
    assert updated_preview.preview_x_display_name == "New Handle Official"
    assert updated_preview.preview_x_banner_url == "https://img.com/banner.png"


def test_ranked_auction_retains_bid_edit_bid_my_bid_buttons():
    """Ranked GTD auctions have exactly [ 💎 BID ], [ ✏️ EDIT BID ], and [ 📍 MY BID ]."""
    auc_id = str(uuid.uuid4())
    view = AuctionNotificationCardView(auction_id=auc_id, is_active=True, is_fcfs=False)

    labels = [b.label for b in view.children if hasattr(b, "label")]
    assert labels == ["BID", "EDIT BID", "MY BID"]

    custom_ids = [b.custom_id for b in view.children if hasattr(b, "custom_id")]
    assert f"obx:auc_card:bid:{auc_id}" in custom_ids
    assert f"obx:auc_card:edit_bid:{auc_id}" in custom_ids
    assert f"obx:auc_card:rankings:{auc_id}" in custom_ids
    assert not any("claim" in cid for cid in custom_ids)


def test_fcfs_retains_claim_spot_behavior():
    """FCFS fixed-price sales retain [ ⚡ CLAIM SPOT ] and [ 📍 MY PURCHASE ], no bidding buttons."""
    auc_id = str(uuid.uuid4())
    view = AuctionNotificationCardView(auction_id=auc_id, is_active=True, is_fcfs=True)

    labels = [b.label for b in view.children if hasattr(b, "label")]
    assert labels == ["CLAIM SPOT", "MY PURCHASE"]

    custom_ids = [b.custom_id for b in view.children if hasattr(b, "custom_id")]
    assert f"obx:auc_card:claim:{auc_id}" in custom_ids
    assert f"obx:auc_card:rankings:{auc_id}" in custom_ids
    assert not any("bid" in cid for cid in custom_ids)


def test_normal_auction_type_cannot_accidentally_change(db_session):
    """A ranked GTD auction retains GTD type throughout edits."""
    service = AuctionService(db_session)
    auc = service.create_auction(
        title="Type Safety Check",
        reward_title="Pass",
        description="Testing immutability",
        auction_type=AuctionType.GTD,
        total_slots=10,
        price_or_min_bid=5,
        created_by="admin",
    )
    assert auc.auction_type == AuctionType.GTD

    # Edit auction
    updated = service.edit_auction(
        auction_id=auc.id,
        changed_by="admin",
        title="Type Safety Check Renamed",
        total_slots=15,
    )
    assert updated.auction_type == AuctionType.GTD


@pytest.mark.asyncio
async def test_auction_updates_do_not_reping_raid_role_id_and_no_duplicates(db_session):
    """Initial publication pings <@&RAID_ROLE_ID>; subsequent edits use content=None (no re-ping, no duplicate)."""
    ch_service = ChannelService(db_session)
    ch_service.update_guild_channel("g_auc_test", "auctions", "999", "admin")

    service = AuctionService(db_session)
    auc = service.create_auction(
        title="Ping Once Test",
        reward_title="WL Spot",
        description="Test ping behavior",
        total_slots=3,
        price_or_min_bid=10,
        created_by="admin",
    )

    mock_guild = MagicMock(spec=discord.Guild, id="g_auc_test")
    mock_msg = MagicMock(id=112233)
    mock_msg.edit = AsyncMock()
    mock_ch = MagicMock(spec=discord.TextChannel, id=999, name="auctions")
    mock_ch.permissions_for.return_value = MagicMock(view_channel=True, send_messages=True, embed_links=True)
    mock_ch.send = AsyncMock(return_value=mock_msg)
    mock_ch.fetch_message = AsyncMock(return_value=mock_msg)
    mock_guild.get_channel.return_value = mock_ch
    mock_bot = MagicMock(spec=discord.Client)

    with patch("apps.obx_tasks.bot.announcement_service.session_scope", lambda: mock_session_scope_for(db_session)), \
         patch("apps.obx_tasks.bot.announcement_service.get_settings") as mock_settings:
        mock_settings.return_value.RAID_ROLE_ID = "55443322"

        # 1. First Publish
        ok1, msg1 = await announce_auction(auc, mock_guild, mock_bot)
        assert ok1 is True
        mock_ch.send.assert_called_once()
        send_kwargs = mock_ch.send.call_args[1]
        assert send_kwargs["content"] == "<@&55443322>"

        # 2. In-place Update (e.g. new bid placed or details edited)
        ok2, msg2 = await announce_auction(auc, mock_guild, mock_bot)
        assert ok2 is True
        # Must NOT send a new message
        assert mock_ch.send.call_count == 1
        # Must edit existing message with allowed_mentions suppressing re-ping
        mock_msg.edit.assert_called_once()
        edit_kwargs = mock_msg.edit.call_args[1]
        assert edit_kwargs.get("allowed_mentions") is not None
        assert not edit_kwargs["allowed_mentions"].roles
        assert not edit_kwargs["allowed_mentions"].everyone
