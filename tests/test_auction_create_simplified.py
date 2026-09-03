import pytest
import uuid
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, AsyncMock, patch
import discord

from packages.database.session import session_scope
from packages.database.models.auction import Auction
from packages.shared.enums import AuctionStatus, AuctionType
from apps.obx_tasks.services.auction_service import AuctionService
from apps.obx_tasks.services.url_preview_service import URLPreviewMetadata
from apps.obx_tasks.bot.auction_views import (
    AdminCreateAuctionModal,
    AuctionCreateConfirmView,
    AuctionCreateRetryView,
    ManualDescriptionModal,
    render_auction_preview_and_confirm,
    render_auction_fetch_failed,
)
from tests.test_discord_channels import mock_session_scope_for


def test_admin_create_auction_modal_has_no_manual_description_field():
    """Verify creation modal contains strictly 4 fields: X URL, Spots, Type, Duration (no title)."""
    modal = AdminCreateAuctionModal()
    # Exactly 4 inputs: X URL, Spots, Type, Duration
    assert len(modal.children) == 4
    assert modal.title == "🎟️ LAUNCH WHITELIST AUCTION"

    labels = [child.label for child in modal.children]
    assert "Auction Title" not in labels
    assert "Title" not in labels
    assert "Project X / Twitter Profile URL" in labels
    assert "Whitelist Spots" in labels
    assert "Type" in labels
    assert "Duration" in labels

    # Must NOT have Project Description field
    assert "Project Description" not in labels
    assert not hasattr(modal, "description")


@pytest.mark.asyncio
async def test_modal_submits_fetches_bio_and_renders_preview(db_session):
    """When X profile URL is provided, bot fetches bio, banner, and renders preview with confirm view."""
    modal = AdminCreateAuctionModal()
    modal.project_x_url._value = "https://x.com/AstralSentinels"
    modal.slots_and_price._value = "5"
    modal.wl_type._value = "GTD"
    modal.duration._value = "19h"

    mock_intr = MagicMock(spec=discord.Interaction)
    mock_intr.user.id = "admin_user_123"
    mock_intr.response = MagicMock()
    mock_intr.response.defer = AsyncMock()
    mock_intr.response.is_done = MagicMock(return_value=True)
    mock_intr.followup = MagicMock()
    mock_intr.followup.send = AsyncMock()

    mock_preview = URLPreviewMetadata(
        platform="X",
        author="Astral Sentinels",
        handle="@AstralSentinels",
        description="4,444 celestial guardians on Robinhood Chain.",
        image_url="https://pbs.twimg.com/og_preview.jpg",
        avatar_url="https://pbs.twimg.com/avatar.jpg",
        banner_url="https://pbs.twimg.com/banner.jpg",
        source="fxtwitter_profile",
        status="SUCCESS",
        fetched_at=datetime.now(timezone.utc),
    )

    with patch("apps.obx_tasks.services.url_preview_service.UrlPreviewService.fetch_preview", AsyncMock(return_value=mock_preview)):
        await modal.on_submit(mock_intr)

    mock_intr.followup.send.assert_awaited_once()
    kw = mock_intr.followup.send.call_args[1]
    embed = kw["embed"]
    view = kw["view"]

    # Preview verification
    assert embed.title == "🎟️ WHITELIST AUCTION"
    assert "ASTRAL SENTINELS" in embed.description
    assert "4,444 celestial guardians on Robinhood Chain." in embed.description
    assert "@AstralSentinels" in embed.description
    assert "5" in embed.description
    assert "10 OBX" in embed.description  # default min bid

    # Priority image: banner
    assert embed.image.url == "https://pbs.twimg.com/banner.jpg"

    # Confirm view has Publish and Cancel
    assert isinstance(view, AuctionCreateConfirmView)
    labels = [b.label for b in view.children if hasattr(b, "label")]
    assert "PUBLISH AUCTION" in labels
    assert "CANCEL" in labels


@pytest.mark.asyncio
async def test_confirm_publish_creates_auction_in_db_and_announces(db_session):
    """Clicking [ 🚀 PUBLISH AUCTION ] creates auction with bio and announces card."""
    payload = {
        "title": "Astral Sentinels",
        "reward_title": "GTD-DTC",
        "total_slots": 5,
        "price_or_min_bid": 10,
        "ends_at": datetime.now(timezone.utc) + timedelta(hours=19),
        "project_x_url": "https://x.com/AstralSentinels",
        "creator_id": "admin_456",
        "handle": "@AstralSentinels",
    }
    bio = "4,444 celestial guardians on Robinhood Chain."
    preview_meta = MagicMock(author="Astral Sentinels", avatar_url="https://avatar.png", banner_url="https://banner.png")

    view = AuctionCreateConfirmView(
        payload=payload,
        bio=bio,
        preview_meta=preview_meta,
        preview_image_url="https://banner.png",
    )

    mock_guild = MagicMock(spec=discord.Guild, id="g_test")
    mock_intr = MagicMock(spec=discord.Interaction)
    mock_intr.guild = mock_guild
    mock_intr.client = MagicMock()
    mock_intr.response = MagicMock()
    mock_intr.response.defer = AsyncMock()
    mock_intr.followup = MagicMock()
    mock_intr.followup.send = AsyncMock()

    with patch("apps.obx_tasks.bot.auction_views.session_scope", lambda: mock_session_scope_for(db_session)), \
         patch("apps.obx_tasks.bot.announcement_service.announce_auction", AsyncMock(return_value=(True, "Card published"))), \
         patch("apps.obx_tasks.bot.announcement_service.deploy_or_update_auction_center", AsyncMock()):
        await view.children[0].callback(mock_intr)

    # Check database
    auc = db_session.query(Auction).filter_by(title="Astral Sentinels").first()
    assert auc is not None
    assert auc.description == bio
    assert auc.preview_image_url == "https://banner.png"
    assert auc.project_x_url == "https://x.com/AstralSentinels"
    assert auc.preview_x_handle == "@AstralSentinels"
    assert auc.total_slots == 5
    assert auc.price_or_min_bid == 10

    mock_intr.followup.send.assert_awaited_once()
    res_embed = mock_intr.followup.send.call_args[1]["embed"]
    assert "Published" in res_embed.title


