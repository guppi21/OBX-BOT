import pytest
from unittest.mock import MagicMock, patch, AsyncMock
from packages.database.models.task import Task
from packages.shared.enums import TaskStatus, TaskType
from apps.obx_tasks.bot.announcement_service import (
    build_task_announcement_embed,
    TaskAnnouncementCardView,
    announce_task,
)
from apps.obx_tasks.services.channel_service import ChannelService
from contextlib import contextmanager

@contextmanager
def mock_session_scope_for(db_session):
    yield db_session
import discord


def test_task_embed_hides_raw_url_and_shows_luxury_social_post():
    mock_task = MagicMock(spec=Task)
    mock_task.id = "task-uuid-1"
    mock_task.title = "Like and RT our Monad Drop"
    mock_task.description = "Engage with the official post"
    mock_task.task_type = TaskType.LIKE
    mock_task.target_url = "https://x.com/monad_xyz/status/1888888888"
    mock_task.reward_per_user = 25
    mock_task.total_reward_pool = 250
    mock_task.distributed_reward = 0
    mock_task.max_approvals = 10
    mock_task.approved_count = 0
    mock_task.status = TaskStatus.ACTIVE
    mock_task.ends_at = None
    mock_task.preview_platform = "X"
    mock_task.preview_author = "Monad\n   @monad_xyz"
    mock_task.preview_title = "Post on X"
    mock_task.preview_description = "Monad testnet is now open for public developers!"
    mock_task.preview_image_url = "https://pbs.twimg.com/media/banner.jpg"
    mock_task.preview_status = "SUCCESS"
    mock_task.preview_source = "fxtwitter"
    mock_task.preview_author_override = None
    mock_task.preview_text_override = None
    mock_task.preview_title_override = None
    mock_task.preview_image_override = None

    embed = build_task_announcement_embed(mock_task)

    # 1. Raw target URL MUST NOT be in the embed description or fields
    assert mock_task.target_url not in embed.description
    for f in embed.fields:
        assert mock_task.target_url not in f.value

    # 2. Luxury social post section must be present with real post content
    assert "𝕏" in embed.description
    assert "Monad" in embed.description
    assert "@monad_xyz" in embed.description

    # 3. Compact reward strip present, ugly system status removed
    assert "💎 **25 OBX**" in embed.description
    assert "ACTIVE" not in embed.description
    assert "💎 **REWARD**" not in embed.description
    assert "⏳ **TIME REMAINING**" not in embed.description
    assert "👥 **SPOTS**" not in embed.description

    # 4. Image preview is attached
    assert embed.image.url == "https://pbs.twimg.com/media/banner.jpg"

    # 5. Action button contains the URL cleanly
    view = TaskAnnouncementCardView(
        task_id=str(mock_task.id),
        is_active=True,
        target_url=mock_task.target_url,
    )
    link_buttons = [b for b in view.children if getattr(b, "url", None)]
    assert len(link_buttons) == 1
    assert link_buttons[0].label.upper() == "OPEN TASK"
    assert link_buttons[0].url == "https://x.com/monad_xyz/status/1888888888"


def test_task_embed_respects_admin_preview_overrides():
    mock_task = MagicMock(spec=Task)
    mock_task.id = "task-uuid-2"
    mock_task.title = "Custom Raid Mission"
    mock_task.description = "Like and reply"
    mock_task.task_type = TaskType.LIKE
    mock_task.target_url = "https://x.com/custom_account/status/112233"
    mock_task.reward_per_user = 50
    mock_task.total_reward_pool = 500
    mock_task.distributed_reward = 0
    mock_task.max_approvals = 10
    mock_task.approved_count = 0
    mock_task.status = TaskStatus.ACTIVE
    mock_task.ends_at = None
    mock_task.preview_platform = "X"
    # Scraped auto values
    mock_task.preview_author = "@custom_account"
    mock_task.preview_description = "Auto scraped text"
    mock_task.preview_image_url = "https://example.com/auto.png"
    mock_task.preview_status = "SUCCESS"
    # Admin overrides (must take highest precedence)
    mock_task.preview_author_override = "OBX Founder\n   @obx_founder"
    mock_task.preview_text_override = "Manual verified announcement override text."
    mock_task.preview_image_override = "https://example.com/manual_override.png"

    embed = build_task_announcement_embed(mock_task)

    assert "OBX Founder" in embed.description
    assert "@obx_founder" in embed.description
    assert "Manual verified announcement override text." in embed.description
    assert "Auto scraped text" not in embed.description
    assert embed.image.url == "https://example.com/manual_override.png"


