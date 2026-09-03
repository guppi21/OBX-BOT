import inspect
import uuid
import discord
from discord.ui import View, Button, Modal, TextInput, Select
from typing import Optional, List, Dict, Any

from packages.database.session import session_scope
from packages.database.models.auction import Auction, AuctionBid, AuctionClaim
from packages.shared.enums import AuctionType, AuctionStatus
from apps.obx_tasks.services.auction_service import AuctionService, AuctionError
from apps.obx_core.services.wallet_service import WalletService
from apps.obx_tasks.bot.permissions import is_admin
from apps.obx_tasks.bot.ui_theme import (
    COLOR_GOLD, COLOR_PURPLE, COLOR_TEAL, COLOR_GREEN, COLOR_BLUE, COLOR_DARK, COLOR_ORANGE, COLOR_RED
)
from packages.shared.logging import get_logger

logger = get_logger("obx.tasks.bot.auctions")

MEDALS = {1: "🥇", 2: "🥈", 3: "🥉", 4: "4️⃣", 5: "5️⃣", 6: "6️⃣", 7: "7️⃣", 8: "8️⃣", 9: "9️⃣", 10: "🔟"}


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


def parse_project_and_reward(val: str) -> Tuple[str, str]:
    val = val.strip()
    if "/" in val:
        parts = val.split("/", 1)
        return parts[0].strip(), parts[1].strip()
    elif "—" in val:
        parts = val.split("—", 1)
        return parts[0].strip(), parts[1].strip()
    elif " - " in val:
        parts = val.split(" - ", 1)
        return parts[0].strip(), parts[1].strip()
    return val, "Whitelist Allocation"


def parse_slots_and_price(val: str) -> Tuple[int, int]:
    val = val.strip()
    if "/" in val:
        parts = val.split("/")
    elif "," in val:
        parts = val.split(",")
    else:
        parts = val.split()
    if len(parts) >= 2:
        return int(parts[0].strip().replace(",", "")), int(parts[1].strip().replace(",", ""))
    elif len(parts) == 1:
        s = int(parts[0].strip().replace(",", ""))
        return s, 10
    raise ValueError("Please provide Whitelist Spots (e.g. '5' or '5 / 10').")


def build_auction_notification_embed(
    auction: Auction,
    standings: Optional[Dict[str, Any]] = None,
) -> discord.Embed:
    """Build a premium minimal notification card embed for the configured Auctions channel.
    Target Hierarchy:
    - 🎟️ WHITELIST AUCTION (title)
    - # **ASTRAL SENTINELS (GTD-DTC)**
    - Project description
    - [Project X Profile Visual Preview] (Banner as hero image, avatar as thumbnail)
    - ━━━━━━━━━━━━━━━━━━━━
    - ⏳ ENDS          19 HOURS
    - 🏆 WINNERS       5
    - 🎟️ SPOT TYPE     WL
    - 💎 MIN BID       10 OBX
    - ━━━━━━━━━━━━━━━━━━━━
    - 🏆 TOP BIDS (🥇, 🥈, 🥉)
    """
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    ends_at = auction.ends_at
    if ends_at is not None and ends_at.tzinfo is None:
        ends_at = ends_at.replace(tzinfo=timezone.utc)

    is_ended = (auction.status in (AuctionStatus.COMPLETED, AuctionStatus.CANCELLED)) or (
        ends_at is not None and now > ends_at
    )
    is_fcfs = (auction.auction_type == AuctionType.FCFS)

    color = COLOR_GOLD if not is_ended else COLOR_RED
    header_title = "🎟️ WHITELIST SALE" if is_fcfs else "🎟️ WHITELIST AUCTION"

    # 1. Title and Description
    body_lines = [
        f"# **{auction.title.upper()}**",
        "",
        auction.description or "Exclusive community whitelist opportunity.",
    ]

    # If X profile preview metadata is available, show clean attribution without raw URL
    if auction.preview_x_handle:
        disp = auction.preview_x_display_name or auction.preview_x_handle.lstrip("@")
        body_lines.append("")
        body_lines.append(f"**{disp}**  {auction.preview_x_handle}")

    body_lines.append("")
    body_lines.append("━━━━━━━━━━━━━━━━━━━━")
    body_lines.append("")

    # 2. Compact Auction Information
    if is_ended and ends_at:
        ends_str = f"ENDED <t:{int(ends_at.timestamp())}:R>"
    elif ends_at:
        ends_str = f"<t:{int(ends_at.timestamp())}:R>"
    else:
        ends_str = "NO DEADLINE"

    spot_type = auction.reward_title or "WL"
    if "whitelist" in spot_type.lower() and len(spot_type) > 15:
        spot_type = "WL"

    if is_fcfs:
        info_lines = [
            f"⏳ **ENDS**          {ends_str}",
            f"🎟️ **SPOTS**         {auction.total_slots}",
            f"🎟️ **SPOT TYPE**     {spot_type}",
            f"💎 **PRICE**         {auction.price_or_min_bid:,} OBX",
        ]
    else:
        info_lines = [
            f"⏳ **ENDS**          {ends_str}",
            f"🏆 **WINNERS**       {auction.total_slots}",
            f"🎟️ **SPOT TYPE**     {spot_type}",
            f"💎 **MIN BID**       {auction.price_or_min_bid:,} OBX",
        ]
    body_lines.extend(info_lines)

    # 3. Top Bids (for ranked auctions)
    if not is_fcfs:
        body_lines.append("")
        body_lines.append("━━━━━━━━━━━━━━━━━━━━")
        body_lines.append("")
        body_lines.append("🏆 **TOP BIDS**")
        body_lines.append("")

        ranked_bids = standings.get("ranked_bids", []) if standings else []
        if ranked_bids:
            medals = ["🥇", "🥈", "🥉"]
            for idx, b in enumerate(ranked_bids[:5]):
                medal = medals[idx] if idx < 3 else f"{idx + 1}."
                bid_amt = getattr(b, "bid_amount", 0)
                u_id = getattr(b, "discord_user_id", "User")
                body_lines.append(f"{medal} <@{u_id}> — **{bid_amt:,} OBX**")
        else:
            body_lines.append("*No bids placed yet. Be the first!*")

    embed = discord.Embed(
        title=header_title,
        description="\n".join(body_lines),
        color=color,
    )

    # Large Project Image selection using strict priority:
    # 1. X profile banner image
    # 2. Project/profile OpenGraph image
    # 3. X profile avatar as a fallback
    # 4. No image if none safely available
    selected_img = getattr(auction, "preview_image_url", None)
    if not selected_img:
        from apps.obx_tasks.services.auction_service import resolve_auction_preview_image
        selected_img = resolve_auction_preview_image(
            banner_url=getattr(auction, "preview_x_banner_url", None),
            og_image_url=getattr(auction, "image_url", None),
            avatar_url=getattr(auction, "preview_x_avatar_url", None),
        )

    # Validate image URL before rendering as the large main image of the embed
    if selected_img and isinstance(selected_img, str):
        clean_img = selected_img.strip()
        if (clean_img.startswith("http://") or clean_img.startswith("https://")) and not any(ch in clean_img for ch in ["\r", "\n", " "]):
            embed.set_image(url=clean_img)

    # Zero footers, zero technical IDs
    return embed


