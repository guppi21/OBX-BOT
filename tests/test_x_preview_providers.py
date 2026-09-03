import pytest
from unittest.mock import patch, MagicMock, AsyncMock
from apps.obx_tasks.services.url_preview_service import UrlPreviewService, URLPreviewMetadata
from apps.obx_tasks.bot.announcement_service import build_task_announcement_embed
from packages.database.models.task import Task
from packages.shared.enums import TaskStatus, TaskType

def test_provider_1_oembed_success():
    """Provider 1 (x_oembed) extracts author name and clean tweet text from oembed HTML."""
    mock_payload = b'''{
        "author_name": "Department of the Interior",
        "html": "<blockquote class=\\"twitter-tweet\\"><p lang=\\"en\\" dir=\\"ltr\\">Sunsets don&#39;t get much better than this one over @GrandTetonNPS. #nature #sunset</p>&mdash; Interior (@Interior) <a href=\\"https://twitter.com/Interior/status/463440424141459456\\">May 5, 2014</a></blockquote>"
    }'''
    with patch("apps.obx_tasks.services.url_preview_service._safe_fetch_bytes", return_value=mock_payload):
        meta = UrlPreviewService._extract_x_preview("https://x.com/Interior/status/463440424141459456")
        assert meta.source == "x_oembed"
        assert meta.status == "SUCCESS"
        assert meta.author == "Department of the Interior"
        assert meta.handle == "@Interior"
        assert "Sunsets don't get much better than this one over @GrandTetonNPS" in meta.description


def test_provider_2_fxtwitter_fallback_on_oembed_failure():
    """When oEmbed fails, Provider 2 (fxtwitter) succeeds and extracts text and photo."""
    fx_payload = b'''{
        "code": 200,
        "tweet": {
            "author": {
                "name": "BaconCheese",
                "screen_name": "BaconCheese21"
            },
            "text": "ACTUAL UNIQUE TWEET CONTENT XYZ 123",
            "media": {
                "photos": [{"url": "https://pbs.twimg.com/media/test_art.jpg"}]
            }
        }
    }'''

    def mock_fetch(url, **kwargs):
        if "publish.twitter.com" in url:
            return None  # Provider 1 fails
        if "api.fxtwitter.com" in url:
            return fx_payload  # Provider 2 succeeds
        return None

    with patch("apps.obx_tasks.services.url_preview_service._safe_fetch_bytes", side_effect=mock_fetch):
        meta = UrlPreviewService._extract_x_preview("https://x.com/BaconCheese21/status/1888000000")
        assert meta.source == "fxtwitter"
        assert meta.status == "SUCCESS"
        assert meta.author == "BaconCheese"
        assert meta.handle == "@BaconCheese21"
        assert meta.description == "ACTUAL UNIQUE TWEET CONTENT XYZ 123"
        assert meta.image_url == "https://pbs.twimg.com/media/test_art.jpg"


def test_provider_3_fixupx_og_fallback():
    """When oEmbed and FxTwitter fail, Provider 3 (fixupx_og) extracts OG description."""
    og_html = b'''<!DOCTYPE html><html><head>
        <meta property="og:title" content="CryptoDev (@CryptoDev22)" />
        <meta property="og:description" content="Decentralized infrastructure is launching tomorrow!" />
        <meta property="og:image" content="https://pbs.twimg.com/media/banner_infra.png" />
    </head><body></body></html>'''

    def mock_fetch(url, **kwargs):
        if "publish.twitter.com" in url or "api.fxtwitter.com" in url:
            return None
        if "fixupx.com" in url:
            return og_html
        return None

    with patch("apps.obx_tasks.services.url_preview_service._safe_fetch_bytes", side_effect=mock_fetch):
        meta = UrlPreviewService._extract_x_preview("https://x.com/CryptoDev22/status/999111")
        assert meta.source == "fixupx_og"
        assert meta.status == "SUCCESS"
        assert meta.author == "CryptoDev"
        assert meta.handle == "@CryptoDev22"
        assert "Decentralized infrastructure is launching tomorrow!" in meta.description
        assert meta.image_url == "https://pbs.twimg.com/media/banner_infra.png"


def test_all_providers_fail_returns_clean_failed_status_without_generic_instructions():
    """If all providers fail, status is FAILED and description is None (never fake instructions)."""
    with patch("apps.obx_tasks.services.url_preview_service._safe_fetch_bytes", return_value=None):
        meta = UrlPreviewService._extract_x_preview("https://x.com/deleted_account/status/00000")
        assert meta.source == "failed"
        assert meta.status == "FAILED"
        assert meta.description is None


def test_card_renders_real_tweet_content_and_never_fake_instructions():
    """The public announcement must display real tweet text, and NEVER generic instructions."""
    task = MagicMock(spec=Task)
    task.id = "task-uuid-real-x"
    task.title = "Spread the Word"
    task.description = "Complete the instructions below and submit proof."
    task.task_type = TaskType.LIKE
    task.target_url = "https://x.com/BaconCheese21/status/1888000000"
    task.reward_per_user = 10
    task.total_reward_pool = 100
    task.distributed_reward = 0
    task.max_approvals = 10
    task.approved_count = 0
    task.status = TaskStatus.ACTIVE
    task.ends_at = None
    task.preview_platform = "X"
    task.preview_author = "BaconCheese\n   @BaconCheese21"
    task.preview_description = "ACTUAL UNIQUE TWEET CONTENT XYZ 123"
    task.preview_image_url = None
    task.preview_source = "fxtwitter"
    task.preview_status = "SUCCESS"
    task.preview_author_override = None
    task.preview_text_override = None
    task.preview_image_override = None

    embed = build_task_announcement_embed(task)

    # 1. Must contain the real tweet text
    assert "ACTUAL UNIQUE TWEET CONTENT XYZ 123" in embed.description

    # 2. Must NOT contain generic instructions masquerading as tweet text inside the preview
    assert "𝕏" in embed.description
    assert "BaconCheese" in embed.description

    # 3. If preview is UNAVAILABLE, it must render clean fallback
    task_failed = MagicMock(spec=Task)
    task_failed.id = "task-failed"
    task_failed.title = "Failed Preview Task"
    task_failed.description = "Complete the instructions below and submit proof."
    task_failed.task_type = TaskType.LIKE
    task_failed.target_url = "https://x.com/BaconCheese21/status/1888000000"
    task_failed.reward_per_user = 10
    task_failed.total_reward_pool = 100
    task_failed.distributed_reward = 0
    task_failed.max_approvals = 10
    task_failed.approved_count = 0
    task_failed.status = TaskStatus.ACTIVE
    task_failed.ends_at = None
    task_failed.preview_platform = "X"
    task_failed.preview_author = "BaconCheese21"
    # Even if description was somehow set to task.description, it must NOT be shown as tweet text!
    task_failed.preview_description = task_failed.description
    task_failed.preview_status = "FAILED"
    task_failed.preview_source = "failed"
    task_failed.preview_image_url = None
    task_failed.preview_author_override = None
    task_failed.preview_text_override = None
    task_failed.preview_image_override = None

    failed_embed = build_task_announcement_embed(task_failed)
    # Must NOT show ugly unavailable error
    assert "PREVIEW UNAVAILABLE" not in failed_embed.description
    assert "𝕏" in failed_embed.description
    assert "@BaconCheese21" in failed_embed.description