def test_preview_box_renders_unavailable_when_all_fail():
    mock_task = MagicMock(spec=Task)
    mock_task.id = "task-uuid-empty"
    mock_task.title = "Fall Back Mission"
    mock_task.description = "Follow instructions to earn rewards"
    mock_task.task_type = "CUSTOM_TASK"
    mock_task.target_url = "https://x.com/fallback_user/status/999"
    mock_task.reward_per_user = 10
    mock_task.total_reward_pool = 100
    mock_task.distributed_reward = 0
    mock_task.max_approvals = 10
    mock_task.approved_count = 0
    mock_task.status = TaskStatus.ACTIVE
    mock_task.ends_at = None
    mock_task.preview_platform = "X"
    mock_task.preview_author = "@fallback_user"
    mock_task.preview_title = None
    mock_task.preview_description = None
    mock_task.preview_image_url = None
    mock_task.preview_status = "FAILED"
    mock_task.preview_author_override = None
    mock_task.preview_text_override = None
    mock_task.preview_image_override = None

    embed = build_task_announcement_embed(mock_task)

    assert "PREVIEW UNAVAILABLE" not in embed.description
    assert "@fallback_user" in embed.description
    assert '"Follow instructions to earn rewards"' not in embed.description


@pytest.mark.asyncio
async def test_announce_task_single_message_only_with_preview(db_session):
    ch_service = ChannelService(db_session)
    ch_service.update_guild_channel("guild_prev_test", "tasks", "12345", "admin")

    mock_task = MagicMock(spec=Task)
    mock_task.id = "00000000-0000-0000-0000-000000000001"
    mock_task.title = "Website Grant Mission"
    mock_task.description = "Read our latest announcement"
    mock_task.task_type = "CUSTOM_TASK"
    mock_task.target_url = "https://ethereum.org/en/roadmap/"
    mock_task.reward_per_user = 10
    mock_task.total_reward_pool = 100
    mock_task.distributed_reward = 0
    mock_task.status = TaskStatus.ACTIVE
    mock_task.ends_at = None
    mock_task.max_approvals = 10
    mock_task.approved_count = 0
    mock_task.preview_platform = "Ethereum"
    mock_task.preview_author = "Ethereum Foundation"
    mock_task.preview_title = "Ethereum Roadmap"
    mock_task.preview_description = "The future of scaling"
    mock_task.preview_image_url = None
    mock_task.preview_fetched_at = None
    mock_task.preview_status = "SUCCESS"
    mock_task.preview_author_override = None
    mock_task.preview_text_override = None
    mock_task.preview_image_override = None

    mock_guild = MagicMock(spec=discord.Guild, id="guild_prev_test")
    mock_ch = MagicMock(spec=discord.TextChannel, id=12345, name="1-tasks")
    mock_msg = MagicMock(id=888999)
    mock_ch.send = AsyncMock(return_value=mock_msg)
    mock_ch.permissions_for.return_value = MagicMock(view_channel=True, send_messages=True, embed_links=True)
    mock_guild.get_channel.return_value = mock_ch

    mock_bot = MagicMock(spec=discord.Client)

    with patch("apps.obx_tasks.bot.announcement_service.session_scope", lambda: mock_session_scope_for(db_session)), \
         patch("apps.obx_tasks.services.url_preview_service.UrlPreviewService.fetch_preview", new_callable=AsyncMock) as mock_fetch:
        
        from apps.obx_tasks.services.url_preview_service import URLPreviewMetadata
        mock_fetch.return_value = URLPreviewMetadata(
            platform="Web",
            author="Ethereum Foundation",
            title="Ethereum Roadmap",
            description="The future of scaling",
            status="SUCCESS",
            source="opengraph",
        )
        ok, msg = await announce_task(mock_task, mock_guild, mock_bot)

    assert ok is True
    # Strictly ONE send call (never two messages)
    assert mock_ch.send.call_count == 1
    send_args = mock_ch.send.call_args[1]
    embed = send_args["embed"]
    assert mock_task.target_url not in embed.description
    assert "Ethereum Foundation" in embed.description
    assert "The future of scaling" in embed.description
