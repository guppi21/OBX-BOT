import inspect
import uuid
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any, Tuple
import discord
from discord.ui import View, Button, Select

from packages.database.session import session_scope
from packages.database.models.auction import Auction, AuctionBid
from packages.shared.enums import AuctionStatus, AuctionType
from packages.shared.exceptions import OBXError
from packages.shared.logging import get_logger
from apps.obx_tasks.services.auction_service import AuctionService, AuctionError
from apps.obx_tasks.bot.permissions import is_admin
from apps.obx_tasks.bot.ui_theme import (
    COLOR_GOLD, COLOR_PURPLE, COLOR_RED, COLOR_GREEN,
)
from apps.obx_tasks.bot.announcement_service import announce_auction, announce_auction_winners, send_admin_log_event

logger = get_logger("obx.tasks.bot.auction_management")


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


async def safe_edit_interaction(
    interaction: discord.Interaction,
    embed: Optional[discord.Embed] = None,
    view: Optional[View] = None,
    content: Optional[str] = None,
):
    """Safely edits an interaction response in place regardless of prior acknowledgement."""
    try:
        if _is_response_done(interaction):
            await interaction.edit_original_response(content=content, embed=embed, view=view)
        else:
            await interaction.response.edit_message(content=content, embed=embed, view=view)
    except Exception as exc:
        logger.warning("safe_edit_interaction fallback on edit: %s", exc)
        try:
            if hasattr(interaction, "followup"):
                await interaction.followup.send(content=content, embed=embed, view=view, ephemeral=True)
        except Exception:
            pass


def build_auction_browser_embed(
    auctions: List[Auction],
    status_filter: Optional[str] = None,
) -> discord.Embed:
    """Builds the Auction Browser embed."""
    filter_label = status_filter or "All"
    embed = discord.Embed(
        title=f"🔨 Auction Management — {filter_label} Auctions",
        color=COLOR_GOLD,
    )

    if not auctions:
        embed.description = (
            f"No auctions found matching status `{filter_label}`.\n\n"
            "Use the buttons below to switch filters, create a new auction, or return to the Admin Hub."
        )
        embed.set_footer(text="0 Total Auctions • OBX Admin Control")
        return embed

    lines = [
        f"Found **{len(auctions)}** auctions. Select one from the menu below to view details, edit, or cancel:\n"
    ]

    for idx, auc in enumerate(auctions[:15], start=1):
        status_icon = "🟢" if auc.status == AuctionStatus.ACTIVE else ("✅" if auc.status == AuctionStatus.COMPLETED else "🔴")
        type_str = "GTD" if auc.auction_type == AuctionType.GTD else "FCFS"
        lines.append(
            f"**{idx}. {auc.title}**\n"
            f"   {status_icon} `{auc.status.value}` • `{type_str}` • 🎟️ `{auc.total_slots} spots` • 💎 `{auc.price_or_min_bid:,} OBX`"
        )

    embed.description = "\n".join(lines)
    embed.set_footer(text=f"Total: {len(auctions)} Auctions • OBX Admin Control")
    return embed


def build_auction_detail_embed(
    auction: Auction,
    bids: List[AuctionBid],
) -> discord.Embed:
    """Builds detailed view for a single auction."""
    is_active = (auction.status == AuctionStatus.ACTIVE)
    color = COLOR_GOLD if is_active else (COLOR_GREEN if auction.status == AuctionStatus.COMPLETED else COLOR_RED)

    embed = discord.Embed(
        title=f"🔨 Whitelist Auction Details — {auction.title}",
        description=auction.description or "No description provided.",
        color=color,
    )

    status_icon = "🟢" if is_active else ("✅" if auction.status == AuctionStatus.COMPLETED else "🔴")
    embed.add_field(name="Status", value=f"{status_icon} **{auction.status.value}**", inline=True)
    embed.add_field(name="Type", value=f"`{auction.auction_type.value}`", inline=True)
    embed.add_field(name="Reward", value=f"**{auction.reward_title}**", inline=True)

    embed.add_field(name="Total Spots", value=f"`{auction.total_slots}`", inline=True)
    embed.add_field(name="Allocated / Sold", value=f"`{auction.allocated_slots}/{auction.total_slots}`", inline=True)
    embed.add_field(name="Min Bid / Price", value=f"`{auction.price_or_min_bid:,} OBX`", inline=True)

    if auction.ends_at:
        ts = int(auction.ends_at.timestamp())
        embed.add_field(name="Ends At", value=f"<t:{ts}:F> (<t:{ts}:R>)", inline=False)
    else:
        embed.add_field(name="Ends At", value="*No expiration date*", inline=False)

    if auction.project_x_url:
        handle = auction.preview_x_handle or "Project X"
        embed.add_field(name="Project X Profile", value=f"[{handle}]({auction.project_x_url})", inline=False)

    if auction.auction_type == AuctionType.GTD:
        if bids:
            top_lines = []
            for i, b in enumerate(bids[:5], start=1):
                win_icon = "🟢" if i <= auction.total_slots else "🔴"
                top_lines.append(f"`#{i}` {win_icon} <@{b.discord_user_id}> — `{b.bid_amount:,} OBX`")
            embed.add_field(
                name=f"Top Bidders (Total: {len(bids)})",
                value="\n".join(top_lines),
                inline=False,
            )
        else:
            embed.add_field(name="Bidders", value="*No bids placed yet.*", inline=False)

    embed.set_footer(text=f"Auction ID: {auction.id} • OBX Economy")
    return embed