class AuctionNotificationCardView(View):
    """Interactive action buttons rendered directly on the public auction card in #auctions."""
    def __init__(
        self,
        auction_id: str,
        is_active: bool,
        is_fcfs: bool = False,
        is_sold_out: bool = False,
        external_url: Optional[str] = None,
    ):
        super().__init__(timeout=None)
        self.auction_id = auction_id

        if is_active:
            if is_fcfs:
                self.add_item(discord.ui.Button(
                    label="CLAIM SPOT",
                    style=discord.ButtonStyle.success,
                    custom_id=f"obx:auc_card:claim:{auction_id}",
                    row=0,
                ))
                self.add_item(discord.ui.Button(
                    label="MY PURCHASE",
                    style=discord.ButtonStyle.secondary,
                    custom_id=f"obx:auc_card:rankings:{auction_id}",
                    row=0,
                ))
            else:
                self.add_item(discord.ui.Button(
                    label="BID",
                    style=discord.ButtonStyle.success,
                    custom_id=f"obx:auc_card:bid:{auction_id}",
                    row=0,
                ))
                self.add_item(discord.ui.Button(
                    label="EDIT BID",
                    style=discord.ButtonStyle.primary,
                    custom_id=f"obx:auc_card:edit_bid:{auction_id}",
                    row=0,
                ))
                self.add_item(discord.ui.Button(
                    label="MY BID",
                    style=discord.ButtonStyle.secondary,
                    custom_id=f"obx:auc_card:rankings:{auction_id}",
                    row=0,
                ))
        else:
            if is_fcfs:
                self.add_item(discord.ui.Button(
                    label="SALE CONCLUDED",
                    style=discord.ButtonStyle.secondary,
                    custom_id=f"obx:auc_card:closed:{auction_id}",
                    disabled=True,
                    row=0,
                ))
                self.add_item(discord.ui.Button(
                    label="MY PURCHASE",
                    style=discord.ButtonStyle.secondary,
                    custom_id=f"obx:auc_card:rankings:{auction_id}",
                    row=0,
                ))
            else:
                self.add_item(discord.ui.Button(
                    label="AUCTION CONCLUDED",
                    style=discord.ButtonStyle.secondary,
                    custom_id=f"obx:auc_card:closed:{auction_id}",
                    disabled=True,
                    row=0,
                ))
                self.add_item(discord.ui.Button(
                    label="MY BID",
                    style=discord.ButtonStyle.secondary,
                    custom_id=f"obx:auc_card:rankings:{auction_id}",
                    row=0,
                ))


def build_auction_card_embed(
    auction: Auction,
    standings: Optional[Dict[str, Any]] = None,
    user_claim: Optional[AuctionClaim] = None,
) -> discord.Embed:
    is_fcfs = (auction.auction_type == AuctionType.FCFS)
    color = COLOR_GOLD if is_fcfs else COLOR_PURPLE

    type_badge = "⚡ First-Come, First-Served (FCFS)" if is_fcfs else "🏆 Multi-Winner Ranked Bid Auction"
    status_emoji = "🟢 LIVE" if auction.status == AuctionStatus.ACTIVE else f"⏸️ {auction.status.value}"

    embed = discord.Embed(
        title=f"🔨 {auction.title} — {auction.reward_title}",
        description=f"{auction.description}\n\n**Type:** `{type_badge}`\n**Status:** {status_emoji}",
        color=color,
    )

    if is_fcfs:
        embed.add_field(name="🏷 Fixed Price", value=f"**{auction.price_or_min_bid:,} OBX**", inline=True)
        embed.add_field(name="🎟 Slots Remaining", value=f"**{auction.remaining_slots} / {auction.total_slots}**", inline=True)
    else:
        cutoff_str = f"**{standings['winning_cutoff']:,} OBX**" if standings else f"**{auction.price_or_min_bid:,} OBX**"
        total_bids_str = f"`{standings['total_bidders']}`" if standings else "`0`"

        embed.add_field(name="🎟 Available WL Spots", value=f"**{auction.total_slots} Spots**", inline=True)
        embed.add_field(name="💎 Minimum Bid", value=f"**{auction.price_or_min_bid:,} OBX**", inline=True)
        embed.add_field(name="📊 Winning Cutoff", value=cutoff_str, inline=True)
        embed.add_field(name="👥 Valid Bidders", value=total_bids_str, inline=True)

    if auction.ends_at:
        embed.add_field(name="⏳ Ends At", value=f"<t:{int(auction.ends_at.timestamp())}:R>", inline=True)

    if auction.external_url:
        embed.add_field(name="🔗 Official Link", value=f"[Open Project Page]({auction.external_url})", inline=False)

    # User Status Section
    if user_claim:
        embed.add_field(
            name="🎟 YOUR STATUS",
            value=f"✅ **WHITELIST SECURED!** (Paid `{user_claim.price_paid:,} OBX`)",
            inline=False,
        )
    elif standings and standings.get("user_bid_amount") is not None:
        u_bid = standings["user_bid_amount"]
        u_rank = standings["user_rank"]
        is_win = standings["is_winning"]
        slots = standings["total_slots"]

        if is_win:
            win_badge = f"🟢 **Currently Winning** (Rank #{u_rank} of {slots} winning spots)"
        else:
            win_badge = f"🔴 **Outside Winning Positions** (Rank #{u_rank} • Need to enter Top {slots})"

        user_status_text = (
            f"**Your Active Bid:** `{u_bid:,} OBX` (Locked in Vault)\n"
            f"**Your Position:** `#{u_rank}` • **Status:** {win_badge}"
        )
        embed.add_field(name="📍 YOUR BID STATUS", value=user_status_text, inline=False)

    embed.set_footer(text=f"Auction ID: {auction.id} • Multi-Winner Ranked Bidding • Double-Entry Protected")
    return embed


