import inspect
import discord
from discord.ui import View, Button, ChannelSelect
from typing import Optional, List, Dict, Any

from packages.database.session import session_scope
from packages.database.models.auction import Auction, AuctionBid, AuctionClaim
from packages.shared.enums import AuctionStatus
from apps.obx_tasks.services.channel_service import ChannelService
from apps.obx_tasks.services.auction_service import AuctionService
from apps.obx_tasks.bot.permissions import is_admin
from apps.obx_tasks.bot.ui_theme import COLOR_GOLD, COLOR_TEAL, COLOR_GREEN, COLOR_BLUE, COLOR_DARK, COLOR_RED, COLOR_PURPLE
from packages.shared.logging import get_logger

logger = get_logger("obx.tasks.bot.channel_views")


def _is_response_done(interaction: discord.Interaction) -> bool:
    if not hasattr(interaction, "response") or interaction.response is None:
        return False
    is_done_fn = getattr(interaction.response, "is_done", None)
    if is_done_fn is None:
        return False
    res = is_done_fn() if callable(is_done_fn) else is_done_fn
    if inspect.iscoroutine(res):
        res.close()
        return False
    return bool(res)


def build_channel_config_embed(guild: discord.Guild, config: Any) -> discord.Embed:
    def format_ch(ch_id: Optional[str]) -> str:
        if not ch_id:
            return "`Not configured`"
        ch = guild.get_channel(int(ch_id))
        return ch.mention if ch else f"`#{ch_id} (Missing)`"

    embed = discord.Embed(
        title="⚙️ OBX CHANNEL ROUTING CONFIGURATION",
        description=(
            "Assign dedicated Discord channels for each feature of the OBX ecosystem.\n"
            "Public dashboards and announcements will automatically route to their assigned locations.\n"
        ),
        color=COLOR_BLUE,
    )

    embed.add_field(name="🎯 Tasks Channel", value=format_ch(config.tasks_channel_id), inline=True)
    embed.add_field(name="🏆 Leaderboard Channel", value=format_ch(config.leaderboard_channel_id), inline=True)
    embed.add_field(name="🔨 Auctions Channel", value=format_ch(config.auctions_channel_id), inline=True)
    embed.add_field(name="🏅 Winners Channel", value=format_ch(config.winners_channel_id), inline=True)
    embed.add_field(name="🔒 Admin Channel", value=format_ch(config.admin_channel_id), inline=True)
    embed.add_field(name="💰 Economy Activity", value=format_ch(config.economy_channel_id), inline=True)

    embed.set_footer(text="Changes persist across bot restarts and deployments • Admin Only")
    return embed