class AdminAuctionSelect(Select):
    """Dropdown menu to choose an auction from the current list."""
    def __init__(self, auctions: List[Auction]):
        options = []
        for auc in auctions[:25]:
            type_str = "GTD" if auc.auction_type == AuctionType.GTD else "FCFS"
            label = f"{auc.title[:45]} ({auc.status.value})"
            desc = f"{type_str} • {auc.total_slots} spots • {auc.price_or_min_bid:,} OBX"
            options.append(discord.SelectOption(
                label=label,
                value=str(auc.id),
                description=desc[:100],
                emoji="🎟️",
            ))

        super().__init__(
            placeholder="Select an auction to view or edit...",
            min_values=1,
            max_values=1,
            options=options,
            row=0,
        )

    async def callback(self, interaction: discord.Interaction):
        if not is_admin(interaction):
            await interaction.response.send_message("❌ Administrator permission required.", ephemeral=True)
            return

        auction_id = self.values[0]
        await show_auction_detail(interaction, auction_id)


class AdminAuctionBrowserView(View):
    """Browser view for listing and selecting auctions."""
    def __init__(self, auctions: List[Auction], current_filter: Optional[str] = "ACTIVE"):
        super().__init__(timeout=None)
        self.current_filter = current_filter
        if auctions:
            self.add_item(AdminAuctionSelect(auctions))

    @discord.ui.button(label="🟢 Active", style=discord.ButtonStyle.primary, row=1)
    async def btn_filter_active(self, interaction: discord.Interaction, button: Button):
        await filter_auctions(interaction, "ACTIVE")

    @discord.ui.button(label="✅ Completed", style=discord.ButtonStyle.secondary, row=1)
    async def btn_filter_completed(self, interaction: discord.Interaction, button: Button):
        await filter_auctions(interaction, "COMPLETED")

    @discord.ui.button(label="🔴 Cancelled", style=discord.ButtonStyle.secondary, row=1)
    async def btn_filter_cancelled(self, interaction: discord.Interaction, button: Button):
        await filter_auctions(interaction, "CANCELLED")

    @discord.ui.button(label="📋 All", style=discord.ButtonStyle.secondary, row=1)
    async def btn_filter_all(self, interaction: discord.Interaction, button: Button):
        await filter_auctions(interaction, None)

    @discord.ui.button(label="➕ Create Auction", style=discord.ButtonStyle.success, row=2)
    async def btn_create_auction(self, interaction: discord.Interaction, button: Button):
        if not is_admin(interaction):
            await interaction.response.send_message("❌ Administrator permission required.", ephemeral=True)
            return
        from apps.obx_tasks.bot.auction_views import AdminCreateAuctionModal
        modal = AdminCreateAuctionModal()
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="⬅️ Back to Admin Hub", style=discord.ButtonStyle.secondary, row=2)
    async def btn_back_hub(self, interaction: discord.Interaction, button: Button):
        if not is_admin(interaction):
            await interaction.response.send_message("❌ Administrator permission required.", ephemeral=True)
            return
        from apps.obx_tasks.bot.dashboard_views import OBXAdminHubView, create_admin_hub_embed
        view = OBXAdminHubView()
        embed = create_admin_hub_embed()
        await safe_edit_interaction(interaction, embed=embed, view=view)