class AuctionCenterView(View):
    """Main landing hub for community auctions."""
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Active Auctions", style=discord.ButtonStyle.success, custom_id="obx:auc:active", row=0)
    async def active_btn(self, interaction: discord.Interaction, button: Button):
        await handle_browse_auctions(interaction, status=AuctionStatus.ACTIVE)

    @discord.ui.button(label="My Bids & Wins", style=discord.ButtonStyle.primary, custom_id="obx:auc:my_activity", row=0)
    async def my_bids_btn(self, interaction: discord.Interaction, button: Button):
        await handle_my_auction_activity(interaction)

    @discord.ui.button(label="Auction Guide", style=discord.ButtonStyle.secondary, custom_id="obx:auc:help", row=0)
    async def guide_btn(self, interaction: discord.Interaction, button: Button):
        embed = discord.Embed(
            title="🔨 OBX Whitelist & Multi-Winner Auction Guide",
            description=(
                "**How Ranked Whitelist Auctions Work:**\n\n"
                "**🏆 1. Multi-Winner Ranked Bid Auctions (GTD)**\n"
                "• Exactly the **Top N highest unique bids** win the N available whitelist spots.\n"
                "• **Pay-As-Bid**: Each winner pays their actual submitted winning bid.\n"
                "• **Full Refund**: All non-winning bids are 100% unlocked and returned to available balance.\n"
                "• **Bid Updates**: You can increase or decrease your bid anytime while the auction is live.\n"
                "• **Deterministic Tie-Breaking**: Higher bid > Earlier timestamp > Stable user ID.\n\n"
                "**⚡ 2. FCFS (First-Come, First-Served) Sales**\n"
                "• Fixed OBX price per spot. First eligible members to claim secure the whitelist.\n"
                "• Zero overselling with race-safe atomic database locking.\n\n"
                "**🛡 Wallet & Economy Protection**\n"
                "• Bids lock funds directly in your vault; locked funds remain part of your total balance.\n"
                "• Zero chance of double-charging or lost balances."
            ),
            color=COLOR_GOLD,
        )
        if _is_response_done(interaction):
            await interaction.followup.send(embed=embed, ephemeral=True)
        else:
            await interaction.response.send_message(embed=embed, ephemeral=True)


class AuctionBrowserView(View):
    """Paginated browser for active whitelist sales and multi-winner GTD auctions."""
    def __init__(self, auctions: List[Auction], current_index: int = 0):
        super().__init__(timeout=300)
        self.auctions = auctions
        self.current_index = current_index
        self._update_buttons()

    def _update_buttons(self):
        has_items = bool(self.auctions)
        self.btn_prev.disabled = (not has_items or self.current_index <= 0)
        self.btn_next.disabled = (not has_items or self.current_index >= len(self.auctions) - 1)

        if has_items:
            current = self.auctions[self.current_index]
            if current.auction_type == AuctionType.FCFS:
                self.btn_action.label = "Claim Whitelist"
                self.btn_action.emoji = "⚡"
                self.btn_action.style = discord.ButtonStyle.success
                self.btn_action.disabled = (current.remaining_slots <= 0 or current.status != AuctionStatus.ACTIVE)
                self.btn_rankings.disabled = True
            else:
                self.btn_action.label = "Place / Update Bid"
                self.btn_action.emoji = "💰"
                self.btn_action.style = discord.ButtonStyle.primary
                self.btn_action.disabled = (current.status != AuctionStatus.ACTIVE)
                self.btn_rankings.disabled = False
        else:
            self.btn_action.disabled = True
            self.btn_rankings.disabled = True

    @discord.ui.button(label="Claim Whitelist", style=discord.ButtonStyle.success, row=0)
    async def btn_action(self, interaction: discord.Interaction, button: Button):
        if not self.auctions:
            return
        auction = self.auctions[self.current_index]
        if auction.auction_type == AuctionType.FCFS:
            # Immediate deferral before DB operations
            await interaction.response.defer(ephemeral=True)
            try:
                with session_scope() as session:
                    service = AuctionService(session)
                    claim = service.claim_fcfs_slot(auction.id, str(interaction.user.id))
                    refreshed_auc = service.get_auction(auction.id)

                from packages.shared.typography import DIVIDER
                desc_lines = [
                    "You successfully secured:",
                    f"🎟️ **{refreshed_auc.reward_title}**",
                    "",
                    "💎 **PRICE PAID**",
                    f"{claim.price_paid:,} OBX",
                    "",
                    "🎟️ **REMAINING SPOTS**",
                    f"{refreshed_auc.remaining_slots} / {refreshed_auc.total_slots}",
                    "",
                    DIVIDER,
                    "",
                    "Your spot is secured. Good luck.",
                ]
                embed = discord.Embed(
                    title="🎉 WHITELIST CLAIMED!",
                    description="\n".join(desc_lines),
                    color=COLOR_GREEN,
                )
                embed.set_footer(text="✦ OBX WHITELIST AUCTIONS")
                view = AuctionActionSuccessView()
                await interaction.followup.send(embed=embed, view=view, ephemeral=True)
            except AuctionError as exc:
                await interaction.followup.send(f"❌ {exc.message}", ephemeral=True)
            except Exception as exc:
                logger.error("Error claiming FCFS slot: %s", exc)
                await interaction.followup.send(f"❌ Error claiming slot: {str(exc)}", ephemeral=True)
        else:
            # Pop up modal IMMEDIATELY without any prior database blocking!
            modal = GTDBidModal(
                auction_id=str(auction.id),
                auction_title=f"{auction.title} — {auction.reward_title}",
                min_bid=auction.price_or_min_bid,
            )
            await interaction.response.send_modal(modal)

    @discord.ui.button(label="View Rankings", style=discord.ButtonStyle.secondary, row=0)
    async def btn_rankings(self, interaction: discord.Interaction, button: Button):
        if not self.auctions:
            return
        # Immediate deferral before database query
        await interaction.response.defer(ephemeral=True)
        auction = self.auctions[self.current_index]
        try:
            with session_scope() as session:
                service = AuctionService(session)
                standings = service.get_auction_standings(auction.id, discord_user_id=str(interaction.user.id))

            embed = discord.Embed(
                title=f"📊 Live Bid Rankings — {auction.title}",
                description=f"**Reward:** {auction.reward_title} • **Available Slots:** `{auction.total_slots}`\nTop {auction.total_slots} unique bidders win whitelist spots at auction close.\n",
                color=COLOR_GOLD,
            )

            bids = standings["ranked_bids"]
            if not bids:
                embed.add_field(name="No Bids Placed Yet", value="Be the first to place a bid and secure the #1 spot!", inline=False)
            else:
                rank_lines = []
                for idx, b in enumerate(bids[:15], start=1):
                    medal = MEDALS.get(idx, f"`#{idx}`")
                    win_icon = "🟢" if idx <= auction.total_slots else "🔴"
                    rank_lines.append(f"{medal} {win_icon} <@{b.discord_user_id}> — **{b.bid_amount:,} OBX**")

                embed.add_field(
                    name=f"Top Bidders (Total Bidders: {len(bids)})",
                    value="\n".join(rank_lines),
                    inline=False,
                )

            if standings.get("user_bid_amount") is not None:
                u_rank = standings["user_rank"]
                u_bid = standings["user_bid_amount"]
                is_win = standings["is_winning"]
                status_text = "🟢 **Winning Position**" if is_win else f"🔴 **Outside Winning Positions** (Cutoff: `{standings['winning_cutoff']:,} OBX`)"
                embed.add_field(
                    name="📍 Your Standing",
                    value=f"**Rank:** `#{u_rank}` • **Bid:** `{u_bid:,} OBX` • **Status:** {status_text}",
                    inline=False,
                )

            embed.set_footer(text="Rankings update dynamically in real time • Pay-As-Bid")
            await interaction.followup.send(embed=embed, ephemeral=True)
        except Exception as exc:
            logger.error("Error viewing rankings: %s", exc)
            await interaction.followup.send("❌ Error fetching live rankings.", ephemeral=True)

    @discord.ui.button(label="Previous", style=discord.ButtonStyle.secondary, row=1)
    async def btn_prev(self, interaction: discord.Interaction, button: Button):
        if self.current_index > 0:
            self.current_index -= 1
            await self._render_current(interaction)

    @discord.ui.button(label="Next", style=discord.ButtonStyle.secondary, row=1)
    async def btn_next(self, interaction: discord.Interaction, button: Button):
        if self.current_index < len(self.auctions) - 1:
            self.current_index += 1
            await self._render_current(interaction)

    @discord.ui.button(label="Refresh", style=discord.ButtonStyle.secondary, row=1)
    async def btn_refresh(self, interaction: discord.Interaction, button: Button):
        await self._render_current(interaction)


    async def _render_current(self, interaction: discord.Interaction):
        # Acknowledge immediately before querying
        if not _is_response_done(interaction):
            await interaction.response.defer(ephemeral=True)

        self._update_buttons()
        current = self.auctions[self.current_index]

        try:
            with session_scope() as session:
                service = AuctionService(session)
                standings = service.get_auction_standings(current.id, discord_user_id=str(interaction.user.id))
                claim = session.query(AuctionClaim).filter_by(auction_id=current.id, discord_user_id=str(interaction.user.id)).first()

            embed = build_auction_card_embed(current, standings=standings, user_claim=claim)
            page_footer = f"Auction {self.current_index + 1} of {len(self.auctions)} • ID: {current.id}"
            embed.set_footer(text=page_footer)

            if hasattr(interaction, "edit_original_response"):
                try:
                    await interaction.edit_original_response(embed=embed, view=self)
                    return
                except Exception:
                    pass
            await interaction.followup.send(embed=embed, view=self, ephemeral=True)
        except Exception as exc:
            logger.error("Error rendering auction card: %s", exc)
            await interaction.followup.send("❌ Error loading auction details.", ephemeral=True)


