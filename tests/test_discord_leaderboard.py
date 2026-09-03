import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from contextlib import contextmanager
import discord

from apps.obx_core.services.wallet_service import WalletService
from apps.obx_tasks.services.task_service import TaskService
from apps.obx_tasks.bot.leaderboard_views import (
    LeaderboardView,
    build_leaderboard_embed,
    handle_leaderboard,
)
from apps.obx_tasks.services.leaderboard_service import (
    LeaderboardService,
    LeaderboardCategory,
    LeaderboardPeriod,
    LeaderboardEntry,
    UserLeaderboardPosition,
)


@contextmanager
def mock_session_scope_for(db_session):
    yield db_session


def test_leaderboard_view_only_has_prev_next():
    """LeaderboardView should only contain PREVIOUS and NEXT buttons."""
    view = LeaderboardView()
    assert len(view.children) == 2

    custom_ids = [b.custom_id for b in view.children]
    assert "obx:lb:prev" in custom_ids
    assert "obx:lb:next" in custom_ids

    # All old tabs, filters, and refresh must be gone
    assert "obx:lb:wealth" not in custom_ids
    assert "obx:lb:earnings" not in custom_ids
    assert "obx:lb:completions" not in custom_ids
    assert "obx:lb:all_time" not in custom_ids
    assert "obx:lb:this_month" not in custom_ids
    assert "obx:lb:this_week" not in custom_ids
    assert "obx:lb:refresh" not in custom_ids
    assert "obx:lb:activity" not in custom_ids
    assert "obx:lb:home" not in custom_ids


def test_leaderboard_embed_shows_user_balance_and_rank():
    """Embed must show user balance and rank at the top."""
    user_pos = UserLeaderboardPosition(
        discord_user_id="12345",
        rank=3,
        score=500,
        total_balance=500,
        task_earnings=200,
        tasks_completed=4,
        total_participants=25,
    )
    entries = [
        LeaderboardEntry(rank=1, discord_user_id="u1", score=1000, total_balance=1000),
        LeaderboardEntry(rank=2, discord_user_id="u2", score=750, total_balance=750),
        LeaderboardEntry(rank=3, discord_user_id="12345", score=500, total_balance=500),
    ]
    embed = build_leaderboard_embed(
        entries=entries,
        total_count=25,
        user_position=user_pos,
        page=0,
        page_size=10,
    )
    assert embed.title == "🏆 OBX LEADERBOARD"
    assert "500 OBX" in embed.description
    assert "🏆 #3" in embed.description
    assert "TOP RAIDERS" in embed.description
    # Should not contain old clutter
    assert "Authoritative Ledger Verified" not in (embed.footer.text if embed.footer else "")
    assert "Live Sync" not in (embed.footer.text if embed.footer else "")


def test_leaderboard_embed_unranked_user():
    """User with no rank shows as Unranked."""
    user_pos = UserLeaderboardPosition(
        discord_user_id="new_user",
        rank=None,
        score=0,
        total_balance=0,
        task_earnings=0,
        tasks_completed=0,
        total_participants=5,
    )
    embed = build_leaderboard_embed(
        entries=[],
        total_count=0,
        user_position=user_pos,
        page=0,
        page_size=10,
    )
    assert "Unranked" in embed.description
    assert "0 OBX" in embed.description


def test_leaderboard_embed_no_user_position():
    """Public leaderboard (no user context) should still render."""
    embed = build_leaderboard_embed(
        entries=[],
        total_count=0,
        user_position=None,
        page=0,
        page_size=10,
    )
    assert embed.title == "🏆 OBX LEADERBOARD"
    assert "TOP RAIDERS" in embed.description


def test_leaderboard_embed_medal_display():
    """Top 3 should show medals, rest show numbers."""
    entries = [
        LeaderboardEntry(rank=i, discord_user_id=f"u{i}", score=100 - i * 10, total_balance=100 - i * 10)
        for i in range(1, 6)
    ]
    embed = build_leaderboard_embed(
        entries=entries,
        total_count=5,
        user_position=None,
        page=0,
        page_size=10,
    )
    assert "🥇" in embed.description
    assert "🥈" in embed.description
    assert "🥉" in embed.description
    assert "4." in embed.description
    assert "5." in embed.description


def test_leaderboard_embed_no_extra_fields():
    """Embed should use description only, not fields."""
    user_pos = UserLeaderboardPosition(
        discord_user_id="u1", rank=1, score=100, total_balance=100,
        task_earnings=50, tasks_completed=2, total_participants=10,
    )
    entries = [LeaderboardEntry(rank=1, discord_user_id="u1", score=100, total_balance=100)]
    embed = build_leaderboard_embed(
        entries=entries,
        total_count=1,
        user_position=user_pos,
        page=0,
        page_size=10,
    )
    # No embed fields — everything is in description
    assert len(embed.fields) == 0


@pytest.mark.asyncio
async def test_leaderboard_pagination_buttons(db_session):
    """Previous disabled on page 0, Next disabled on last page."""
    ws = WalletService(db_session)
    ws.get_or_create_user("lb_pag_user")
    ws.credit(discord_user_id="lb_pag_user", amount=250, reference_type="test", idempotency_key="lb_pag1")

    view = LeaderboardView(page=0)

    mock_interaction = MagicMock(spec=discord.Interaction)
    mock_interaction.user = MagicMock(id="lb_pag_user")
    mock_interaction.response = AsyncMock()
    mock_interaction.followup = AsyncMock()

    with patch("apps.obx_tasks.bot.leaderboard_views.session_scope", lambda: mock_session_scope_for(db_session)):
        await view.update_view(mock_interaction)

    # On page 0 with small data: Previous disabled, Next disabled (only 1 page)
    assert view.btn_prev.disabled is True
    assert view.btn_next.disabled is True