class AdminAuctionDetailView(View):
    """Detailed management card for an individual auction with Edit & Delete/Cancel buttons."""
    def __init__(self, auction_id: str, is_active: bool = True, is_gtd: bool = True):
        super().__init__(timeout=None)
        self.auction_id = str(auction_id)
        self.is_active = is_active
        self.is_gtd = is_gtd

    @discord.ui.button(label="Edit Auction", style=discord.ButtonStyle.primary, emoji="✏️", row=0)
    async def edit_btn(self, interaction: discord.Interaction, button: Button):
        if not is_admin(interaction):
            await interaction.response.send_message("❌ Administrator permission required.", ephemeral=True)
            return

        with session_scope() as session:
            service = AuctionService(session)
            auction = service.get_auction(self.auction_id)
            if not auction:
                await interaction.response.send_message("❌ Auction not found.", ephemeral=True)
                return
            from apps.obx_tasks.bot.auction_views import AdminEditAuctionModal
            modal = AdminEditAuctionModal(auction)
            await interaction.response.send_modal(modal)

    @discord.ui.button(label="Cancel / Delete", style=discord.ButtonStyle.danger, emoji="🗑️", row=0)
    async def cancel_btn(self, interaction: discord.Interaction, button: Button):
        if not is_admin(interaction):
            await interaction.response.send_message("❌ Administrator permission required.", ephemeral=True)
            return

        with session_scope() as session:
            service = AuctionService(session)
            auction = service.get_auction(self.auction_id)
            if not auction:
                await interaction.response.send_message("❌ Auction not found.", ephemeral=True)
                return

            if auction.status == AuctionStatus.COMPLETED:
                await interaction.response.send_message("❌ Cannot cancel an already COMPLETED auction.", ephemeral=True)
                return
            if auction.status == AuctionStatus.CANCELLED:
                await interaction.response.send_message("❌ This auction is already CANCELLED.", ephemeral=True)
                return

        # Show cancel confirmation view
        confirm_view = AdminCancelAuctionConfirmView(self.auction_id)
        embed = discord.Embed(
            title="⚠️ Confirm Auction Cancellation & Instant Refund",
            description=(
                f"Are you sure you want to cancel auction **{auction.title}**?\n\n"
                "**Effects:**\n"
                "• 🔒 The auction will immediately close.\n"
                "• 💸 **100% of all locked bids will be INSTANTLY refunded** to bidders' available balances.\n"
                "• 📢 The announcement card in `#wl-auctions` will be updated to CANCELLED status.\n"
                "• 📜 An audit entry will be recorded in admin logs."
            ),
            color=COLOR_RED,
        )
        await safe_edit_interaction(interaction, embed=embed, view=confirm_view)

    @discord.ui.button(label="Settle Now", style=discord.ButtonStyle.success, emoji="🏆", row=0)
    async def settle_btn(self, interaction: discord.Interaction, button: Button):
        if not is_admin(interaction):
            await interaction.response.send_message("❌ Administrator permission required.", ephemeral=True)
            return

        if not _is_response_done(interaction):
            try:
                await interaction.response.defer(ephemeral=True)
            except Exception:
                pass

        try:
            with session_scope() as session:
                service = AuctionService(session)
                auc = service.get_auction(self.auction_id)
                if auc.status != AuctionStatus.ACTIVE:
                    await interaction.followup.send(f"❌ Auction is not active (current status: {auc.status.value}).", ephemeral=True)
                    return

                auc, winners, losers = service.settle_and_finalize_auction(
                    self.auction_id,
                    finalized_by=str(interaction.user.id),
                )

            # Auto-announce winners to channel
            if interaction.guild:
                try:
                    with session_scope() as sess_bids:
                        all_bids = sess_bids.query(AuctionBid).filter_by(auction_id=auc.id).all()
                        total_bidders = len(all_bids)
                    await announce_auction_winners(auc, winners, total_bidders, interaction.guild, interaction.client)
                except Exception as ann_err:
                    logger.warning("Auto-announcement of winners failed: %s", ann_err)

                # Update live card in place
                try:
                    await announce_auction(auc, interaction.guild, interaction.client)
                except Exception as card_err:
                    logger.warning("Could not refresh auction card on settle: %s", card_err)

            # Log to admin channel
            if interaction.guild:
                await send_admin_log_event(
                    guild=interaction.guild,
                    title="🏆 AUCTION SETTLED & FINALIZED",
                    description=f"Admin <@{interaction.user.id}> manually settled auction **{auc.title}**.",
                    color=COLOR_GOLD,
                    fields=[
                        ("Winners Count", str(len(winners)), True),
                        ("Refunded Losers", str(len(losers)), True),
                    ],
                )

            embed = discord.Embed(
                title="🏆 Auction Settled & Finalized!",
                description=(
                    f"Auction **{auc.title}** has concluded.\n"
                    f"• 👑 **Winners:** `{len(winners)}`\n"
                    f"• 💸 **Non-Winners Refunded:** `{len(losers)}`\n"
                    "All non-winning bids have been released to available balances instantly!"
                ),
                color=COLOR_GOLD,
            )
            await safe_edit_interaction(interaction, embed=embed, view=None)
        except Exception as exc:
            logger.error("Error settling auction manually: %s", exc)
            await interaction.followup.send(f"❌ Error settling auction: {str(exc)}", ephemeral=True)

    @discord.ui.button(label="Refresh Card", style=discord.ButtonStyle.secondary, emoji="🔄", row=1)
    async def refresh_btn(self, interaction: discord.Interaction, button: Button):
        if not is_admin(interaction):
            await interaction.response.send_message("❌ Administrator permission required.", ephemeral=True)
            return

        if not _is_response_done(interaction):
            try:
                await interaction.response.defer(ephemeral=True)
            except Exception:
                pass

        try:
            with session_scope() as session:
                service = AuctionService(session)
                auc = service.get_auction(self.auction_id)

            if interaction.guild:
                ok, msg = await announce_auction(auc, interaction.guild, interaction.client)
                await interaction.followup.send(f"📢 **Card Refresh:** {msg}", ephemeral=True)
            else:
                await interaction.followup.send("❌ Cannot refresh: Guild context missing.", ephemeral=True)
        except Exception as exc:
            logger.error("Error refreshing card: %s", exc)
            await interaction.followup.send(f"❌ Error refreshing card: {str(exc)}", ephemeral=True)

    @discord.ui.button(label="⬅️ Back to Auctions", style=discord.ButtonStyle.secondary, row=1)
    async def back_btn(self, interaction: discord.Interaction, button: Button):
        if not is_admin(interaction):
            if not _is_response_done(interaction):
                try:
                    await interaction.response.send_message("❌ Administrator permission required.", ephemeral=True)
                except Exception:
                    pass
            return
        await render_auction_browser(interaction, status_filter="ACTIVE")