class GTDBidModal(Modal):
    def __init__(self, auction_id: str, auction_title: str, min_bid: int):
        super().__init__(title="💎 PLACE / UPDATE BID")
        self.auction_id = auction_id

        self.bid_amount_input = TextInput(
            label=f"Bid Amount (Min: {min_bid:,} OBX)",
            placeholder="Enter total OBX to bid (e.g. 500)",
            min_length=1,
            max_length=12,
            required=True,
        )
        self.add_item(self.bid_amount_input)

    async def on_submit(self, interaction: discord.Interaction):
        from apps.obx_tasks.bot.permissions import check_raider_access
        if not await check_raider_access(interaction):
            return

        # Immediate deferral before any parsing or DB work!
        await interaction.response.defer(ephemeral=True)

        try:
            amount_val = int(self.bid_amount_input.value.strip().replace(",", ""))
            if amount_val <= 0:
                raise ValueError("Bid amount must be a positive integer.")
        except ValueError:
            await interaction.followup.send("❌ Please enter a valid positive integer for your bid amount.", ephemeral=True)
            return

        try:
            with session_scope() as session:
                service = AuctionService(session)
                bid = service.place_or_update_gtd_bid(
                    auction_id=self.auction_id,
                    discord_user_id=str(interaction.user.id),
                    bid_amount=amount_val,
                )
                standings = service.get_auction_standings(self.auction_id, discord_user_id=str(interaction.user.id))
                auction = standings["auction"]

            u_rank = standings["user_rank"]
            slots = standings["total_slots"]
            is_win = standings["is_winning"]

            status_banner = "🟢 **CURRENTLY WINNING**" if is_win else f"🔴 **OUTSIDE WINNING POSITIONS** (Top {slots} win)"

            from packages.shared.typography import DIVIDER

            relative_time = f"<t:{int(auction.ends_at.timestamp())}:R>" if auction.ends_at else "♾️ No Deadline"
            rank_str = f"#{u_rank}" if u_rank else "Unranked"

            desc_lines = [
                "Your bid has been successfully secured.",
                "",
                DIVIDER,
                "",
                "💎 **YOUR BID**",
                f"{bid.bid_amount:,} OBX",
                "",
                "🎟️ **WHITELIST SPOTS**",
                f"{slots}",
                "",
                "📍 **YOUR CURRENT POSITION**",
                rank_str,
                "",
                "⏳ **AUCTION ENDS**",
                relative_time,
                "",
                DIVIDER,
                "",
                "*Your position can change as new bids arrive.*",
            ]

            embed = discord.Embed(
                title="💎 BID PLACED",
                description="\n".join(desc_lines),
                color=COLOR_PURPLE,
            )
            embed.set_footer(text="✦ OBX WHITELIST AUCTIONS")
            view = AuctionBidSuccessView(auction_id=str(auction.id))

            # Refresh public auction card in #auctions in place
            if interaction.guild:
                try:
                    from apps.obx_tasks.bot.announcement_service import announce_auction
                    await announce_auction(auction, interaction.guild, interaction.client)
                except Exception as ann_err:
                    logger.warning("Could not refresh live auction card after bid: %s", ann_err)

            try:
                await interaction.followup.send(embed=embed, view=view, ephemeral=True)
            except Exception as discord_err:
                logger.warning("Discord UI followup failed after successful bid: %s", discord_err)
        except AuctionError as exc:
            try:
                await interaction.followup.send(f"❌ {exc.message}", ephemeral=True)
            except Exception:
                pass
        except Exception as exc:
            logger.error("Error submitting GTD bid: %s", exc)
            try:
                await interaction.followup.send("❌ An unexpected error occurred while processing your bid.", ephemeral=True)
            except Exception:
                pass


class AuctionBidSuccessView(View):
    def __init__(self, auction_id: str):
        super().__init__(timeout=180)
        self.auction_id = auction_id

    @discord.ui.button(label="View My Position", style=discord.ButtonStyle.primary, row=0)
    async def position_btn(self, interaction: discord.Interaction, button: Button):
        await handle_view_my_auction_position(interaction, self.auction_id)

    @discord.ui.button(label="Close", style=discord.ButtonStyle.secondary, row=0)
    async def close_btn(self, interaction: discord.Interaction, button: Button):
        try:
            await interaction.message.delete()
        except Exception:
            try:
                await interaction.response.defer()
            except Exception:
                pass