class FeatureChannelSelect(ChannelSelect):
    """Subclassed ChannelSelect for explicit callback handling."""
    def __init__(self, channel_key: str, display_name: str):
        super().__init__(
            channel_types=[discord.ChannelType.text],
            placeholder=f"Choose channel for {display_name}...",
            min_values=1,
            max_values=1,
        )
        self.channel_key = channel_key
        self.display_name = display_name

    async def callback(self, interaction: discord.Interaction):
        if not is_admin(interaction):
            await interaction.response.send_message("❌ Permission Denied: Administrator role required.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)
        selected_ch = self.values[0]
        if interaction.guild:
            resolved_ch = interaction.guild.get_channel(selected_ch.id)
            if resolved_ch:
                selected_ch = resolved_ch

        from apps.obx_tasks.bot.announcement_service import (
            check_channel_permissions, deploy_or_update_task_center, deploy_or_update_leaderboard,
            deploy_or_update_auction_center, deploy_or_update_admin_hub
        )

        me = interaction.guild.me or interaction.guild.get_member(interaction.client.user.id)
        if me:
            valid, missing = check_channel_permissions(selected_ch, me)
            if not valid:
                embed = discord.Embed(
                    title="⚠️ CHANNEL ACCESS REQUIRED",
                    description=(
                        f"I cannot fully operate in {selected_ch.mention}.\n\n"
                        f"Please grant the following bot permissions:\n"
                        + "\n".join([f"• **{m}**" for m in missing])
                    ),
                    color=COLOR_RED,
                )
                await interaction.followup.send(embed=embed, ephemeral=True)
                return

        # Save to database
        with session_scope() as session:
            service = ChannelService(session)
            service.update_guild_channel(
                guild_id=str(interaction.guild.id),
                channel_key=self.channel_key,
                channel_id=str(selected_ch.id),
                updated_by=str(interaction.user.id),
            )

        # Trigger auto-deployment
        deploy_msg = ""
        if self.channel_key == "tasks":
            ok, msg = await deploy_or_update_task_center(interaction.guild, interaction.client)
            deploy_msg = f"\n\n{msg}"
        elif self.channel_key == "leaderboard":
            ok, msg = await deploy_or_update_leaderboard(interaction.guild, interaction.client)
            deploy_msg = f"\n\n{msg}"
        elif self.channel_key == "auctions":
            ok, msg = await deploy_or_update_auction_center(interaction.guild, interaction.client)
            deploy_msg = f"\n\n{msg}"
        elif self.channel_key == "admin":
            ok, msg = await deploy_or_update_admin_hub(interaction.guild, interaction.client)
            deploy_msg = f"\n\n{msg}"

        embed = discord.Embed(
            title="✅ CHANNEL CONFIGURED",
            description=(
                f"**{self.display_name}** has been successfully assigned to {selected_ch.mention}."
                f"{deploy_msg}"
            ),
            color=COLOR_GREEN,
        )
        await interaction.followup.send(embed=embed, ephemeral=True)


class ChannelSelectPromptView(View):
    """View containing Discord's native ChannelSelect component for choosing a channel."""
    def __init__(self, channel_key: str, display_name: str):
        super().__init__(timeout=120)
        self.select_item = FeatureChannelSelect(channel_key=channel_key, display_name=display_name)
        self.add_item(self.select_item)


class ChannelConfigView(View):
    """Main administrative channel configuration dashboard."""
    def __init__(self):
        super().__init__(timeout=300)

    @discord.ui.button(label="Tasks Channel", style=discord.ButtonStyle.primary, row=0)
    async def btn_tasks(self, interaction: discord.Interaction, button: Button):
        view = ChannelSelectPromptView(channel_key="tasks", display_name="🎯 Tasks Channel")
        await interaction.response.send_message("Select destination channel for the **OBX Task Center**:", view=view, ephemeral=True)

    @discord.ui.button(label="Leaderboard Channel", style=discord.ButtonStyle.primary, row=0)
    async def btn_lb(self, interaction: discord.Interaction, button: Button):
        view = ChannelSelectPromptView(channel_key="leaderboard", display_name="🏆 Leaderboard Channel")
        await interaction.response.send_message("Select destination channel for the **Public Leaderboard**:", view=view, ephemeral=True)

    @discord.ui.button(label="Auctions Channel", style=discord.ButtonStyle.primary, row=0)
    async def btn_auc(self, interaction: discord.Interaction, button: Button):
        view = ChannelSelectPromptView(channel_key="auctions", display_name="🔨 Auctions Channel")
        await interaction.response.send_message("Select destination channel for **Active Whitelist Auctions**:", view=view, ephemeral=True)

    @discord.ui.button(label="Winners Channel", style=discord.ButtonStyle.secondary, row=1)
    async def btn_winners(self, interaction: discord.Interaction, button: Button):
        view = ChannelSelectPromptView(channel_key="winners", display_name="🏅 Winners Channel")
        await interaction.response.send_message("Select destination channel for **Auction Winner Announcements**:", view=view, ephemeral=True)

    @discord.ui.button(label="Admin Channel", style=discord.ButtonStyle.secondary, row=1)
    async def btn_admin(self, interaction: discord.Interaction, button: Button):
        view = ChannelSelectPromptView(channel_key="admin", display_name="🔒 Admin Channel")
        await interaction.response.send_message("Select destination channel for **Administrative Alerts**:", view=view, ephemeral=True)

    @discord.ui.button(label="Economy Channel", style=discord.ButtonStyle.secondary, row=1)
    async def btn_econ(self, interaction: discord.Interaction, button: Button):
        view = ChannelSelectPromptView(channel_key="economy", display_name="💰 Economy Activity Channel")
        await interaction.response.send_message("Select destination channel for **Optional Economy Activity**:", view=view, ephemeral=True)

    @discord.ui.button(label="Auto-Detect", emoji="🔍", style=discord.ButtonStyle.success, row=2)
    async def btn_auto_detect(self, interaction: discord.Interaction, button: Button):
        if not is_admin(interaction):
            await interaction.response.send_message("❌ Permission Denied: Administrator role required.", ephemeral=True)
            return

        if not interaction.guild:
            await interaction.response.send_message("❌ Must be run inside a server.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)
        try:
            with session_scope() as session:
                service = ChannelService(session)
                discovered = service.auto_discover_guild_channels(interaction.guild, overwrite=True)
                config = service.get_or_create_guild_config(str(interaction.guild.id))

            embed = build_channel_config_embed(interaction.guild, config)
            if discovered:
                summary = "🔍 **Auto-Detected Mappings:**\n" + "\n".join([f"• **{k.capitalize()}**: `{v}`" for k, v in discovered.items()])
            else:
                summary = "🔍 Channels verified against server layout."
            embed.description = f"{summary}\n\n{embed.description}"
            await interaction.followup.send(embed=embed, ephemeral=True)
        except Exception as exc:
            logger.error("Error in auto-detect button: %s", exc)
            await interaction.followup.send(f"❌ Error auto-detecting channels: {exc}", ephemeral=True)

    @discord.ui.button(label="Refresh", style=discord.ButtonStyle.secondary, row=2)
    async def btn_refresh(self, interaction: discord.Interaction, button: Button):
        await handle_channel_config(interaction)

    @discord.ui.button(label="Back to Admin Hub", style=discord.ButtonStyle.secondary, row=2)
    async def btn_back(self, interaction: discord.Interaction, button: Button):
        from apps.obx_tasks.bot.dashboard_views import handle_home
        await handle_home(interaction)


class AuctionWinnerResultView(View):
    """Persistent view attached to public winner announcement cards."""
    def __init__(self, auction_id: Optional[str] = None):
        super().__init__(timeout=None)
        self.auction_id = auction_id

    @discord.ui.button(label="View My Result", style=discord.ButtonStyle.primary, custom_id="obx:auc:win_result", row=0)
    async def btn_my_result(self, interaction: discord.Interaction, button: Button):
        await interaction.response.defer(ephemeral=True)

        try:
            with session_scope() as session:
                service = AuctionService(session)
                bids = (
                    session.query(AuctionBid)
                    .filter_by(discord_user_id=str(interaction.user.id))
                    .join(Auction)
                    .filter(Auction.status == AuctionStatus.COMPLETED)
                    .order_by(Auction.updated_at.desc())
                    .all()
                )

                if not bids:
                    await interaction.followup.send("ℹ️ You did not place a bid in this auction.", ephemeral=True)
                    return

                target_bid = bids[0]
                if self.auction_id:
                    for b in bids:
                        if str(b.auction_id) == self.auction_id:
                            target_bid = b
                            break

                auc = target_bid.auction
                if target_bid.is_winner:
                    embed = discord.Embed(
                        title="🎉 YOU WON THE WHITELIST!",
                        description=(
                            f"Congratulations! You secured a guaranteed whitelist spot for **{auc.title} — {auc.reward_title}**!\n\n"
                            f"💎 **Winning Bid Amount:** `{target_bid.bid_amount:,} OBX`\n"
                            f"🎟 **Reward Status:** Confirmed Whitelist Pass\n\n"
                            "Your winning bid has been settled through your OBX vault."
                        ),
                        color=COLOR_GREEN,
                    )
                else:
                    embed = discord.Embed(
                        title="🏁 AUCTION RESULT: WL Spot Not Secured",
                        description=(
                            f"The bidding for **{auc.title} — {auc.reward_title}** has concluded.\n\n"
                            f"💎 **Your Submitted Bid:** `{target_bid.bid_amount:,} OBX`\n"
                            f"🔓 **Refund Status:** 100% Full Refund Complete\n\n"
                            f"Your `{target_bid.bid_amount:,} OBX` has been unlocked and returned to your available balance."
                        ),
                        color=COLOR_DARK,
                    )

                embed.set_footer(text="Double-Entry Verified • Zero Lost Balances")
                await interaction.followup.send(embed=embed, ephemeral=True)
        except Exception as exc:
            logger.error("Error retrieving personal auction result: %s", exc)
            await interaction.followup.send("❌ Error fetching your personal auction result.", ephemeral=True)


async def handle_channel_config(interaction: discord.Interaction):
    """Entry point for the admin channel configuration hub."""
    if not is_admin(interaction):
        await interaction.response.send_message("❌ Permission Denied: Administrator role required.", ephemeral=True)
        return

    if not _is_response_done(interaction):
        await interaction.response.defer(ephemeral=True)

    with session_scope() as session:
        service = ChannelService(session)
        config = service.get_or_create_guild_config(str(interaction.guild.id))

    embed = build_channel_config_embed(interaction.guild, config)
    view = ChannelConfigView()

    await interaction.followup.send(embed=embed, view=view, ephemeral=True)