class AdminCancelAuctionConfirmView(View):
    """Confirmation prompt to execute instant cancellation and refund."""
    def __init__(self, auction_id: str):
        super().__init__(timeout=None)
        self.auction_id = str(auction_id)

    @discord.ui.button(label="Confirm Cancel & Instant Refund", style=discord.ButtonStyle.danger, emoji="🔴", row=0)
    async def confirm_btn(self, interaction: discord.Interaction, button: Button):
        if not is_admin(interaction):
            await interaction.response.send_message("❌ Administrator permission required.", ephemeral=True)
            return

        if not _is_response_done(interaction):
            try:
                await interaction.response.defer(ephemeral=True)
            except Exception:
                pass

        try:
            with session_scope() as session:
                service = AuctionService(session)
                cancelled_auc = service.cancel_auction(
                    self.auction_id,
                    cancelled_by=str(interaction.user.id),
                )

            # Update public announcement card to show CANCELLED
            if interaction.guild:
                try:
                    await announce_auction(cancelled_auc, interaction.guild, interaction.client)
                except Exception as card_err:
                    logger.warning("Could not refresh auction card on cancel: %s", card_err)

                # Send admin audit log
                try:
                    await send_admin_log_event(
                        guild=interaction.guild,
                        title="🗑️ AUCTION CANCELLED & REFUNDED",
                        description=f"Admin <@{interaction.user.id}> cancelled auction **{cancelled_auc.title}**.",
                        color=COLOR_RED,
                        fields=[
                            ("Auction Title", cancelled_auc.title, True),
                            ("Action", "All locked bids refunded to available balances", False),
                        ],
                    )
                except Exception as log_err:
                    logger.warning("Could not send admin log for cancel: %s", log_err)

            success_embed = discord.Embed(
                title="🗑️ Auction Cancelled & Fully Refunded",
                description=(
                    f"Auction **{cancelled_auc.title}** has been marked as **CANCELLED**.\n\n"
                    "✅ **Instant Refund Executed:** All locked bids have been returned to user wallets immediately.\n"
                    "📢 The public announcement card in `#wl-auctions` has been updated."
                ),
                color=COLOR_RED,
            )
            back_view = View()
            back_btn = Button(label="⬅️ Return to Auctions", style=discord.ButtonStyle.secondary)

            async def back_callback(b_inter: discord.Interaction):
                await render_auction_browser(b_inter, status_filter="ACTIVE")

            back_btn.callback = back_callback
            back_view.add_item(back_btn)

            await safe_edit_interaction(interaction, embed=success_embed, view=back_view)
        except Exception as exc:
            logger.error("Error cancelling auction: %s", exc)
            await interaction.followup.send(f"❌ Error cancelling auction: {str(exc)}", ephemeral=True)

    @discord.ui.button(label="❌ Keep Auction (Back)", style=discord.ButtonStyle.secondary, row=0)
    async def cancel_back_btn(self, interaction: discord.Interaction, button: Button):
        if not is_admin(interaction):
            if not _is_response_done(interaction):
                try:
                    await interaction.response.send_message("❌ Administrator permission required.", ephemeral=True)
                except Exception:
                    pass
            return
        await show_auction_detail(interaction, self.auction_id)