class AuctionActionSuccessView(AuctionBidSuccessView):
    def __init__(self, auction_id: str = ""):
        super().__init__(auction_id=auction_id)

    @discord.ui.button(label="My Bids & Wins", style=discord.ButtonStyle.primary, row=0)
    async def my_bids_btn(self, interaction: discord.Interaction, button: Button):
        await handle_my_auction_activity(interaction)

    @discord.ui.button(label="Browse Auctions", style=discord.ButtonStyle.secondary, row=0)
    async def browse_btn(self, interaction: discord.Interaction, button: Button):
        await handle_browse_auctions(interaction)

    @discord.ui.button(label="My Wallet", style=discord.ButtonStyle.secondary, row=0)
    async def wallet_btn(self, interaction: discord.Interaction, button: Button):
        from apps.obx_tasks.bot.dashboard_views import handle_my_balance
        await handle_my_balance(interaction)


# Unified Minimal Admin Creation Modal (All auctions are standard ranked multi-winner auctions)
class AdminCreateAuctionModal(Modal, title="🎟️ LAUNCH WHITELIST AUCTION"):
    def __init__(self):
        super().__init__()
        self.project_x_url = TextInput(
            label="Project X / Twitter Profile URL",
            placeholder="https://x.com/AstralSentinels or @AstralSentinels",
            max_length=1024,
            required=True,
        )
        self.slots_and_price = TextInput(
            label="Whitelist Spots",
            placeholder="e.g. 5 (or 5 / 10 for spots & min bid)",
            default="1",
            max_length=32,
            required=True,
        )
        self.wl_type = TextInput(
            label="Type",
            placeholder="e.g. GTD, FCFS, OG",
            default="WL",
            max_length=32,
            required=False,
        )
        self.duration = TextInput(
            label="Duration",
            placeholder="e.g. 19h, 24h, 3d",
            max_length=32,
            required=True,
        )

        self.add_item(self.project_x_url)
        self.add_item(self.slots_and_price)
        self.add_item(self.wl_type)
        self.add_item(self.duration)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        try:
            winners, bid_min = parse_slots_and_price(self.slots_and_price.value)
            from packages.shared.utils import parse_duration_or_datetime
            ends_at = parse_duration_or_datetime(self.duration.value)

            from apps.obx_tasks.services.raider_service import normalize_twitter_input
            handle, canonical_url = normalize_twitter_input(self.project_x_url.value.strip())
        except ValueError as val_err:
            await interaction.followup.send(f"❌ Input Error: {str(val_err)}", ephemeral=True)
            return
        except Exception as exc:
            await interaction.followup.send(f"❌ Error processing inputs: {str(exc)}", ephemeral=True)
            return

        raw_type = (self.wl_type.value or "").strip() if hasattr(self, "wl_type") and self.wl_type.value else ""
        wl_type = raw_type if raw_type else "GTD"

        payload = {
            "title": f"@{handle}",
            "reward_title": wl_type,
            "total_slots": winners,
            "price_or_min_bid": bid_min,
            "ends_at": ends_at,
            "project_x_url": canonical_url,
            "creator_id": str(interaction.user.id),
            "handle": f"@{handle}",
        }

        # Fetch preview metadata
        try:
            from apps.obx_tasks.services.url_preview_service import UrlPreviewService
            preview_meta = await UrlPreviewService.fetch_preview(canonical_url)
        except Exception as prev_err:
            logger.warning("Could not fetch X preview for %s: %s", canonical_url, prev_err)
            preview_meta = None

        if preview_meta and preview_meta.author:
            payload["title"] = preview_meta.author

        if preview_meta and preview_meta.status == "SUCCESS" and preview_meta.description and preview_meta.description.strip():
            await render_auction_preview_and_confirm(interaction, payload, preview_meta=preview_meta)
        else:
            await render_auction_fetch_failed(interaction, payload, canonical_url, preview_meta=preview_meta)


async def render_auction_preview_and_confirm(
    interaction: discord.Interaction,
    payload: dict,
    preview_meta=None,
    manual_description: Optional[str] = None,
):
    """Renders the preview card with confirmation buttons before publishing."""
    bio = manual_description or (
        preview_meta.description.strip()
        if preview_meta and preview_meta.description
        else "Exclusive community whitelist opportunity."
    )
    display_name = (
        preview_meta.author
        if preview_meta and preview_meta.author
        else payload.get("handle", "").lstrip("@")
    )

    from apps.obx_tasks.services.auction_service import resolve_auction_preview_image
    preview_image_url = resolve_auction_preview_image(
        banner_url=preview_meta.banner_url if preview_meta else None,
        og_image_url=preview_meta.image_url if preview_meta else None,
        avatar_url=preview_meta.avatar_url if preview_meta else None,
    )

    # In-memory transient Auction object for building identical preview embed
    preview_auc = Auction(
        id=uuid.uuid4(),
        title=payload["title"],
        reward_title=payload["reward_title"],
        description=bio,
        auction_type=AuctionType.GTD,
        total_slots=payload["total_slots"],
        allocated_slots=0,
        price_or_min_bid=payload["price_or_min_bid"],
        status=AuctionStatus.ACTIVE,
        ends_at=payload["ends_at"],
        project_x_url=payload["project_x_url"],
        preview_x_handle=payload.get("handle"),
        preview_x_display_name=display_name,
        preview_x_avatar_url=preview_meta.avatar_url if preview_meta else None,
        preview_x_banner_url=preview_meta.banner_url if preview_meta else None,
        preview_image_url=preview_image_url,
    )

    embed = build_auction_notification_embed(preview_auc, standings={"ranked_bids": []})

    view = AuctionCreateConfirmView(
        payload=payload,
        bio=bio,
        preview_meta=preview_meta,
        preview_image_url=preview_image_url,
    )

    content = "📋 **AUCTION PREVIEW** — Review the details below before publishing to the community:"
    if _is_response_done(interaction):
        await interaction.followup.send(content=content, embed=embed, view=view, ephemeral=True)
    else:
        await interaction.response.send_message(content=content, embed=embed, view=view, ephemeral=True)


