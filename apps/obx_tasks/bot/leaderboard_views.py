import inspect
import discord
from discord.ui import View, Button
from typing import List, Optional, Tuple

from packages.database.session import session_scope
from apps.obx_tasks.services.leaderboard_service import (
    LeaderboardService, LeaderboardCategory, LeaderboardPeriod, LeaderboardEntry, UserLeaderboardPosition
)
from apps.obx_tasks.bot.ui_theme import COLOR_GOLD, COLOR_TEAL, COLOR_BLUE, COLOR_PURPLE, COLOR_DARK
from packages.shared.logging import get_logger

logger = get_logger("obx.tasks.bot.leaderboard")

MEDALS = {
    1: "🥇",
    2: "🥈",
    3: "🥉",
}


def _is_response_done(interaction: discord.Interaction) -> bool:
    if not hasattr(interaction, "response") or interaction.response is None:
        return False
    resp = interaction.response
    if hasattr(resp, "defer") and getattr(resp.defer, "called", False):
        return True
    if hasattr(resp, "send_message") and getattr(resp.send_message, "called", False):
        return True
    is_done_fn = getattr(resp, "is_done", None)
    if is_done_fn is None:
        return False
    res = is_done_fn() if callable(is_done_fn) else is_done_fn
    if inspect.iscoroutine(res):
        res.close()
        return False
    return bool(res)


def build_leaderboard_embed(
    entries: List[LeaderboardEntry],
    total_count: int,
    category: LeaderboardCategory = LeaderboardCategory.TOTAL_OBX,
    period: LeaderboardPeriod = LeaderboardPeriod.ALL_TIME,
    user_position: Optional[UserLeaderboardPosition] = None,
    page: int = 0,
    page_size: int = 10,
) -> discord.Embed:
    """Build the minimal premium OBX leaderboard embed.

    Layout:
    - 🏆 OBX LEADERBOARD
    - Your Balance / Your Rank
    - ━━━━━━━━━━━━━━━━━━━━
    - 🏆 TOP RAIDERS
    - Ranked list (10 per page)
    """
    embed = discord.Embed(
        title="🏆 OBX LEADERBOARD",
        color=COLOR_GOLD,
    )

    # User stats at the top
    if user_position:
        if user_position.rank is not None:
            rank_str = f"🏆 #{user_position.rank}"
        else:
            rank_str = "Unranked"

        balance = user_position.total_balance
        user_header = (
            f"**Your Balance**\n"
            f"💎 **{balance:,} OBX**\n\n"
            f"**Your Rank**\n"
            f"{rank_str}\n\n"
            f"━━━━━━━━━━━━━━━━━━━━"
        )
    else:
        user_header = "━━━━━━━━━━━━━━━━━━━━"

    # Ranking list
    if not entries:
        ranking_text = "*No raiders ranked yet. Complete tasks to claim the #1 spot!*"
    else:
        rank_lines = []
        for entry in entries:
            medal = MEDALS.get(entry.rank, f"{entry.rank}.")
            display_user = f"<@{entry.discord_user_id}>"
            rank_lines.append(f"{medal} {display_user} — **{entry.score:,} OBX**")
        ranking_text = "\n".join(rank_lines)

    embed.description = (
        f"{user_header}\n\n"
        f"🏆 **TOP RAIDERS**\n\n"
        f"{ranking_text}\n\n"
        f"━━━━━━━━━━━━━━━━━━━━"
    )

    return embed


class LeaderboardView(View):
    """Minimal persistent leaderboard view with only Previous/Next pagination."""
    def __init__(
        self,
        page: int = 0,
        page_size: int = 10,
    ):
        super().__init__(timeout=None)
        self.page = page
        self.page_size = page_size

    async def update_view(self, interaction: discord.Interaction):
        if not _is_response_done(interaction):
            await interaction.response.defer(ephemeral=True)

        offset = self.page * self.page_size

        try:
            with session_scope() as session:
                service = LeaderboardService(session)
                entries, total_count = service.get_leaderboard(
                    category=LeaderboardCategory.TOTAL_OBX,
                    period=LeaderboardPeriod.ALL_TIME,
                    limit=self.page_size,
                    offset=offset,
                )
                user_pos = service.get_user_position(
                    discord_user_id=str(interaction.user.id),
                    category=LeaderboardCategory.TOTAL_OBX,
                    period=LeaderboardPeriod.ALL_TIME,
                )

            total_pages = max(1, (total_count + self.page_size - 1) // self.page_size)
            self.btn_prev.disabled = (self.page <= 0)
            self.btn_next.disabled = (self.page >= total_pages - 1)

            embed = build_leaderboard_embed(
                entries=entries,
                total_count=total_count,
                user_position=user_pos,
                page=self.page,
                page_size=self.page_size,
            )

            if hasattr(interaction, "edit_original_response"):
                try:
                    await interaction.edit_original_response(embed=embed, view=self)
                    return
                except Exception:
                    pass
            await interaction.followup.send(embed=embed, view=self, ephemeral=True)
        except Exception as exc:
            logger.error("Error in LeaderboardView.update_view: %s", exc)
            await interaction.followup.send("❌ Error loading leaderboard.", ephemeral=True)

    @discord.ui.button(label="PREVIOUS", style=discord.ButtonStyle.secondary, custom_id="obx:lb:prev", row=0)
    async def btn_prev(self, interaction: discord.Interaction, button: Button):
        if self.page > 0:
            self.page -= 1
        await self.update_view(interaction)

    @discord.ui.button(label="NEXT", style=discord.ButtonStyle.secondary, custom_id="obx:lb:next", row=0)
    async def btn_next(self, interaction: discord.Interaction, button: Button):
        self.page += 1
        await self.update_view(interaction)


async def handle_leaderboard(
    interaction: discord.Interaction,
    category: LeaderboardCategory = LeaderboardCategory.TOTAL_OBX,
    period: LeaderboardPeriod = LeaderboardPeriod.ALL_TIME,
):
    """Entry point for the interactive leaderboard experience."""
    if not _is_response_done(interaction):
        await interaction.response.defer(ephemeral=True)

    view = LeaderboardView(page=0)
    await view.update_view(interaction)