@pytest.mark.asyncio
async def test_fetch_failed_shows_error_with_retry_and_manual_fallback():
    """When X metadata fetch fails, error screen gives Retry, Enter Manually, Cancel."""
    payload = {
        "title": "Unfetchable Project",
        "reward_title": "WL",
        "total_slots": 1,
        "price_or_min_bid": 10,
        "ends_at": datetime.now(timezone.utc) + timedelta(hours=24),
        "project_x_url": "https://x.com/UnavailableHandle",
        "creator_id": "admin_789",
        "handle": "@UnavailableHandle",
    }

    mock_intr = MagicMock(spec=discord.Interaction)
    mock_intr.response = MagicMock()
    mock_intr.response.defer = AsyncMock()
    mock_intr.response.is_done = MagicMock(return_value=True)
    mock_intr.followup = MagicMock()
    mock_intr.followup.send = AsyncMock()

    await render_auction_fetch_failed(mock_intr, payload, "https://x.com/UnavailableHandle")

    mock_intr.followup.send.assert_awaited_once()
    kw = mock_intr.followup.send.call_args[1]
    embed = kw["embed"]
    view = kw["view"]

    assert embed.title == "⚠️ COULD NOT RETRIEVE PROJECT METADATA"
    assert "https://x.com/UnavailableHandle" in embed.description
    assert isinstance(view, AuctionCreateRetryView)

    labels = [b.label for b in view.children if hasattr(b, "label")]
    assert "RETRY FETCH" in labels
    assert "ENTER DESCRIPTION MANUALLY" in labels
    assert "CANCEL" in labels


@pytest.mark.asyncio
async def test_manual_description_fallback_renders_preview():
    """Submitting ManualDescriptionModal successfully advances to preview with manual bio."""
    payload = {
        "title": "Manual Project",
        "reward_title": "WL",
        "total_slots": 2,
        "price_or_min_bid": 20,
        "ends_at": datetime.now(timezone.utc) + timedelta(hours=5),
        "project_x_url": "https://x.com/ManualProject",
        "creator_id": "admin_manual",
        "handle": "@ManualProject",
    }

    modal = ManualDescriptionModal(payload=payload)
    modal.description._value = "Manually entered description by admin."

    mock_intr = MagicMock(spec=discord.Interaction)
    mock_intr.response = MagicMock()
    mock_intr.response.defer = AsyncMock()
    mock_intr.response.is_done = MagicMock(return_value=True)
    mock_intr.followup = MagicMock()
    mock_intr.followup.send = AsyncMock()

    await modal.on_submit(mock_intr)

    mock_intr.followup.send.assert_awaited_once()
    kw = mock_intr.followup.send.call_args[1]
    embed = kw["embed"]
    view = kw["view"]

    assert embed.title == "🎟️ WHITELIST AUCTION"
    assert "MANUAL PROJECT" in embed.description
    assert "Manually entered description by admin." in embed.description


@pytest.mark.asyncio
async def test_admin_create_auction_custom_type_field(db_session):
    """Admin can specify a custom whitelist type (e.g. 'OG', 'FCFS', 'Guaranteed')."""
    modal = AdminCreateAuctionModal()
    modal.project_x_url._value = "https://x.com/SuperProject"
    modal.slots_and_price._value = "3 / 25"
    modal.wl_type._value = "Guaranteed Free Mint"
    modal.duration._value = "12h"

    mock_intr = MagicMock(spec=discord.Interaction)
    mock_intr.user.id = "admin_custom_type"
    mock_intr.response = MagicMock()
    mock_intr.response.defer = AsyncMock()
    mock_intr.response.is_done = MagicMock(return_value=True)
    mock_intr.followup = MagicMock()
    mock_intr.followup.send = AsyncMock()

    mock_preview = URLPreviewMetadata(
        platform="X",
        author="Super Project",
        handle="@SuperProject",
        description="Next-gen gaming ecosystem.",
        image_url=None,
        avatar_url="https://avatar.png",
        banner_url=None,
        source="fxtwitter_profile",
        status="SUCCESS",
        fetched_at=datetime.now(timezone.utc),
    )

    with patch("apps.obx_tasks.services.url_preview_service.UrlPreviewService.fetch_preview", AsyncMock(return_value=mock_preview)):
        await modal.on_submit(mock_intr)

    mock_intr.followup.send.assert_awaited_once()
    kw = mock_intr.followup.send.call_args[1]
    embed = kw["embed"]
    view = kw["view"]

    # Verify custom type is rendered in SPOT TYPE row
    assert "Guaranteed Free Mint" in embed.description
    assert "SUPER PROJECT" in embed.description
    assert "3" in embed.description
    assert "25 OBX" in embed.description
    assert view.payload["reward_title"] == "Guaranteed Free Mint"

    assert isinstance(view, AuctionCreateConfirmView)