class AuctionCreateConfirmView(View):
    """View presenting the confirm/cancel actions on the auction preview."""
    def __init__(
        self,
        payload: dict,
        bio: str,
        preview_meta=None,
        preview_image_url: Optional[str] = None,
    ):
        super().__init__(timeout=600)
        self.payload = payload
        self.bio = bio
        self.preview_meta = preview_meta
        self.preview_image_url = preview_image_url

    @discord.ui.button(label="PUBLISH AUCTION", style=discord.ButtonStyle.success, row=0)
    async def confirm_publish(self, interaction: discord.Interaction, button: Button):
        await interaction.response.defer(ephemeral=True)

        try:
            with session_scope() as session:
                service = AuctionService(session)
                auc = service.create_auction(
                    title=self.payload["title"],
                    reward_title=self.payload["reward_title"],
                    description=self.bio,
                    auction_type=AuctionType.GTD,
                    total_slots=self.payload["total_slots"],
                    price_or_min_bid=self.payload["price_or_min_bid"],
                    created_by=self.payload["creator_id"],
                    ends_at=self.payload["ends_at"],
                    project_x_url=self.payload["project_x_url"],
                    preview_image_url=self.preview_image_url,
                    preview_x_handle=self.payload.get("handle"),
                    preview_x_display_name=self.preview_meta.author if self.preview_meta else None,
                    preview_x_avatar_url=self.preview_meta.avatar_url if self.preview_meta else None,
                    preview_x_banner_url=self.preview_meta.banner_url if self.preview_meta else None,
                    preview_x_bio=self.bio,
                )
                auc_id = str(auc.id)
                db_auc = service.get_auction(auc_id)

            # Auto-announce to configured Auctions channel & refresh Auction Center
            ann_result = ""
            try:
                if interaction.guild:
                    from apps.obx_tasks.bot.announcement_service import announce_auction, deploy_or_update_auction_center
                    await deploy_or_update_auction_center(interaction.guild, interaction.client)
                    ok_ann, msg_ann = await announce_auction(db_auc, interaction.guild, interaction.client)
                    ann_result = f"\n\n📢 **Live Card:** {msg_ann}"
            except Exception as ann_err:
                logger.warning("Auto-announcement for auction %s failed: %s", db_auc.id, ann_err)
                ann_result = f"\n\n⚠️ **Card Publishing Notice:** `{str(ann_err)}`"

            success_embed = discord.Embed(
                title="🚀 Whitelist Auction Published!",
                description=f"Successfully published **{db_auc.title}** to the auctions channel.{ann_result}",
                color=COLOR_GREEN,
            )
            for child in self.children:
                child.disabled = True
            await interaction.followup.send(embed=success_embed, ephemeral=True)
        except Exception as exc:
            logger.error("Error confirming auction creation: %s", exc)
            await interaction.followup.send(f"❌ Error publishing auction: {str(exc)}", ephemeral=True)

    @discord.ui.button(label="CANCEL", style=discord.ButtonStyle.secondary, row=0)
    async def cancel_publish(self, interaction: discord.Interaction, button: Button):
        for child in self.children:
            child.disabled = True
        await interaction.response.send_message("❌ Auction creation cancelled.", ephemeral=True)


async def render_auction_fetch_failed(
    interaction: discord.Interaction,
    payload: dict,
    canonical_url: str,
    preview_meta=None,
):
    """Renders the fetch error screen with Retry and Manual Description options."""
    embed = discord.Embed(
        title="⚠️ COULD NOT RETRIEVE PROJECT METADATA",
        description=(
            f"Unable to automatically retrieve project information from:\n"
            f"**{canonical_url}**\n\n"
            f"• Ensure the X/Twitter profile exists and is public.\n"
            f"• You can retry the automatic fetch, or enter the project description manually."
        ),
        color=COLOR_RED,
    )
    view = AuctionCreateRetryView(payload=payload, canonical_url=canonical_url, preview_meta=preview_meta)
    if _is_response_done(interaction):
        await interaction.followup.send(embed=embed, view=view, ephemeral=True)
    else:
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)


class AuctionCreateRetryView(View):
    """View displayed when X profile metadata fetch fails."""
    def __init__(self, payload: dict, canonical_url: str, preview_meta=None):
        super().__init__(timeout=600)
        self.payload = payload
        self.canonical_url = canonical_url
        self.preview_meta = preview_meta

    @discord.ui.button(label="RETRY FETCH", style=discord.ButtonStyle.primary, row=0)
    async def retry_fetch(self, interaction: discord.Interaction, button: Button):
        await interaction.response.defer(ephemeral=True)
        try:
            from apps.obx_tasks.services.url_preview_service import UrlPreviewService
            new_meta = await UrlPreviewService.fetch_preview(self.canonical_url)
        except Exception as prev_err:
            logger.warning("Could not retry fetch X preview for %s: %s", self.canonical_url, prev_err)
            new_meta = None

        if new_meta and new_meta.status == "SUCCESS" and new_meta.description and new_meta.description.strip():
            await render_auction_preview_and_confirm(interaction, self.payload, preview_meta=new_meta)
        else:
            await render_auction_fetch_failed(interaction, self.payload, self.canonical_url, preview_meta=new_meta)

    @discord.ui.button(label="ENTER DESCRIPTION MANUALLY", style=discord.ButtonStyle.secondary, row=0)
    async def manual_desc(self, interaction: discord.Interaction, button: Button):
        modal = ManualDescriptionModal(payload=self.payload, preview_meta=self.preview_meta)
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="CANCEL", style=discord.ButtonStyle.danger, row=0)
    async def cancel(self, interaction: discord.Interaction, button: Button):
        for child in self.children:
            child.disabled = True
        await interaction.response.send_message("❌ Auction creation cancelled.", ephemeral=True)


class ManualDescriptionModal(Modal, title="✏️ PROJECT DESCRIPTION"):
    """Fallback modal allowing admin to enter a project description manually when X fetch fails."""
    def __init__(self, payload: dict, preview_meta=None):
        super().__init__()
        self.payload = payload
        self.preview_meta = preview_meta

        self.description = TextInput(
            label="Project Description",
            placeholder="Enter the project summary or whitelist description...",
            style=discord.TextStyle.paragraph,
            required=True,
            max_length=1000,
        )
        self.add_item(self.description)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        manual_desc = self.description.value.strip()
        if not manual_desc:
            await interaction.followup.send("❌ Description cannot be empty.", ephemeral=True)
            return

        await render_auction_preview_and_confirm(
            interaction,
            self.payload,
            preview_meta=self.preview_meta,
            manual_description=manual_desc,
        )


