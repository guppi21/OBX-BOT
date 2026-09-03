import pytest
from unittest.mock import patch, MagicMock
from apps.obx_tasks.services.url_preview_service import (
    is_safe_url,
    UrlPreviewService,
    URLPreviewMetadata,
    _OGParser,
)

def test_ssrf_blocks_unsafe_urls():
    blocked_cases = [
        "http://localhost",
        "http://localhost:8080/secret",
        "http://127.0.0.1/admin",
        "http://127.0.0.1:5432",
        "http://10.0.0.1/status",
        "http://172.16.0.1/internal",
        "http://192.168.1.1/router",
        "http://169.254.169.254/latest/meta-data/",
        "http://metadata.google.internal/computeMetadata/v1/",
        "file:///etc/passwd",
        "ftp://example.com/files",
        "javascript:alert(1)",
        "",
        "   ",
    ]

    for url in blocked_cases:
        safe, reason = is_safe_url(url)
        assert not safe, f"Expected {url} to be blocked, but was allowed: {reason}"


def test_platform_detection():
    cases = [
        ("https://x.com/GUPPI_ETH/status/189000000000", "X"),
        ("https://twitter.com/obx_tasks/status/999", "X"),
        ("https://www.youtube.com/watch?v=dQw4w9WgXcQ", "YouTube"),
        ("https://youtu.be/dQw4w9WgXcQ", "YouTube"),
        ("https://discord.gg/obxcommunity", "Discord"),
        ("https://discord.com/invite/obxcommunity", "Discord"),
        ("https://ethereum.org/en/developers/", "Web"),
    ]

    for url, expected_plat in cases:
        plat = UrlPreviewService.detect_platform(url)
        assert plat == expected_plat, f"Expected {expected_plat} for {url}, got {plat}"


def test_x_fallback_metadata_returns_failed_status_and_no_fake_instructions():
    url = "https://x.com/monad_xyz/status/1888888888"
    meta = UrlPreviewService._fallback_metadata(url)
    assert meta.platform == "X"
    assert meta.handle == "@monad_xyz"
    assert meta.status == "FAILED"
    assert meta.source == "failed"
    assert meta.description is None


def test_og_parser_extracts_tags():
    sample_html = """
    <!DOCTYPE html>
    <html>
    <head>
        <meta property="og:title" content="Monad Developer Grants" />
        <meta property="og:description" content="Apply for ecosystem funding to build on Monad." />
        <meta property="og:image" content="https://monad.xyz/assets/banner.png" />
        <meta property="og:site_name" content="Monad Foundation" />
        <title>Ignored regular title</title>
    </head>
    <body><h1>Hello</h1></body>
    </html>
    """
    parser = _OGParser()
    parser.feed(sample_html)
    assert parser.title == "Monad Developer Grants"
    assert parser.description == "Apply for ecosystem funding to build on Monad."
    assert parser.image_url == "https://monad.xyz/assets/banner.png"
    assert parser.site_name == "Monad Foundation"


@pytest.mark.asyncio
async def test_fetch_preview_handles_exceptions_and_returns_fallback():
    with patch("apps.obx_tasks.services.url_preview_service.UrlPreviewService._sync_fetch_preview") as mock_sync:
        mock_sync.side_effect = Exception("Network connection timeout")
        meta = await UrlPreviewService.fetch_preview("https://x.com/vitalikbuterin/status/12345")
        assert meta.platform == "X"
        assert meta.handle == "@vitalikbuterin"
        assert meta.status == "FAILED"
        assert meta.description is None


@pytest.mark.asyncio
async def test_fxtwitter_parsing_extracts_author_handle_text_and_photo():
    mock_fx_payload = b'''{
        "code": 200,
        "message": "OK",
        "tweet": {
            "author": {
                "name": "BaconCheese",
                "screen_name": "BaconCheese21"
            },
            "text": "The actual text from the X post should appear here as a clean preview, not an empty box.",
            "media": {
                "photos": [
                    {"url": "https://pbs.twimg.com/media/tweet_photo.jpg"}
                ]
            }
        }
    }'''
    with patch("apps.obx_tasks.services.url_preview_service._safe_fetch_bytes", return_value=mock_fx_payload):
        meta = await UrlPreviewService.fetch_preview("https://x.com/BaconCheese21/status/1888000000")
        assert meta.platform == "X"
        assert meta.author == "BaconCheese"
        assert meta.handle == "@BaconCheese21"
        assert "The actual text from the X post" in meta.description
        assert meta.image_url == "https://pbs.twimg.com/media/tweet_photo.jpg"
