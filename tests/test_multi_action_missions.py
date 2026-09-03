import pytest
from unittest.mock import MagicMock, AsyncMock, patch
import discord

from packages.shared.enums import TaskStatus, TaskType
from packages.database.models.task import Task
from apps.obx_tasks.bot.announcement_service import (
    build_task_announcement_embed,
    TaskAnnouncementCardView,
    announce_task,
)
from apps.obx_tasks.services.task_service import TaskService


def create_mock_task(
    task_type="LIKE",
    required_actions=None,
    title="Special Partner Campaign",
    description="Like and repost the official announcement",
    target_url="https://x.com/BaconCheese21/status/987654321",
    reward=10,
    pool=100,
    status=TaskStatus.ACTIVE,
    preview_author=None,
    preview_description=None,
    preview_image_url=None,
    preview_status=None,
    preview_author_override=None,
    preview_text_override=None,
    preview_image_override=None,
) -> MagicMock:
    t = MagicMock(spec=Task)
    t.id = "00000000-0000-0000-0000-000000000001"
    t.title = title
    t.description = description
    t.task_type = task_type
    t.required_actions = required_actions
    t.target_url = target_url
    t.platform = "X"
    t.reward_per_user = reward
    t.total_reward_pool = pool
    t.distributed_reward = 0
    t.approved_count = 0
    t.max_approvals = 10
    t.status = status
    t.ends_at = None
    t.preview_platform = "X"
    t.preview_author = preview_author
    t.preview_description = preview_description
    t.preview_image_url = preview_image_url
    t.preview_status = preview_status
    t.preview_author_override = preview_author_override
    t.preview_text_override = preview_text_override
    t.preview_image_override = preview_image_override
    return t


def test_multi_action_two_actions_headline_and_objectives():
    task = create_mock_task(
        task_type="MULTI_ACTION",
        required_actions="LIKE,RETWEET",
        title="Community Engagement Raid",
    )
    embed = build_task_announcement_embed(task)

    # 1. No large headers
    assert embed.title is None
    # 2. Mini instruction generates concise raid sentence
    assert "📝 Like the post and retweet it to complete this raid." in embed.description


def test_multi_action_three_actions_headline_community_raid():
    task = create_mock_task(
        task_type="MULTI_ACTION",
        required_actions="LIKE,RETWEET,FOLLOW",
        title="Triple Ecosystem Blitz",
    )
    embed = build_task_announcement_embed(task)

    # 1. No large headers
    assert embed.title is None
    # 2. Mini instruction generates concise raid sentence with 📝
    assert "📝" in embed.description
    assert "complete this raid" in embed.description


def test_zero_unavailable_fallback_on_scrape_failure():
    """If live metadata extraction fails completely, the card must NEVER say
    '𝕏 POST PREVIEW UNAVAILABLE'. It renders a clean fallback without faking tweet text.
    """
    task = create_mock_task(
        task_type="LIKE",
        title="Sunset Appreciation Mission",
        description="Complete the instructions below and submit proof.",
        preview_author="BaconCheese21",
        preview_description=None,
        preview_status="FAILED",
    )
    embed = build_task_announcement_embed(task)

    # Must NOT contain ugly error text
    assert "PREVIEW UNAVAILABLE" not in embed.description

    # Must contain clean post preview with handle and fallback
    assert "𝕏" in embed.description
    assert "@BaconCheese21" in embed.description

    # Task instructions must NEVER be in quotes as fake tweet text
    assert '"Complete the instructions below and submit proof."' not in embed.description


def test_regression_task_instructions_never_appear_in_x_preview_section():
    """Regression test requested by user:
    Given:
      task.instructions = 'Like the target post on X and click Verify Completion.'
      Actual X preview text = 'Monad is launching its new campaign today.'
    Expected public preview = 'Monad is launching its new campaign today.'
    Forbidden output = 'Like the target post on X and click Verify Completion.'

    Assertion: task.instructions never appears inside the X preview section
    unless an explicit manual preview override was configured.
    """
    task = create_mock_task(
        task_type="LIKE",
        title="Ecosystem Campaign",
        description="Like the target post on X and click Verify Completion.",
        preview_author="Monad (@monad_xyz)",
        preview_description="Monad is launching its new campaign today.",
        preview_status="SUCCESS",
    )
    embed = build_task_announcement_embed(task)

    # The actual X preview text MUST be displayed in quote block
    assert "Monad is launching its new campaign today." in embed.description
    assert "> *“Monad is launching its new campaign today.”*" in embed.description
    # The task instruction MUST NOT be in the quote box
    assert "> *“Like the target post on X and click Verify Completion.”*" not in embed.description


def test_real_post_text_rendered_when_available():
    task = create_mock_task(
        task_type="LIKE",
        title="Grand Teton Sunset",
        preview_author="U.S. Department of the Interior\n   @Interior",
        preview_description="Sunsets don't get much better than this one over @GrandTetonNPS . #nature #sunset",
        preview_status="SUCCESS",
    )
    embed = build_task_announcement_embed(task)

    assert "𝕏" in embed.description
    assert "U.S. Department of the Interior" in embed.description
    assert "@Interior" in embed.description
    assert "Sunsets don't get much better than this one" in embed.description


def test_hero_media_image_attachment():
    """Extracted or overridden photo must be set as embed hero image."""
    task = create_mock_task(
        preview_image_url="https://pbs.twimg.com/media/test_hero_image.jpg",
    )
    embed = build_task_announcement_embed(task)
    assert embed.image.url == "https://pbs.twimg.com/media/test_hero_image.jpg"


def test_admin_preview_override_takes_highest_priority():
    task = create_mock_task(
        preview_author="Original Scraped Author",
        preview_description="Original scraped text",
        preview_image_url="https://example.com/scraped.png",
        preview_author_override="Manual Admin Author (@ManualHandle)",
        preview_text_override="This is manually curated text that overrides scraping.",
        preview_image_override="https://example.com/admin_hero.png",
    )
    embed = build_task_announcement_embed(task)

    assert "Manual Admin Author" in embed.description
    assert "This is manually curated text that overrides scraping." in embed.description
    assert "Original Scraped Author" not in embed.description
    assert embed.image.url == "https://example.com/admin_hero.png"


def test_target_url_only_in_button_not_in_description():
    target_url = "https://x.com/secret_account/status/123456"
    task = create_mock_task(target_url=target_url)
    embed = build_task_announcement_embed(task)
    view = TaskAnnouncementCardView(task_id=task.id, is_active=True, target_url=target_url)

    # URL must not be in embed description
    assert target_url not in embed.description

    # URL must be in Open Task link button
    open_btns = [b for b in view.children if getattr(b, "url", None) == target_url]
    assert len(open_btns) == 1
    assert open_btns[0].label.upper() == "OPEN TASK"


def test_zero_operational_status_in_embed():
    """Ensure no '🟢 ACTIVE' or database status appears in embed."""
    task = create_mock_task(status=TaskStatus.ACTIVE)
    embed = build_task_announcement_embed(task)

    assert embed.title is None
    assert "ACTIVE" not in embed.description