class AdminEditAuctionModal(Modal, title="✏️ EDIT WHITELIST AUCTION"):
    def __init__(self, auction: Auction):
        super().__init__()
        self.auction_id = str(auction.id)
        self.description_input = TextInput(
            label="Project Description",
            default=auction.description or "",
            style=discord.TextStyle.paragraph,
            max_length=1000,
            required=True,
        )
        self.project_x_url = TextInput(
            label="Project Twitter / X Profile URL (Optional)",
            default=auction.project_x_url or "",
            max_length=1024,
            required=False,
        )
        self.slots_and_price = TextInput(
            label="Spots / Min Bid (OBX)",
            default=f"{auction.total_slots} / {auction.price_or_min_bid}",
            max_length=32,
            required=True,
        )
        self.duration_input = TextInput(
            label="Extend Duration / End Time (UTC)",
            placeholder="e.g. 24h, 2d, or leave blank to keep unchanged",
            max_length=32,
            required=False,
        )

        self.add_item(self.description_input)
        self.add_item(self.project_x_url)
        self.add_item(self.slots_and_price)
        self.add_item(self.duration_input)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        try:
            winners, bid_min = parse_slots_and_price(self.slots_and_price.value)
            ends_at = None
            if self.duration_input.value.strip():
                from packages.shared.utils import parse_duration_or_datetime
                ends_at = parse_duration_or_datetime(self.duration_input.value)
        except ValueError as val_err:
            await interaction.followup.send(f"❌ Input Error: {str(val_err)}", ephemeral=True)
            return

        raw_x_url = self.project_x_url.value.strip() if self.project_x_url.value else None

        try:
            with session_scope() as session:
                service = AuctionService(session)
                existing = service.get_auction(self.auction_id)
                x_url_changed = (raw_x_url != existing.project_x_url)

                service.edit_auction(
                    auction_id=self.auction_id,
                    changed_by=str(interaction.user.id),
                    title=existing.title,
                    description=self.description_input.value.strip(),
                    project_x_url=raw_x_url,
                    total_slots=winners,
                    price_or_min_bid=bid_min,
                    ends_at=ends_at,
                )

            # Re-fetch preview if X URL was updated or set
            if raw_x_url and x_url_changed:
                try:
                    from apps.obx_tasks.services.url_preview_service import UrlPreviewService
                    preview_meta = await UrlPreviewService.fetch_preview(raw_x_url)
                    if preview_meta:
                        with session_scope() as session:
                            service = AuctionService(session)
                            service.update_auction_preview(
                                auction_id=self.auction_id,
                                project_x_url=raw_x_url,
                                handle=preview_meta.handle,
                                display_name=preview_meta.author,
                                avatar_url=preview_meta.avatar_url,
                                banner_url=preview_meta.banner_url,
                                og_image_url=preview_meta.image_url,
                                bio=preview_meta.description,
                            )
                except Exception as prev_err:
                    logger.warning("Could not refresh preview on edit for auction %s: %s", self.auction_id, prev_err)

            # Refresh live card in place
            ann_result = ""
            with session_scope() as session:
                service = AuctionService(session)
                updated_auc = service.get_auction(self.auction_id)

            if interaction.guild:
                try:
                    from apps.obx_tasks.bot.announcement_service import announce_auction
                    ok_ann, msg_ann = await announce_auction(updated_auc, interaction.guild, interaction.client)
                    ann_result = f"\n\n📢 **Live Card:** {msg_ann}"
                except Exception as ann_err:
                    logger.warning("Could not update live card on edit: %s", ann_err)

            embed = discord.Embed(
                title="✏️ Whitelist Auction Updated!",
                description=f"Successfully updated **{updated_auc.title}**.{ann_result}",
                color=COLOR_GOLD,
            )
            await interaction.followup.send(embed=embed, ephemeral=True)
        except Exception as exc:
            logger.error("Error editing auction: %s", exc)
            await interaction.followup.send(f"❌ Error editing auction: {str(exc)}", ephemeral=True)


AdminCreateGTDModal = AdminCreateAuctionModal
AdminCreateFCFSModal = AdminCreateAuctionModal


class AdminGrantRewardModal(Modal, title="🎁 GRANT CUSTOM REWARD"):
    user_id_input = TextInput(label="Discord User ID or @Mention", placeholder="e.g. 943941681512874014", max_length=64, required=True)
    amount_input = TextInput(label="OBX Reward Amount", placeholder="e.g. 500", max_length=12, required=True)
    reason_input = TextInput(label="Reason / Notes", placeholder="e.g. Community contest winner", max_length=255, required=True)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        clean_user_id = self.user_id_input.value.strip().replace("<@", "").replace(">", "").replace("!", "")
        try:
            amt = int(self.amount_input.value.strip().replace(",", ""))
            if amt <= 0:
                raise ValueError("Amount must be positive.")
        except ValueError:
            await interaction.followup.send("❌ Please provide a valid positive integer for amount.", ephemeral=True)
            return

        try:
            with session_scope() as session:
                service = AuctionService(session)
                entry = service.grant_custom_reward(
                    admin_discord_id=str(interaction.user.id),
                    target_discord_id=clean_user_id,
                    amount=amt,
                    reason=self.reason_input.value.strip(),
                )

            embed = discord.Embed(
                title="🎁 Custom Reward Granted!",
                description=(
                    f"Successfully credited **{amt:,} OBX** to <@{clean_user_id}>.\n\n"
                    f"📝 **Reason:** {self.reason_input.value.strip()}\n"
                    f"🧾 **Ledger Transaction ID:** `{entry.id}`\n"
                    f"👑 **Authorized By:** <@{interaction.user.id}>"
                ),
                color=COLOR_GREEN,
            )
            await interaction.followup.send(embed=embed, ephemeral=True)

            # Try to notify the user via DM
            try:
                guild = interaction.guild
                if guild:
                    member = guild.get_member(int(clean_user_id))
                    if member:
                        dm_embed = discord.Embed(
                            title="🎉 You Received a Custom OBX Reward!",
                            description=(
                                f"You have been awarded **{amt:,} OBX** by server administration.\n\n"
                                f"📝 **Reason:** {self.reason_input.value.strip()}"
                            ),
                            color=COLOR_GOLD,
                        )
                        await member.send(embed=dm_embed)
            except Exception:
                pass
        except AuctionError as exc:
            await interaction.followup.send(f"❌ {exc.message}", ephemeral=True)
        except Exception as exc:
            logger.error("Error granting custom reward: %s", exc)
            await interaction.followup.send("❌ Error granting reward.", ephemeral=True)


class AdminCreateAuctionSelectView(View):
    def __init__(self):
        super().__init__(timeout=120)

    @discord.ui.button(label="Create Whitelist Auction", style=discord.ButtonStyle.primary, row=0)
    async def btn_create(self, interaction: discord.Interaction, button: Button):
        modal = AdminCreateAuctionModal()
        await interaction.response.send_modal(modal)

    # Backwards compatibility methods
    async def btn_fcfs(self, interaction: discord.Interaction, button: Button):
        modal = AdminCreateAuctionModal()
        await interaction.response.send_modal(modal)

    async def btn_gtd(self, interaction: discord.Interaction, button: Button):
        modal = AdminCreateAuctionModal()
        await interaction.response.send_modal(modal)