async def show_auction_detail(interaction: discord.Interaction, auction_id: str):
    """Displays the detail inspection card for an auction."""
    try:
        with session_scope() as session:
            service = AuctionService(session)
            auction = service.get_auction(auction_id)
            if not auction:
                await safe_edit_interaction(interaction, content="❌ Auction not found.", embed=None, view=None)
                return

            bids = (
                session.query(AuctionBid)
                .filter_by(auction_id=auction.id)
                .order_by(AuctionBid.bid_amount.desc(), AuctionBid.updated_at.asc())
                .all()
            )

        embed = build_auction_detail_embed(auction, bids)
        view = AdminAuctionDetailView(
            auction_id=str(auction.id),
            is_active=(auction.status == AuctionStatus.ACTIVE),
            is_gtd=(auction.auction_type == AuctionType.GTD),
        )
        await safe_edit_interaction(interaction, embed=embed, view=view)
    except Exception as exc:
        logger.error("Error showing auction detail: %s", exc)
        await safe_edit_interaction(interaction, content=f"❌ Error loading auction details: {str(exc)}", embed=None, view=None)


async def render_auction_browser(interaction: discord.Interaction, status_filter: Optional[str] = "ACTIVE"):
    """Renders or updates the Auction Browser list view in place."""
    try:
        with session_scope() as session:
            query = session.query(Auction).order_by(Auction.created_at.desc())
            if status_filter:
                try:
                    stat_enum = AuctionStatus(status_filter)
                    query = query.filter(Auction.status == stat_enum)
                except Exception:
                    pass
            auctions = query.limit(25).all()

        embed = build_auction_browser_embed(auctions, status_filter=status_filter)
        view = AdminAuctionBrowserView(auctions, current_filter=status_filter)
        await safe_edit_interaction(interaction, embed=embed, view=view)
    except Exception as exc:
        logger.error("Error in render_auction_browser: %s", exc)
        await safe_edit_interaction(interaction, content=f"❌ Error loading auctions: {str(exc)}", embed=None, view=None)


async def filter_auctions(interaction: discord.Interaction, status_filter: Optional[str]):
    """Filters auctions by status and updates the browser view."""
    await render_auction_browser(interaction, status_filter=status_filter)


async def handle_admin_manage_auctions(interaction: discord.Interaction):
    """Main entry point for managing auctions from Admin Hub."""
    if not is_admin(interaction):
        if not _is_response_done(interaction):
            try:
                await interaction.response.send_message("❌ Administrator permission required.", ephemeral=True)
            except Exception:
                pass
        else:
            try:
                await interaction.followup.send("❌ Administrator permission required.", ephemeral=True)
            except Exception:
                pass
        return

    if not _is_response_done(interaction):
        try:
            await interaction.response.defer(ephemeral=True)
        except Exception:
            pass

    await render_auction_browser(interaction, status_filter="ACTIVE")