async def handle_view_my_auction_position(interaction: discord.Interaction, auction_id: str):
    """Render the user's current standing, rank, and bid in a specific auction."""
    from apps.obx_tasks.bot.permissions import check_raider_access
    if not await check_raider_access(interaction):
        return

    if not _is_response_done(interaction):
        await interaction.response.defer(ephemeral=True)

    try:
        import uuid
        auc_uuid = auction_id if isinstance(auction_id, uuid.UUID) else uuid.UUID(str(auction_id))
        with session_scope() as session:
            service = AuctionService(session)
            standings = service.get_auction_standings(auc_uuid, discord_user_id=str(interaction.user.id))
            auc = standings["auction"]

        embed = discord.Embed(
            title=f"📊 Live Bid Rankings — {auc.title}",
            description=f"**Reward:** {auc.reward_title} • **Available Spots:** `{auc.total_slots}`\nTop {auc.total_slots} unique bidders win whitelist spots at auction close.\n",
            color=COLOR_GOLD,
        )

        bids = standings["ranked_bids"]
        if not bids:
            embed.add_field(name="No Bids Placed Yet", value="Be the first to place a bid and secure the #1 spot!", inline=False)
        else:
            rank_lines = []
            for idx, b in enumerate(bids[:15], start=1):
                medal = MEDALS.get(idx, f"`#{idx}`")
                win_icon = "🟢" if idx <= auc.total_slots else "🔴"
                rank_lines.append(f"{medal} {win_icon} <@{b.discord_user_id}> — **{b.bid_amount:,} OBX**")

            embed.add_field(
                name=f"Top Bidders (Total Bidders: {len(bids)})",
                value="\n".join(rank_lines),
                inline=False,
            )

        if standings.get("user_bid_amount") is not None:
            u_rank = standings["user_rank"]
            u_bid = standings["user_bid_amount"]
            is_win = standings["is_winning"]
            status_text = "🟢 **Winning Position**" if is_win else f"🔴 **Outside Winning Positions** (Cutoff: `{standings['winning_cutoff']:,} OBX`)"
            embed.add_field(
                name="📍 Your Standing",
                value=f"**Rank:** `#{u_rank}` • **Bid:** `{u_bid:,} OBX` • **Status:** {status_text}",
                inline=False,
            )

        embed.set_footer(text="Rankings update dynamically in real time • Pay-As-Bid")
        await interaction.followup.send(embed=embed, ephemeral=True)
    except Exception as exc:
        logger.error("Error viewing rankings: %s", exc)
        await interaction.followup.send("❌ Error fetching live rankings.", ephemeral=True)


# Handlers
async def handle_auction_center(interaction: discord.Interaction):
    """Entry point for the Auction Center."""
    embed = discord.Embed(
        title="🔨 OBX AUCTION CENTER & WHITELIST REWARDS",
        description=(
            "Welcome to the **OBX Whitelist & Reward Center**!\n\n"
            "Compete for exclusive guaranteed whitelist spots in multi-winner ranked auctions or claim instant FCFS opportunities with your OBX balance.\n\n"
            "🔥 **Active Auctions**: View live whitelist opportunities\n"
            "💼 **My Bids & Wins**: Track your locked bids and confirmed passes\n"
            "❓ **Auction Guide**: Learn how ranked bidding and FCFS sales work"
        ),
        color=COLOR_GOLD,
    )
    embed.set_footer(text="Multi-Winner Ranked Bidding • Double-Entry Protected • Integer Precision")
    view = AuctionCenterView()

    if _is_response_done(interaction):
        await interaction.followup.send(embed=embed, view=view, ephemeral=True)
    else:
        try:
            await interaction.response.edit_message(embed=embed, view=view)
        except Exception:
            await interaction.response.send_message(embed=embed, view=view, ephemeral=True)


async def handle_browse_auctions(interaction: discord.Interaction, status: Optional[AuctionStatus] = AuctionStatus.ACTIVE, current_index: int = 0):
    from apps.obx_tasks.bot.permissions import check_raider_access
    if not await check_raider_access(interaction):
        return

    if not _is_response_done(interaction):
        await interaction.response.defer(ephemeral=True)

    try:
        with session_scope() as session:
            service = AuctionService(session)
            auctions, total = service.list_auctions(status=status, limit=50)

        if not auctions:
            embed = discord.Embed(
                title="🔨 NO ACTIVE AUCTIONS",
                description=(
                    "There are currently no active whitelist rewards open for claim or bidding.\n\n"
                    "Earn OBX now through tasks to prepare for upcoming drops!"
                ),
                color=COLOR_DARK,
            )
            embed.set_footer(text="New whitelist opportunities will be announced soon.")
            view = AuctionActionSuccessView()
            await interaction.followup.send(embed=embed, view=view, ephemeral=True)
            return

        current_index = max(0, min(current_index, len(auctions) - 1))
        current = auctions[current_index]

        with session_scope() as session:
            service = AuctionService(session)
            standings = service.get_auction_standings(current.id, discord_user_id=str(interaction.user.id))
            claim = session.query(AuctionClaim).filter_by(auction_id=current.id, discord_user_id=str(interaction.user.id)).first()

        embed = build_auction_card_embed(current, standings=standings, user_claim=claim)
        embed.set_footer(text=f"Auction {current_index + 1} of {len(auctions)} • ID: {current.id}")
        view = AuctionBrowserView(auctions=auctions, current_index=current_index)

        await interaction.followup.send(embed=embed, view=view, ephemeral=True)
    except Exception as exc:
        logger.error("Error browsing auctions: %s", exc)
        await interaction.followup.send("❌ Error loading auctions.", ephemeral=True)


async def handle_my_auction_activity(interaction: discord.Interaction):
    from apps.obx_tasks.bot.permissions import check_raider_access
    if not await check_raider_access(interaction):
        return

    if not _is_response_done(interaction):
        await interaction.response.defer(ephemeral=True)

    try:
        with session_scope() as session:
            service = AuctionService(session)
            activity = service.get_user_auction_activity(str(interaction.user.id))

        embed = discord.Embed(
            title="💼 YOUR AUCTION & WHITELIST ACTIVITY",
            description=f"Summary for <@{interaction.user.id}>:",
            color=COLOR_TEAL,
        )

        # Active Bids
        if activity["active_bids"]:
            bid_lines = []
            for b in activity["active_bids"]:
                bid_lines.append(f"• **{b.auction.title} ({b.auction.reward_title})**: `{b.bid_amount:,} OBX` (Locked)")
            embed.add_field(name="🔒 Active GTD Bids", value="\n".join(bid_lines), inline=False)
        else:
            embed.add_field(name="🔒 Active GTD Bids", value="*No active bids placed.*", inline=False)

        # Confirmed Whitelist Wins & Claims
        if activity["wins"]:
            win_lines = []
            for w in activity["wins"]:
                win_lines.append(f"• ✅ **{w.auction.title} — {w.auction.reward_title}** (Cost: `{w.price_paid:,} OBX`)")
            embed.add_field(name="🏆 Confirmed Whitelist Passes", value="\n".join(win_lines), inline=False)
        else:
            embed.add_field(name="🏆 Confirmed Whitelist Passes", value="*No whitelist passes claimed yet.*", inline=False)

        embed.set_footer(text="All balances and allocations are verified on-chain.")
        view = AuctionActionSuccessView()
        await interaction.followup.send(embed=embed, view=view, ephemeral=True)
    except Exception as exc:
        logger.error("Error loading user auction activity: %s", exc)
        await interaction.followup.send("❌ Error loading your auction activity.", ephemeral=True)
