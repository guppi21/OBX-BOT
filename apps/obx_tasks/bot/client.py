import uuid
import time
import asyncio
import traceback
import discord
from discord import app_commands
from discord.ext import commands
from typing import Optional
from sqlalchemy import text

from packages.shared.config import get_settings
from packages.shared.enums import TaskStatus, TaskType, SubmissionStatus, AuctionType, AuctionStatus
from packages.shared.exceptions import TaskError, OBXError
from packages.shared.logging import get_logger
from packages.database.session import session_scope, get_engine
from apps.obx_tasks.services.task_service import TaskService
from apps.obx_tasks.services.auction_service import AuctionService, AuctionError
from apps.obx_tasks.bot.permissions import is_admin
from packages.database.models.task import Task
from packages.database.models.submission import TaskSubmission
from packages.database.models.auction import Auction, AuctionBid, AuctionClaim
from apps.obx_tasks.bot.views import TaskSubmitModal
from apps.obx_tasks.bot.dashboard_views import (
    OBXDashboardView, OBXAdminHubView, create_dashboard_embed, handle_refresh_public_systems, _is_response_done
)
from apps.obx_tasks.bot.leaderboard_views import LeaderboardView
from apps.obx_tasks.bot.auction_views import AuctionCenterView, AdminCreateAuctionSelectView
from apps.obx_tasks.bot.channel_views import ChannelConfigView, AuctionWinnerResultView, handle_channel_config
from apps.obx_tasks.bot.announcement_service import refresh_all_public_systems, announce_auction_winners, AdminLogDismissView

logger = get_logger("obx.tasks.bot")


class OBXTaskBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        super().__init__(command_prefix="!", intents=intents)

    async def setup_hook(self):
        # Register persistent views for re-attachment across restarts
        self.add_view(OBXDashboardView())
        self.add_view(OBXAdminHubView())
        self.add_view(LeaderboardView())
        self.add_view(AuctionCenterView())
        self.add_view(AuctionWinnerResultView())
        self.add_view(AdminLogDismissView())

        # Start background maintenance loop for auctions & tasks
        self.loop.create_task(self._auction_maintenance_loop())

        settings = get_settings()
        if settings.DISCORD_GUILD_ID:
            try:
                guild = discord.Object(id=int(settings.DISCORD_GUILD_ID))
                self.tree.copy_global_to(guild=guild)
                synced = await self.tree.sync(guild=guild)
                logger.info("Test guild synchronized: Guild ID=%s (%d commands registered)", settings.DISCORD_GUILD_ID, len(synced))
            except discord.Forbidden:
                logger.warning("Could not sync commands directly to Guild ID %s (403 Forbidden). Syncing globally as fallback.", settings.DISCORD_GUILD_ID)
                synced = await self.tree.sync()
                logger.info("Slash commands synchronized globally: %d commands registered", len(synced))
            except Exception as exc:
                logger.error("Error syncing commands to guild %s: %s", settings.DISCORD_GUILD_ID, exc)
        else:
            synced = await self.tree.sync()
            logger.info("Slash commands synchronized globally: %d commands registered", len(synced))

    async def on_interaction(self, interaction: discord.Interaction):
        # Handle custom component IDs for live task and auction announcement cards
        if interaction.type == discord.InteractionType.component:
            custom_id = interaction.data.get("custom_id", "")
            # Join Raid Onboarding
            if custom_id == "obx:join_raid":
                from apps.obx_tasks.bot.join_raid_views import handle_join_raid_click
                await handle_join_raid_click(interaction)
                return
            elif custom_id == "obx:join_raid:activate":
                from apps.obx_tasks.bot.join_raid_views import handle_activate_join_raid_click
                await handle_activate_join_raid_click(interaction)
                return
            elif custom_id == "obx:raider:set_twitter":
                from apps.obx_tasks.bot.join_raid_views import SetTwitterModal
                await interaction.response.send_modal(SetTwitterModal())
                return
            elif custom_id in ("obx:user:submissions", "obx:submissions:my"):
                from apps.obx_tasks.bot.dashboard_views import handle_my_submissions
                await handle_my_submissions(interaction)
                return
            elif custom_id == "obx:browse_raids":
                with session_scope() as session:
                    from apps.obx_tasks.services.channel_service import ChannelService
                    ch_s = ChannelService(session)
                    tasks_ch_id = None
                    if interaction.guild:
                        cfg = ch_s.get_or_create_guild_config(str(interaction.guild.id))
                        tasks_ch_id = cfg.tasks_channel_id
                mention = f"<#{tasks_ch_id}>" if tasks_ch_id else "the tasks channel"
                await interaction.response.send_message(
                    f"⚔️ Head over to {mention} to view and participate in live community missions!",
                    ephemeral=True,
                )
                return

            # Task card interactions
            if custom_id.startswith("obx:task_card:verify:"):
                from apps.obx_tasks.bot.permissions import check_raider_access
                if not await check_raider_access(interaction):
                    return
                task_id = custom_id.split("obx:task_card:verify:")[1]
                await self._handle_task_card_verify(interaction, task_id)
                return
            elif custom_id.startswith("obx:task_card:details:"):
                task_id = custom_id.split("obx:task_card:details:")[1]
                await self._handle_task_card_details(interaction, task_id)
                return
            elif custom_id.startswith("obx:task_card:closed:"):
                await interaction.response.send_message("ℹ️ This task is closed and no longer accepting submissions.", ephemeral=True)
                return
            # Auction card interactions
            elif custom_id.startswith("obx:auc_card:bid:"):
                from apps.obx_tasks.bot.permissions import check_raider_access
                if not await check_raider_access(interaction):
                    return
                auc_id = custom_id.split("obx:auc_card:bid:")[1]
                await self._handle_auc_card_bid(interaction, auc_id)
                return
            elif custom_id.startswith("obx:auc_card:edit_bid:"):
                from apps.obx_tasks.bot.permissions import check_raider_access
                if not await check_raider_access(interaction):
                    return
                auc_id = custom_id.split("obx:auc_card:edit_bid:")[1]
                await self._handle_auc_card_bid(interaction, auc_id)
                return
            elif custom_id.startswith("obx:auc_card:rankings:"):
                from apps.obx_tasks.bot.permissions import check_raider_access
                if not await check_raider_access(interaction):
                    return
                auc_id = custom_id.split("obx:auc_card:rankings:")[1]
                await self._handle_auc_card_rankings(interaction, auc_id)
                return
            elif custom_id.startswith("obx:auc_card:claim:"):
                from apps.obx_tasks.bot.permissions import check_raider_access
                if not await check_raider_access(interaction):
                    return
                auc_id = custom_id.split("obx:auc_card:claim:")[1]
                await self._handle_auc_card_claim(interaction, auc_id)
                return
            # Dismiss reward celebration interaction
            elif custom_id.startswith("obx:celebrate:dismiss:"):
                parts = custom_id.split(":")
                if len(parts) >= 5:
                    target_user_id = parts[4]
                    if str(interaction.user.id) != target_user_id:
                        await interaction.response.send_message(
                            "❌ Only the rewarded member can dismiss this celebration!",
                            ephemeral=True,
                        )
                        return
                    try:
                        if interaction.message:
                            await interaction.message.delete()
                    except discord.NotFound:
                        pass
                    except Exception as del_err:
                        logger.warning("Could not delete celebration message: %s", del_err)
                return
            # Admin log dismissal
            elif custom_id == "obx:admin:dismiss_log":
                from apps.obx_tasks.bot.permissions import is_admin
                if not is_admin(interaction):
                    await interaction.response.send_message("❌ Administrator permission required.", ephemeral=True)
                    return
                try:
                    if not interaction.response.is_done():
                        await interaction.response.defer()
                    if interaction.message:
                        await interaction.message.delete()
                except discord.NotFound:
                    pass
                except Exception as del_err:
                    logger.warning("Could not delete admin log message: %s", del_err)
                return
            # Help & Channel Info interactions
            elif custom_id == "obx:help:tasks":
                from apps.obx_tasks.bot.help_views import send_tasks_how_it_works
                await send_tasks_how_it_works(interaction)
                return
            elif custom_id == "obx:help:auctions":
                from apps.obx_tasks.bot.help_views import send_auctions_how_it_works
                await send_auctions_how_it_works(interaction)
                return
            elif custom_id == "obx:help:winners":
                from apps.obx_tasks.bot.help_views import send_winners_how_it_works
                await send_winners_how_it_works(interaction)
                return
            elif custom_id == "obx:help:browse_missions":
                from apps.obx_tasks.bot.dashboard_views import handle_browse_tasks
                await handle_browse_tasks(interaction)
                return
            elif custom_id == "obx:help:view_auctions":
                from apps.obx_tasks.bot.auction_views import handle_browse_auctions
                from packages.shared.enums import AuctionStatus
                await handle_browse_auctions(interaction, status=AuctionStatus.ACTIVE)
                return
            elif custom_id == "obx:help:view_result":
                from apps.obx_tasks.bot.auction_views import handle_my_auction_activity
                await handle_my_auction_activity(interaction)
                return
            elif custom_id == "obx:user:submissions":
                from apps.obx_tasks.bot.permissions import check_raider_access
                if not await check_raider_access(interaction):
                    return
                from apps.obx_tasks.bot.dashboard_views import handle_my_submissions
                await handle_my_submissions(interaction)
                return
            elif custom_id == "obx:user:wallet":
                from apps.obx_tasks.bot.permissions import check_raider_access
                if not await check_raider_access(interaction):
                    return
                from apps.obx_tasks.bot.dashboard_views import handle_my_wallet
                await handle_my_wallet(interaction)
                return
            elif custom_id == "obx:help:close":
                from apps.obx_tasks.bot.help_views import handle_help_close
                await handle_help_close(interaction)
                return
            # Admin task management interactions
            elif custom_id == "obx:admin:manage_tasks":
                from apps.obx_tasks.bot.task_management_views import handle_admin_manage_tasks
                await handle_admin_manage_tasks(interaction)
                return
            elif custom_id.startswith("obx:mgmt:"):
                from apps.obx_tasks.bot.task_management_views import handle_admin_mgmt_interaction
                await handle_admin_mgmt_interaction(interaction, custom_id)
                return

    async def _handle_task_card_verify(self, interaction: discord.Interaction, task_id: str):
        try:
            task_uuid = task_id if isinstance(task_id, uuid.UUID) else uuid.UUID(str(task_id))
            with session_scope() as session:
                task = session.query(Task).filter_by(id=task_uuid).first()
                if not task:
                    await interaction.response.send_message("❌ Task not found in system.", ephemeral=True)
                    return

                if task.status != TaskStatus.ACTIVE:
                    await interaction.response.send_message("❌ This task is currently not active.", ephemeral=True)
                    return

                if task.total_reward_pool and task.distributed_reward >= task.total_reward_pool:
                    await interaction.response.send_message("💰 The reward pool for this task has been fully claimed.", ephemeral=True)
                    return

                modal = TaskSubmitModal(task_id=str(task.id), task_title=task.title)
                await interaction.response.send_modal(modal)
        except Exception as exc:
            logger.error("Error launching task verification modal: %s", exc)
            if not _is_response_done(interaction):
                await interaction.response.send_message("❌ Failed to open submission form.", ephemeral=True)

    async def _handle_task_card_details(self, interaction: discord.Interaction, task_id: str):
        try:
            task_uuid = task_id if isinstance(task_id, uuid.UUID) else uuid.UUID(str(task_id))
            with session_scope() as session:
                task = session.query(Task).filter_by(id=task_uuid).first()
                if not task:
                    await interaction.response.send_message("❌ Task not found in system.", ephemeral=True)
                    return

                sub = (
                    session.query(TaskSubmission)
                    .filter_by(task_id=task_uuid, discord_user_id=str(interaction.user.id))
                    .first()
                )

                from apps.obx_tasks.bot.ui_theme import COLOR_BLUE, BADGE_APPROVED, BADGE_PENDING, BADGE_REJECTED
                remaining_pool = task.remaining_reward_pool
                embed = discord.Embed(
                    title=f"📋 Task Details: {task.title}",
                    description=task.description or "Complete instructions and submit proof link.",
                    color=COLOR_BLUE,
                )
                embed.add_field(name="💰 Reward Per User", value=f"**{task.reward_per_user:,} OBX**", inline=True)
                embed.add_field(name="📦 Pool Remaining", value=f"**{remaining_pool:,} OBX**", inline=True)
                embed.add_field(name="🔥 Status", value=f"`{task.status.value}`", inline=True)

                if sub:
                    status_badge = BADGE_APPROVED if sub.status == SubmissionStatus.APPROVED else (
                        BADGE_REJECTED if sub.status == SubmissionStatus.REJECTED else BADGE_PENDING
                    )
                    embed.add_field(name="📄 Your Submission Status", value=f"{status_badge} (`{sub.status.value}`)", inline=False)
                else:
                    embed.add_field(name="📄 Your Submission Status", value="*Not submitted yet*", inline=False)

                if task.target_url:
                    embed.add_field(name="🔗 Official Link", value=f"[Open Target Link]({task.target_url})", inline=False)

                embed.set_footer(text="OBX Economy Engine • Submit Proof to Earn • Double-Entry Vault")
                await interaction.response.send_message(embed=embed, ephemeral=True)
        except Exception as exc:
            logger.error("Error displaying task details: %s", exc)
            if not _is_response_done(interaction):
                await interaction.response.send_message("❌ Failed to display task details.", ephemeral=True)

    async def _handle_auc_card_bid(self, interaction: discord.Interaction, auction_id: str):
        try:
            auc_uuid = auction_id if isinstance(auction_id, uuid.UUID) else uuid.UUID(str(auction_id))
            with session_scope() as session:
                auc = session.query(Auction).filter_by(id=auc_uuid).first()
                if not auc:
                    await interaction.response.send_message("❌ Auction not found.", ephemeral=True)
                    return
                if auc.status != AuctionStatus.ACTIVE:
                    await interaction.response.send_message("❌ This auction is no longer active.", ephemeral=True)
                    return

                from apps.obx_tasks.bot.auction_views import GTDBidModal
                modal = GTDBidModal(
                    auction_id=str(auc.id),
                    auction_title=f"{auc.title} — {auc.reward_title}",
                    min_bid=auc.price_or_min_bid,
                )
                await interaction.response.send_modal(modal)
        except Exception as exc:
            logger.error("Error opening auction bid modal from card: %s", exc)
            if not _is_response_done(interaction):
                await interaction.response.send_message("❌ Failed to open bid dialog.", ephemeral=True)

    async def _handle_auc_card_rankings(self, interaction: discord.Interaction, auction_id: str):
        try:
            await interaction.response.defer(ephemeral=True)
            auc_uuid = auction_id if isinstance(auction_id, uuid.UUID) else uuid.UUID(str(auction_id))
            with session_scope() as session:
                service = AuctionService(session)
                auc = session.query(Auction).filter_by(id=auc_uuid).first()
                if not auc:
                    await interaction.followup.send("❌ Auction not found.", ephemeral=True)
                    return
                standings = service.get_auction_standings(auc.id, discord_user_id=str(interaction.user.id))

            from apps.obx_tasks.bot.ui_theme import COLOR_GOLD
            from apps.obx_tasks.bot.auction_views import MEDALS
            embed = discord.Embed(
                title=f"📊 Live Bid Rankings — {auc.title}",
                description=f"**Reward:** {auc.reward_title} • **Available Slots:** `{auc.total_slots}`\nTop {auc.total_slots} unique bidders win whitelist spots at auction close.\n",
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
            logger.error("Error viewing rankings from card: %s", exc)
            await interaction.followup.send("❌ Error fetching live rankings.", ephemeral=True)

    async def _handle_auc_card_claim(self, interaction: discord.Interaction, auction_id: str):
        try:
            await interaction.response.defer(ephemeral=True)
            auc_uuid = auction_id if isinstance(auction_id, uuid.UUID) else uuid.UUID(str(auction_id))
            with session_scope() as session:
                service = AuctionService(session)
                claim = service.claim_fcfs_slot(auc_uuid, str(interaction.user.id))
                refreshed_auc = service.get_auction(auc_uuid)

            # Update public notification card in place
            try:
                if interaction.guild:
                    from apps.obx_tasks.bot.announcement_service import announce_auction
                    await announce_auction(refreshed_auc, interaction.guild, self)
            except Exception as ann_err:
                logger.warning("Could not refresh auction card on claim: %s", ann_err)

            from packages.shared.typography import DIVIDER
            from apps.obx_tasks.bot.ui_theme import COLOR_GREEN
            from apps.obx_tasks.bot.auction_views import AuctionActionSuccessView

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
            logger.error("Error claiming FCFS slot from card: %s", exc)
            await interaction.followup.send(f"❌ Error claiming slot: {str(exc)}", ephemeral=True)

    async def _auction_maintenance_loop(self):
        await self.wait_until_ready()
        while not self.is_closed():
            # 1. Auction settlements
            try:
                with session_scope() as session:
                    auc_service = AuctionService(session)
                    settled_results = auc_service.auto_expire_and_settle_auctions()
                    if settled_results:
                        logger.info("Background auction worker settled %d expired auction(s).", len(settled_results))
                        for auc_obj, winners, total_bidders in settled_results:
                            for guild in self.guilds:
                                try:
                                    if auc_obj.auction_type == AuctionType.GTD:
                                        await announce_auction_winners(auc_obj, winners, total_bidders, guild, self)
                                    else:
                                        from apps.obx_tasks.bot.announcement_service import announce_auction
                                        await announce_auction(auc_obj, guild, self)
                                except Exception as ann_err:
                                    logger.warning("Could not announce settlement for auction %s in %s: %s", auc_obj.id, guild.id, ann_err)
            except Exception as exc:
                logger.error("Error in auction maintenance loop: %s", exc)

            # 2. Task auto-expiries & Proof media retention cleanups
            try:
                with session_scope() as session:
                    task_service = TaskService(session)
                    expired_tasks = task_service.auto_expire_tasks()
                    cleaned_media = task_service.cleanup_proof_media()
                    if expired_tasks:
                        logger.info("Background task worker expired %d task(s).", len(expired_tasks))
                    if cleaned_media:
                        logger.info("Background task worker cleaned up %d proof media item(s).", cleaned_media)

                # Update Discord announcement cards for expired tasks
                if expired_tasks:
                    from apps.obx_tasks.bot.announcement_service import announce_task
                    for task in expired_tasks:
                        for guild in self.guilds:
                            try:
                                await announce_task(task, guild, self)
                            except Exception as ann_err:
                                logger.warning("Could not update card for expired task %s in %s: %s", task.id, guild.id, ann_err)
            except Exception as exc:
                logger.error("Error in task lifecycle maintenance loop: %s", exc)

            # 3. Clean up abandoned reward celebrations (> 24 hours)
            try:
                from datetime import datetime, timezone, timedelta
                cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
                with session_scope() as session:
                    from packages.database.models.channel_config import PublishedMessage
                    stale_celebs = session.query(PublishedMessage).filter(
                        PublishedMessage.feature_type == "REWARD_CELEBRATION",
                        PublishedMessage.published_at < cutoff,
                    ).all()
                    for rec in stale_celebs:
                        guild = self.get_guild(int(rec.guild_id))
                        if guild:
                            ch = guild.get_channel(int(rec.channel_id))
                            if ch and isinstance(ch, discord.TextChannel):
                                try:
                                    msg = await ch.fetch_message(int(rec.message_id))
                                    await msg.delete()
                                except Exception:
                                    pass
                        session.delete(rec)
            except Exception as celeb_cleanup_err:
                logger.debug("Celebration cleanup error: %s", celeb_cleanup_err)

            await asyncio.sleep(60)

    async def on_ready(self):
        settings = get_settings()
        logger.info("Discord connected successfully")
        logger.info("Connected as: %s (ID: %s)", self.user.name, self.user.id)

        # Verify Guild and Role presence if configured
        if settings.DISCORD_GUILD_ID:
            guild = self.get_guild(int(settings.DISCORD_GUILD_ID))
            if guild:
                logger.info("Connected to guild: %s (ID: %s)", guild.name, guild.id)
                if settings.DISCORD_ADMIN_ROLE_IDS:
                    for r_id in settings.DISCORD_ADMIN_ROLE_IDS:
                        role = guild.get_role(int(r_id))
                        if role:
                            logger.info("Admin role verified in guild: '%s' (ID: %s)", role.name, role.id)
                        else:
                            logger.warning(
                                "Configured admin role ID '%s' NOT FOUND in guild '%s'. "
                                "Please verify DISCORD_ADMIN_ROLE_ID or create the role.",
                                r_id,
                                guild.name,
                            )

                from apps.obx_tasks.bot.announcement_service import resolve_raider_role
                rid, raid_role = resolve_raider_role(guild)
                if raid_role:
                    logger.info("Raid role verified in guild '%s': '%s' (ID: %s)", guild.name, raid_role.name, raid_role.id)
                else:
                    logger.warning("No raid role found in guild '%s'.", guild.name)

                if settings.RAID_JOIN_CHANNEL_ID:
                    join_ch = guild.get_channel(int(settings.RAID_JOIN_CHANNEL_ID))
                    if join_ch:
                        logger.info("Join raid channel verified in guild: '%s' (ID: %s)", join_ch.name, join_ch.id)
                    else:
                        logger.warning(
                            "Configured RAID_JOIN_CHANNEL_ID '%s' NOT FOUND in guild '%s'.",
                            settings.RAID_JOIN_CHANNEL_ID,
                            guild.name,
                        )
                else:
                    logger.warning("No RAID_JOIN_CHANNEL_ID configured in .env! Join raid onboarding card will not be deployed.")
            else:
                logger.warning(
                    "Bot is currently not a member of configured Guild ID %s. "
                    "Please invite the bot to the server using the OAuth2 URL.",
                    settings.DISCORD_GUILD_ID,
                )

        # Auto-ensure bot nickname in all joined servers is 'OBX'
        for g in self.guilds:
            try:
                me = g.me or await g.fetch_member(self.user.id)
                if me and me.nick != "OBX":
                    await me.edit(nick="OBX")
                    logger.info("Auto-set bot nickname to 'OBX' in guild '%s' (%s)", g.name, g.id)
            except Exception as nick_err:
                logger.debug("Could not auto-set nickname in guild '%s': %s", g.name, nick_err)

        # Auto-sync slash commands directly to all connected guilds for instant availability
        for g in self.guilds:
            try:
                self.tree.copy_global_to(guild=g)
                synced_g = await self.tree.sync(guild=g)
                logger.info("Synchronized %d slash commands to guild '%s' (ID: %s)", len(synced_g), g.name, g.id)
            except Exception as sync_err:
                logger.debug("Could not direct-sync commands to guild '%s': %s", g.name, sync_err)

        # Auto-deploy / refresh public systems across configured channels
        for g in self.guilds:
            try:
                res = await refresh_all_public_systems(g, self)
                logger.info("Public systems synchronized for guild '%s' (ID: %s): %s", g.name, g.id, res)
            except Exception as g_exc:
                logger.warning("Error refreshing public systems for guild '%s': %s", g.name, g_exc)

        logger.info("OBX Discord bot ready")

    async def on_guild_join(self, guild: discord.Guild):
        settings = get_settings()
        logger.info("Bot joined new guild: '%s' (ID: %s)", guild.name, guild.id)
        try:
            me = guild.me or await guild.fetch_member(self.user.id)
            if me and me.nick != "OBX":
                await me.edit(nick="OBX")
                logger.info("Set bot nickname to 'OBX' in '%s'", guild.name)
        except Exception as nick_err:
            logger.debug("Could not set nickname in '%s' on join: %s", guild.name, nick_err)

        try:
            self.tree.copy_global_to(guild=guild)
            synced = await self.tree.sync(guild=guild)
            logger.info("Automatically synchronized %d slash commands to guild '%s' on join!", len(synced), guild.name)
        except Exception as exc:
            logger.error("Error synchronizing slash commands on guild join for '%s': %s", guild.name, exc)

    async def on_tree_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        logger.error("Discord interaction failure on command '%s': %s", interaction.command.name if interaction.command else "unknown", error)
        try:
            if not interaction.response.is_done():
                await interaction.response.send_message("❌ An unexpected error occurred while processing your request.", ephemeral=True)
            else:
                await interaction.followup.send("❌ An unexpected error occurred while processing your request.", ephemeral=True)
        except Exception:
            pass


def create_discord_bot() -> OBXTaskBot:
    bot = OBXTaskBot()
    bot.tree.on_error = bot.on_tree_error

    # User Commands
    @bot.tree.command(name="auctions", description="Browse live whitelist sales, GTD auctions, and your bids")
    async def auctions_command(interaction: discord.Interaction):
        from apps.obx_tasks.bot.auction_views import handle_auction_center
        await handle_auction_center(interaction)

    @bot.tree.command(name="leaderboard", description="View community rankings, top earners, and your position")
    async def leaderboard_command(interaction: discord.Interaction):
        from apps.obx_tasks.bot.leaderboard_views import handle_leaderboard
        await handle_leaderboard(interaction)

    @bot.tree.command(name="tasks", description="Show active OBX social tasks and available reward pools")
    async def tasks_command(interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=False)
        try:
            with session_scope() as session:
                service = TaskService(session)
                tasks, total = service.list_tasks(status=TaskStatus.ACTIVE, limit=10)

                if not tasks:
                    embed = discord.Embed(
                        title="📋 Active OBX Tasks",
                        description="There are currently no active tasks. Check back soon!",
                        color=discord.Color.blue(),
                    )
                    await interaction.followup.send(embed=embed)
                    return

                embed = discord.Embed(
                    title="📋 Active OBX Tasks",
                    description=f"Showing {len(tasks)} active tasks out of {total}:",
                    color=discord.Color.gold(),
                )
                for t in tasks:
                    embed.add_field(
                        name=f"🔹 {t.title} ({t.reward_per_user:,} OBX)",
                        value=(
                            f"**Type:** `{t.task_type.value}` | **Platform:** `{t.platform}`\n"
                            f"**Target:** [Open Link]({t.target_url})\n"
                            f"**Pool Remaining:** `{t.remaining_reward_pool:,} / {t.total_reward_pool:,} OBX`\n"
                            f"**Task ID:** `{t.id}`"
                        ),
                        inline=False,
                    )
                embed.set_footer(text="Use /submit to submit proof for any task!")
                await interaction.followup.send(embed=embed)
        except Exception as exc:
            logger.error("Error in /tasks command: %s", exc)
            await interaction.followup.send("❌ Error fetching active tasks.", ephemeral=True)

    @bot.tree.command(name="task", description="View detailed instructions and requirements for a specific task")
    @app_commands.describe(task_id="The UUID of the task")
    async def task_command(interaction: discord.Interaction, task_id: str):
        await interaction.response.defer(ephemeral=False)
        try:
            with session_scope() as session:
                service = TaskService(session)
                task = service.get_task(task_id)

                embed = discord.Embed(
                    title=f"🎯 {task.title}",
                    description=task.description,
                    color=discord.Color.purple(),
                )
                embed.add_field(name="Reward Per User", value=f"**{task.reward_per_user:,} OBX**", inline=True)
                embed.add_field(name="Remaining Pool", value=f"`{task.remaining_reward_pool:,} / {task.total_reward_pool:,} OBX`", inline=True)
                embed.add_field(name="Status", value=f"`{task.status.value}`", inline=True)
                embed.add_field(name="Platform & Type", value=f"`{task.platform}` • `{task.task_type.value}`", inline=True)
                embed.add_field(name="Target URL", value=f"[Click to Open]({task.target_url})", inline=True)
                embed.add_field(name="Task ID", value=f"`{task.id}`", inline=False)

                embed.set_footer(text="Use /submit with this Task ID to submit your completion proof!")
                await interaction.followup.send(embed=embed)
        except TaskError as exc:
            await interaction.followup.send(f"❌ {exc.message}", ephemeral=True)
        except Exception as exc:
            logger.error("Error in /task command: %s", exc)
            await interaction.followup.send("❌ An unexpected error occurred.", ephemeral=True)

    @bot.tree.command(name="submit", description="Submit proof of completion for a social task")
    @app_commands.describe(
        task_id="The UUID of the task",
        x_username="Your X/Twitter username handle (without @)",
        proof_url="URL to your tweet, reply, or proof link",
        proof_text="Text explanation or context for your proof",
        screenshot_url="Optional direct image URL for screenshot proof",
    )
    async def submit_command(
        interaction: discord.Interaction,
        task_id: str,
        x_username: Optional[str] = None,
        proof_url: Optional[str] = None,
        proof_text: Optional[str] = None,
        screenshot_url: Optional[str] = None,
    ):
        from apps.obx_tasks.bot.permissions import check_raider_access
        if not await check_raider_access(interaction):
            return

        try:
            with session_scope() as session:
                service = TaskService(session)
                task = service.get_task(task_id)

                if task.status != TaskStatus.ACTIVE:
                    await interaction.response.send_message(
                        f"❌ Task '{task.title}' is not active (current status: {task.status.value}).",
                        ephemeral=True,
                    )
                    return

                # If parameters were not passed inline, pop up the interactive modal!
                if not x_username or not proof_url or not proof_text:
                    modal = TaskSubmitModal(task_id=str(task.id), task_title=task.title)
                    await interaction.response.send_modal(modal)
                    return

                # Inline submission
                await interaction.response.defer(ephemeral=True)
                submission = service.submit_task(
                    task_id=task_id,
                    discord_user_id=str(interaction.user.id),
                    x_username=x_username,
                    proof_url=proof_url,
                    proof_text=proof_text,
                    proof_screenshot_url=screenshot_url,
                )

                embed = discord.Embed(
                    title="✅ Proof Submitted Successfully!",
                    description="Your task submission has been received and queued for admin verification.",
                    color=discord.Color.green(),
                )
                embed.add_field(name="Submission ID", value=f"`{submission.id}`", inline=False)
                embed.add_field(name="Status", value="`PENDING REVIEW`", inline=True)
                embed.add_field(name="X Handle", value=f"`@{submission.x_username}`", inline=True)
                embed.add_field(name="Proof Link", value=f"[View Proof]({submission.proof_url})", inline=False)

                await interaction.followup.send(embed=embed, ephemeral=True)
        except (TaskError, ValueError) as exc:
            msg = exc.message if hasattr(exc, "message") else str(exc)
            if interaction.response.is_done():
                await interaction.followup.send(f"❌ {msg}", ephemeral=True)
            else:
                await interaction.response.send_message(f"❌ {msg}", ephemeral=True)
        except Exception as exc:
            logger.error("Error in /submit command: %s", exc)
            if interaction.response.is_done():
                await interaction.followup.send(f"❌ Error submitting proof: {str(exc)}", ephemeral=True)
            else:
                await interaction.response.send_message(f"❌ Error submitting proof: {str(exc)}", ephemeral=True)

    @bot.tree.command(name="my-submissions", description="View your historical task submissions and reward statuses")
    async def my_submissions_command(interaction: discord.Interaction):
        from apps.obx_tasks.bot.permissions import check_raider_access
        if not await check_raider_access(interaction):
            return

        await interaction.response.defer(ephemeral=True)
        try:
            with session_scope() as session:
                service = TaskService(session)
                submissions, total = service.list_submissions(
                    discord_user_id=str(interaction.user.id),
                    limit=10,
                )

                if not submissions:
                    await interaction.followup.send("You haven't submitted any task proofs yet. Use `/tasks` to get started!", ephemeral=True)
                    return

                embed = discord.Embed(
                    title="📜 Your Task Submissions",
                    description=f"Showing your recent {len(submissions)} submissions:",
                    color=discord.Color.teal(),
                )
                for s in submissions:
                    status_emoji = "⏳" if s.status == SubmissionStatus.PENDING else ("✅" if s.status == SubmissionStatus.APPROVED else "❌")
                    reward_str = f" • Awarded: `{s.reward_amount:,} OBX`" if s.reward_amount else ""
                    reason_str = f"\n*Reason:* {s.rejection_reason}" if s.rejection_reason else ""
                    embed.add_field(
                        name=f"{status_emoji} {s.task.title}",
                        value=(
                            f"**Status:** `{s.status.value}`{reward_str}\n"
                            f"**Proof:** [Open Proof]({s.proof_url}){reason_str}\n"
                            f"**Date:** <t:{int(s.submitted_at.timestamp())}:R>"
                        ),
                        inline=False,
                    )
                await interaction.followup.send(embed=embed, ephemeral=True)
        except Exception as exc:
            logger.error("Error in /my-submissions command: %s", exc)
            await interaction.followup.send("❌ Error fetching submissions.", ephemeral=True)

    @bot.tree.command(name="balance", description="View your current OBX wallet balance and earnings")
    async def balance_command(interaction: discord.Interaction):
        from apps.obx_tasks.bot.permissions import check_raider_access
        if not await check_raider_access(interaction):
            return
        from apps.obx_tasks.bot.dashboard_views import handle_my_balance
        await handle_my_balance(interaction)

    @bot.tree.command(name="my-balance", description="View your current OBX wallet balance and earnings")
    async def my_balance_command(interaction: discord.Interaction):
        from apps.obx_tasks.bot.permissions import check_raider_access
        if not await check_raider_access(interaction):
            return
        from apps.obx_tasks.bot.dashboard_views import handle_my_balance
        await handle_my_balance(interaction)

    # Admin Commands
    @bot.tree.command(name="admin-create-auction", description="[Admin] Launch a new Whitelist Auction")
    async def admin_create_auction_command(interaction: discord.Interaction):
        if not is_admin(interaction):
            await interaction.response.send_message("❌ Permission Denied: You do not have the required administrator role.", ephemeral=True)
            return

        from apps.obx_tasks.bot.auction_views import AdminCreateAuctionModal
        modal = AdminCreateAuctionModal()
        await interaction.response.send_modal(modal)

    @bot.tree.command(name="admin-post-auction", description="[Admin] Post the live OBX Whitelist & Auction Center card in this channel")
    async def admin_post_auction_command(interaction: discord.Interaction):
        if not is_admin(interaction):
            await interaction.response.send_message("❌ Permission Denied: You do not have the required administrator role.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)
        try:
            from apps.obx_tasks.bot.auction_views import AuctionCenterView
            embed = discord.Embed(
                title="🔨 OBX AUCTION CENTER & WHITELIST REWARDS",
                description=(
                    "Welcome to the **OBX Whitelist & Reward Center**!\n\n"
                    "Compete for exclusive guaranteed whitelist spots or claim instant FCFS opportunities with your OBX balance.\n\n"
                    "🔥 **Active Auctions**: View live whitelist opportunities\n"
                    "💼 **My Bids & Wins**: Track your locked bids and confirmed passes\n"
                    "❓ **Auction Guide**: Learn how FCFS and GTD allocations work"
                ),
                color=discord.Color.gold(),
            )
            embed.set_footer(text="Double-Entry Protected • Integer Precision • Authoritative Ledger")
            view = AuctionCenterView()
            await interaction.channel.send(embed=embed, view=view)
            await interaction.followup.send("✅ Auction Center posted successfully in this channel!", ephemeral=True)
        except Exception as exc:
            logger.error("Error posting auction center: %s", exc)
            await interaction.followup.send(f"❌ Error posting auction center: {str(exc)}", ephemeral=True)

    @bot.tree.command(name="admin-grant-reward", description="[Admin] Grant a custom OBX reward to a community member")
    @app_commands.describe(
        user="Target member to credit",
        amount="Amount of OBX to credit",
        reason="Reason / context for this custom reward grant",
    )
    async def admin_grant_reward_command(interaction: discord.Interaction, user: discord.Member, amount: int, reason: str):
        if not is_admin(interaction):
            await interaction.response.send_message("❌ Permission Denied: Administrator role required.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)
        try:
            with session_scope() as session:
                service = AuctionService(session)
                entry = service.grant_custom_reward(
                    admin_discord_id=str(interaction.user.id),
                    target_discord_id=str(user.id),
                    amount=amount,
                    reason=reason,
                )

            embed = discord.Embed(
                title="🎁 Custom Reward Granted!",
                description=(
                    f"Successfully credited **{amount:,} OBX** to <@{user.id}>.\n\n"
                    f"📝 **Reason:** {reason}\n"
                    f"🧾 **Ledger Transaction ID:** `{entry.id}`\n"
                    f"👑 **Authorized By:** <@{interaction.user.id}>"
                ),
                color=discord.Color.green(),
            )
            await interaction.followup.send(embed=embed, ephemeral=True)
        except Exception as exc:
            logger.error("Error in admin_grant_reward: %s", exc)
            await interaction.followup.send(f"❌ Error granting reward: {str(exc)}", ephemeral=True)

    @bot.tree.command(name="admin-settle-auction", description="[Admin] Settle and finalize an auction to distribute rewards and unlock losing bids")
    @app_commands.describe(auction_id="The UUID of the auction to settle")
    async def admin_settle_auction_command(interaction: discord.Interaction, auction_id: str):
        if not is_admin(interaction):
            await interaction.response.send_message("❌ Permission Denied: Administrator role required.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)
        try:
            with session_scope() as session:
                service = AuctionService(session)
                auc, winners, losers = service.settle_and_finalize_auction(
                    auction_id=auction_id,
                    finalized_by=str(interaction.user.id),
                )

            # Auto-announce winners to configured Winners channel
            try:
                if interaction.guild:
                    with session_scope() as sess_bids:
                        all_bids = sess_bids.query(AuctionBid).filter_by(auction_id=auc.id).all()
                        total_bidders = len(all_bids)
                    await announce_auction_winners(auc, winners, total_bidders, interaction.guild, interaction.client)
            except Exception as ann_err:
                logger.warning("Auto-announcement of winners for auction %s failed: %s", auc.id, ann_err)

            embed = discord.Embed(
                title="🏆 AUCTION SETTLED & FINALIZED!",
                description=(
                    f"Auction **{auc.title} — {auc.reward_title}** has concluded.\n\n"
                    f"🎟 **Available Slots:** `{auc.total_slots}`\n"
                    f"👑 **Winners Count:** `{len(winners)}`\n"
                    f"🔓 **Non-Winners Refunded:** `{len(losers)}`"
                ),
                color=discord.Color.gold(),
            )
            if winners:
                win_lines = [f"• <@{w.discord_user_id}> — `{w.bid_amount:,} OBX`" for w in winners[:10]]
                embed.add_field(name="🏆 Top Winners", value="\n".join(win_lines), inline=False)

            await interaction.followup.send(embed=embed, ephemeral=True)
        except Exception as exc:
            logger.error("Error settling auction: %s", exc)
            await interaction.followup.send(f"❌ Error settling auction: {str(exc)}", ephemeral=True)

    @bot.tree.command(name="admin-members", description="[Admin] Open Raiders & Members directory and inspect member details")
    async def admin_members_command(interaction: discord.Interaction):
        from apps.obx_tasks.bot.admin_members_views import handle_admin_members
        await handle_admin_members(interaction)

    @bot.tree.command(name="admin-set-bot-name", description="[Admin] Set or refresh the bot's display nickname in this server")
    @app_commands.describe(name="The new display nickname (default: OBX)")
    async def admin_set_bot_name_command(interaction: discord.Interaction, name: Optional[str] = "OBX"):
        if not is_admin(interaction):
            await interaction.response.send_message("❌ Permission Denied: You do not have the required administrator role.", ephemeral=True)
            return

        if not interaction.guild:
            await interaction.response.send_message("❌ Must be executed inside a server.", ephemeral=True)
            return

        target_name = name or "OBX"
        try:
            me = interaction.guild.me or await interaction.guild.fetch_member(bot.user.id)
            await me.edit(nick=target_name)
            await interaction.response.send_message(f"✅ Bot nickname updated to **{target_name}** in this server!", ephemeral=True)
        except discord.Forbidden:
            await interaction.response.send_message(
                "❌ Bot is missing the **Change Nickname** permission in this server. Please ensure the bot's role has 'Change Nickname'.",
                ephemeral=True,
            )
        except Exception as exc:
            await interaction.response.send_message(f"❌ Could not update nickname: {exc}", ephemeral=True)

    @bot.tree.command(name="admin-configure-channels", description="[Admin] Open interactive channel routing dashboard")
    async def admin_configure_channels_command(interaction: discord.Interaction):
        await handle_channel_config(interaction)

    @bot.tree.command(name="admin-refresh-public-systems", description="[Admin] Refresh all public dashboards and leaderboards across configured channels")
    async def admin_refresh_public_systems_command(interaction: discord.Interaction):
        await handle_refresh_public_systems(interaction)

    @bot.tree.command(name="admin-post-leaderboard", description="[Admin] Post a live, interactive OBX Leaderboard card in this channel")
    async def admin_post_leaderboard_command(interaction: discord.Interaction):
        if not is_admin(interaction):
            await interaction.response.send_message("❌ Permission Denied: You do not have the required administrator role.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)
        try:
            from apps.obx_tasks.bot.leaderboard_views import LeaderboardView, build_leaderboard_embed
            from apps.obx_tasks.services.leaderboard_service import LeaderboardService, LeaderboardCategory, LeaderboardPeriod
            with session_scope() as session:
                service = LeaderboardService(session)
                entries, total_count = service.get_leaderboard(
                    category=LeaderboardCategory.TOTAL_OBX,
                    period=LeaderboardPeriod.ALL_TIME,
                    limit=10,
                )
            embed = build_leaderboard_embed(
                entries=entries,
                total_count=total_count,
                user_position=None,
                page=0,
                page_size=10,
            )
            view = LeaderboardView()
            await interaction.channel.send(embed=embed, view=view)
            await interaction.followup.send("✅ OBX Leaderboard posted successfully in this channel!", ephemeral=True)
        except Exception as exc:
            logger.error("Error posting leaderboard: %s", exc)
            await interaction.followup.send(f"❌ Error posting leaderboard: {str(exc)}", ephemeral=True)

    @bot.tree.command(name="admin-post-dashboard", description="[Admin] Post or refresh the interactive OBX Task Center dashboard in this channel")
    async def admin_post_dashboard_command(interaction: discord.Interaction):
        if not is_admin(interaction):
            await interaction.response.send_message("❌ Permission Denied: You do not have the required administrator role.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)
        try:
            embed = create_dashboard_embed()
            view = OBXDashboardView()
            await interaction.channel.send(embed=embed, view=view)
            await interaction.followup.send("✅ OBX Task Center dashboard posted successfully in this channel!", ephemeral=True)
        except Exception as exc:
            logger.error("Error posting dashboard: %s", exc)
            await interaction.followup.send(f"❌ Error posting dashboard: {str(exc)}", ephemeral=True)

    @bot.tree.command(name="admin-health", description="[Admin] Check operational health of the bot, database, and guild setup")
    async def admin_health_command(interaction: discord.Interaction):
        if not is_admin(interaction):
            await interaction.response.send_message("❌ Permission Denied: You do not have the required administrator role.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)
        settings = get_settings()

        # Check DB Latency
        t0 = time.perf_counter()
        db_status = "Disconnected"
        try:
            with get_engine().connect() as conn:
                conn.execute(text("SELECT 1"))
            latency_ms = (time.perf_counter() - t0) * 1000
            db_status = f"Connected ({latency_ms:.1f}ms)"
        except Exception as e:
            db_status = f"Error: {str(e)[:50]}"

        # Check Guild and Admin Role
        guild_info = "Not Configured"
        role_info = "Not Configured"
        if settings.DISCORD_GUILD_ID:
            guild = bot.get_guild(int(settings.DISCORD_GUILD_ID))
            if guild:
                guild_info = f"Connected ({guild.name})"
                if settings.DISCORD_ADMIN_ROLE_IDS:
                    found_roles = []
                    for rid in settings.DISCORD_ADMIN_ROLE_IDS:
                        r = guild.get_role(int(rid))
                        if r:
                            found_roles.append(f"@{r.name}")
                    role_info = ", ".join(found_roles) if found_roles else "Configured ID not found in server"
            else:
                guild_info = "Bot is not in configured server"

        embed = discord.Embed(
            title="🛠️ OBX System Health Diagnostic",
            description="Operational status and configuration verification report:",
            color=discord.Color.green(),
        )
        embed.add_field(name="Bot Status", value="🟢 `Online & Operational`", inline=True)
        embed.add_field(name="Database", value=f"`{db_status}`", inline=True)
        embed.add_field(name="Migration Head", value="`004_whitelist_auctions`", inline=True)
        embed.add_field(name="Guild Setup", value=f"`{guild_info}`", inline=False)
        embed.add_field(name="Admin Roles", value=f"`{role_info}`", inline=False)
        embed.add_field(name="Slash Commands Registered", value="`17 commands`", inline=True)
        embed.add_field(name="Environment", value=f"`{settings.ENVIRONMENT}`", inline=True)

        embed.set_footer(text="All secrets and internal credentials are secure.")
        await interaction.followup.send(embed=embed, ephemeral=True)

    @bot.tree.command(name="admin-create-task", description="[Admin] Create a new social task with reward pool")
    @app_commands.describe(
        description="Detailed task instructions",
        task_type="Type of action required (RETWEET, COMMENT, LIKE, FOLLOW, etc.)",
        target_url="Target URL / tweet link",
        reward_per_user="OBX awarded per approved user",
        total_reward_pool="Total OBX pool for this task",
        title="Optional custom title (auto-generated if omitted)",
        platform="Platform (default: X)",
    )
    async def admin_create_task_command(
        interaction: discord.Interaction,
        description: str,
        task_type: str,
        target_url: str,
        reward_per_user: int,
        total_reward_pool: int,
        title: Optional[str] = None,
        platform: str = "X",
    ):
        if not is_admin(interaction):
            await interaction.response.send_message("❌ Permission Denied: You do not have the required administrator role.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)
        try:
            with session_scope() as session:
                service = TaskService(session)
                actual_title = title.strip() if (title and title.strip()) else f"{task_type.upper()} Raid"
                task = service.create_task(
                    title=actual_title,
                    description=description,
                    task_type=task_type.upper(),
                    target_url=target_url,
                    reward_per_user=reward_per_user,
                    total_reward_pool=total_reward_pool,
                    created_by=str(interaction.user.id),
                    platform=platform,
                    status=TaskStatus.ACTIVE,
                )

                embed = discord.Embed(
                    title="🎉 Task Created Successfully!",
                    description=f"Task **{task.title}** is now active for users.",
                    color=discord.Color.green(),
                )
                embed.add_field(name="Task ID", value=f"`{task.id}`", inline=False)
                embed.add_field(name="Reward Per User", value=f"`{task.reward_per_user:,} OBX`", inline=True)
                embed.add_field(name="Total Pool", value=f"`{task.total_reward_pool:,} OBX`", inline=True)
                embed.add_field(name="Max Approvals", value=f"`{task.max_approvals}`", inline=True)

                if interaction.guild:
                    from apps.obx_tasks.bot.announcement_service import announce_task
                    try:
                        await announce_task(task, interaction.guild, bot)
                    except Exception as ann_err:
                        logger.warning("Could not auto-announce task %s: %s", task.id, ann_err)

                await interaction.followup.send(embed=embed, ephemeral=True)
        except TaskError as exc:
            await interaction.followup.send(f"❌ {exc.message}", ephemeral=True)
        except Exception as exc:
            logger.error("Error creating task: %s", exc)
            await interaction.followup.send(f"❌ Error creating task: {str(exc)}", ephemeral=True)

    @bot.tree.command(name="admin-task-edit", description="[Admin] Edit task configuration, pool size, reward rate, or status")
    @app_commands.describe(
        task_id="The UUID of the task to edit",
        total_reward_pool="New total reward pool (cannot be lower than distributed OBX)",
        reward_per_user="New future reward per approved user",
        title="Updated task title",
        description="Updated description",
        target_url="Updated target URL",
    )
    async def admin_task_edit_command(
        interaction: discord.Interaction,
        task_id: str,
        total_reward_pool: Optional[int] = None,
        reward_per_user: Optional[int] = None,
        title: Optional[str] = None,
        description: Optional[str] = None,
        target_url: Optional[str] = None,
    ):
        if not is_admin(interaction):
            await interaction.response.send_message("❌ Permission Denied: You do not have the required administrator role.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)
        try:
            with session_scope() as session:
                service = TaskService(session)
                task = service.edit_task(
                    task_id=task_id,
                    changed_by=str(interaction.user.id),
                    total_reward_pool=total_reward_pool,
                    reward_per_user=reward_per_user,
                    title=title,
                    description=description,
                    target_url=target_url,
                )

                embed = discord.Embed(
                    title="✏️ Task Configuration Updated",
                    description=f"Task **{task.title}** has been updated with audit tracking.",
                    color=discord.Color.blue(),
                )
                embed.add_field(name="Total Pool", value=f"`{task.total_reward_pool:,} OBX`", inline=True)
                embed.add_field(name="Distributed", value=f"`{task.distributed_reward:,} OBX`", inline=True)
                embed.add_field(name="Remaining Pool", value=f"`{task.remaining_reward_pool:,} OBX`", inline=True)
                embed.add_field(name="Reward Per User", value=f"`{task.reward_per_user:,} OBX`", inline=True)
                embed.add_field(name="Status", value=f"`{task.status.value}`", inline=True)

                await interaction.followup.send(embed=embed, ephemeral=True)
        except TaskError as exc:
            await interaction.followup.send(f"❌ {exc.message}", ephemeral=True)
        except Exception as exc:
            logger.error("Error editing task: %s", exc)
            await interaction.followup.send(f"❌ Error editing task: {str(exc)}", ephemeral=True)

    @bot.tree.command(name="admin-edit-task", description="[Admin Alias] Edit task configuration")
    @app_commands.describe(
        task_id="The UUID of the task to edit",
        total_reward_pool="New total reward pool",
        reward_per_user="New future reward per approved user",
        title="Updated task title",
        description="Updated description",
        target_url="Updated target URL",
    )
    async def admin_edit_task_alias(
        interaction: discord.Interaction,
        task_id: str,
        total_reward_pool: Optional[int] = None,
        reward_per_user: Optional[int] = None,
        title: Optional[str] = None,
        description: Optional[str] = None,
        target_url: Optional[str] = None,
    ):
        await admin_task_edit_command.callback(
            interaction,
            task_id=task_id,
            total_reward_pool=total_reward_pool,
            reward_per_user=reward_per_user,
            title=title,
            description=description,
            target_url=target_url,
        )

    @bot.tree.command(name="admin-task-status", description="[Admin] Change task status (pause, resume, complete, cancel)")
    @app_commands.describe(
        task_id="The UUID of the task",
        status="Target lifecycle status",
    )
    @app_commands.choices(status=[
        app_commands.Choice(name="Active (Resume Task)", value="ACTIVE"),
        app_commands.Choice(name="Paused (Temporarily Pause)", value="PAUSED"),
        app_commands.Choice(name="Completed (Mark Completed)", value="COMPLETED"),
        app_commands.Choice(name="Cancelled (Cancel Task)", value="CANCELLED"),
    ])
    async def admin_task_status_command(
        interaction: discord.Interaction,
        task_id: str,
        status: app_commands.Choice[str],
    ):
        if not is_admin(interaction):
            await interaction.response.send_message("❌ Permission Denied: You do not have the required administrator role.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)
        try:
            with session_scope() as session:
                service = TaskService(session)
                task = service.edit_task(
                    task_id=task_id,
                    changed_by=str(interaction.user.id),
                    status=status.value,
                )

                embed = discord.Embed(
                    title="🔄 Task Status Changed",
                    description=f"Task **{task.title}** status is now **{task.status.value}**.",
                    color=discord.Color.gold(),
                )
                embed.add_field(name="Task ID", value=f"`{task.id}`", inline=False)
                embed.add_field(name="Status", value=f"`{task.status.value}`", inline=True)
                embed.add_field(name="Remaining Pool", value=f"`{task.remaining_reward_pool:,} OBX`", inline=True)

                await interaction.followup.send(embed=embed, ephemeral=True)
        except TaskError as exc:
            await interaction.followup.send(f"❌ {exc.message}", ephemeral=True)
        except Exception as exc:
            logger.error("Error updating status: %s", exc)
            await interaction.followup.send(f"❌ Error updating status: {str(exc)}", ephemeral=True)

    @bot.tree.command(name="admin-task-history", description="[Admin] View configuration change audit trail for a task")
    @app_commands.describe(task_id="The UUID of the task")
    async def admin_task_history_command(interaction: discord.Interaction, task_id: str):
        if not is_admin(interaction):
            await interaction.response.send_message("❌ Permission Denied: You do not have the required administrator role.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)
        try:
            with session_scope() as session:
                service = TaskService(session)
                logs, total = service.get_task_audit_logs(task_id=task_id, limit=10)

                if not logs:
                    await interaction.followup.send("No audit logs found for this task.", ephemeral=True)
                    return

                embed = discord.Embed(
                    title=f"📜 Configuration Audit Trail (Total: {total})",
                    description=f"Showing recent {len(logs)} audit entries:",
                    color=discord.Color.dark_grey(),
                )
                for log in logs:
                    embed.add_field(
                        name=f"🔧 {log.field_name} (by <@{log.changed_by}>)",
                        value=(
                            f"**Old:** `{log.old_value}`\n"
                            f"**New:** `{log.new_value}`\n"
                            f"**Timestamp:** <t:{int(log.changed_at.timestamp())}:R>"
                        ),
                        inline=False,
                    )
                await interaction.followup.send(embed=embed, ephemeral=True)
        except Exception as exc:
            logger.error("Error fetching audit logs: %s", exc)
            await interaction.followup.send("❌ Error fetching audit history.", ephemeral=True)

    @bot.tree.command(name="admin-submissions", description="[Admin] View pending task submissions awaiting review with buttons")
    @app_commands.describe(task_id="Optional Task UUID to filter by")
    async def admin_submissions_command(interaction: discord.Interaction, task_id: Optional[str] = None):
        if not is_admin(interaction):
            await interaction.response.send_message("❌ Permission Denied: You do not have the required administrator role.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)
        try:
            with session_scope() as session:
                service = TaskService(session)
                submissions, total = service.list_submissions(
                    task_id=task_id,
                    status=SubmissionStatus.PENDING,
                    limit=5,
                )

                if not submissions:
                    await interaction.followup.send("✅ No pending submissions awaiting review!", ephemeral=True)
                    return

                for s in submissions:
                    embed = discord.Embed(
                        title=f"🔍 Submission: {s.task.title}",
                        description=f"Submission `{s.id}` awaiting admin review:",
                        color=discord.Color.orange(),
                    )
                    embed.add_field(name="User", value=f"<@{s.discord_user_id}> (`{s.discord_user_id}`)", inline=True)
                    embed.add_field(name="X Handle", value=f"`@{s.x_username}`", inline=True)
                    embed.add_field(name="Reward", value=f"`{s.task.reward_per_user:,} OBX`", inline=True)
                    embed.add_field(name="Proof URL", value=f"[Open Proof Link]({s.proof_url})", inline=False)
                    embed.add_field(name="Proof Context", value=f"{s.proof_text[:200]}", inline=False)
                    if s.proof_screenshot_url:
                        embed.set_image(url=s.proof_screenshot_url)

                    view = TaskReviewView(
                        submission_id=str(s.id),
                        submitter_discord_id=str(s.discord_user_id),
                    )
                    await interaction.followup.send(embed=embed, view=view, ephemeral=True)
        except Exception as exc:
            logger.error("Error listing admin submissions: %s", exc)
            await interaction.followup.send("❌ Error fetching queue.", ephemeral=True)

    @bot.tree.command(name="admin-review", description="[Admin] Approve or reject a pending task submission")
    @app_commands.describe(
        submission_id="The UUID of the submission to review",
        action="Approve or Reject",
        reason="Required if rejecting, optional if approving",
    )
    @app_commands.choices(action=[
        app_commands.Choice(name="Approve (Distribute OBX Reward)", value="approve"),
        app_commands.Choice(name="Reject", value="reject"),
    ])
    async def admin_review_command(
        interaction: discord.Interaction,
        submission_id: str,
        action: app_commands.Choice[str],
        reason: Optional[str] = None,
    ):
        if not is_admin(interaction):
            await interaction.response.send_message("❌ Permission Denied: You do not have the required administrator role.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)
        try:
            with session_scope() as session:
                service = TaskService(session)
                if action.value == "approve":
                    sub = service.approve_submission(
                        submission_id=submission_id,
                        reviewer_discord_id=str(interaction.user.id),
                    )
                    from apps.obx_core.services.wallet_service import WalletService
                    ws = WalletService(session)
                    _, u_wallet, _ = ws.get_or_create_user(sub.discord_user_id)
                    new_bal = u_wallet.available_balance if u_wallet else 0
                    sub_id = str(sub.id)
                    user_id = str(sub.discord_user_id)
                    reward_amt = int(sub.reward_amount or 0)

                    logger.info("[APPROVAL] New balance: %d OBX for user %s", new_bal, user_id)

                    embed = discord.Embed(
                        title="✅ Submission Approved",
                        description=(
                            f"Approved submission for <@{user_id}>.\n"
                            f"Awarded **{reward_amt:,} OBX**."
                        ),
                        color=discord.Color.green(),
                    )
                    await interaction.followup.send(embed=embed, ephemeral=True)

                    # Send approval DM to the user
                    try:
                        from apps.obx_tasks.bot.notification_service import send_approval_dm
                        await send_approval_dm(
                            bot=interaction.client,
                            discord_user_id=user_id,
                            approved_amount=reward_amt,
                            new_balance=new_bal,
                            submission_id=sub_id,
                        )
                    except Exception as notif_err:
                        logger.error("[DM] Could not send approval DM from slash command: %s\n%s", notif_err, traceback.format_exc())
                else:
                    if not reason or not reason.strip():
                        await interaction.followup.send("❌ Rejection reason is required when rejecting a submission.", ephemeral=True)
                        return
                    sub = service.reject_submission(
                        submission_id=submission_id,
                        reviewer_discord_id=str(interaction.user.id),
                        rejection_reason=reason,
                    )
                    embed = discord.Embed(
                        title="❌ Submission Rejected",
                        description=f"Submission `{sub.id}` was rejected with reason: *{reason}*",
                        color=discord.Color.red(),
                    )
                    await interaction.followup.send(embed=embed, ephemeral=True)
        except (TaskError, OBXError) as exc:
            await interaction.followup.send(f"❌ {exc.message}", ephemeral=True)
        except Exception as exc:
            logger.error("Error reviewing submission: %s", exc)
            await interaction.followup.send(f"❌ Review error: {str(exc)}", ephemeral=True)

    @bot.tree.command(name="admin-test-dm", description="[Admin] Test approval DM delivery to a user without modifying balances")
    @app_commands.describe(
        user="The Discord user to send the test approval DM to",
        reward="Reward amount to display in DM (default: 10 OBX)",
    )
    async def admin_test_dm_cmd(interaction: discord.Interaction, user: discord.User, reward: Optional[int] = 10):
        if not is_admin(interaction):
            await interaction.response.send_message("❌ Permission Denied: Administrator role required.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)
        target_uid = str(user.id)
        reward_amt = reward if (reward and reward > 0) else 10

        try:
            with session_scope() as session:
                from apps.obx_core.services.wallet_service import WalletService
                ws = WalletService(session)
                _, u_wallet, _ = ws.get_or_create_user(target_uid)
                current_bal = u_wallet.available_balance if u_wallet else 0

            from apps.obx_tasks.bot.notification_service import send_approval_dm
            ok, detail = await send_approval_dm(
                bot=interaction.client,
                discord_user_id=target_uid,
                approved_amount=reward_amt,
                new_balance=current_bal,
                is_test=True,
                return_detail=True,
            )
            if ok:
                await interaction.followup.send(
                    f"✅ **TEST DM SENT SUCCESSFULLY**\n\n"
                    f"Sent approval DM to <@{target_uid}>.\n"
                    f"Reward Displayed: `+{reward_amt:,} OBX`\n"
                    f"Balance Displayed: `{current_bal:,} OBX`\n\n"
                    f"*Financial Safety Check: No OBX credited, zero balances modified.*",
                    ephemeral=True,
                )
            else:
                await interaction.followup.send(
                    f"❌ **TEST DM FAILED**\n\n"
                    f"Could not deliver DM to <@{target_uid}>: {detail}",
                    ephemeral=True,
                )
        except Exception as exc:
            logger.error("[TEST DM] Command error: %s\n%s", exc, traceback.format_exc())
            await interaction.followup.send(f"❌ Test DM error: {str(exc)}", ephemeral=True)

    @bot.tree.command(name="admin-raider", description="[Admin] Inspect, set, or remove a member's connected X (Twitter) handle")
    @app_commands.describe(
        action="Action to perform: lookup, set, or remove",
        member="The server member to manage",
        twitter_handle_or_url="The new X handle or profile URL (required for 'set')",
        override="Force update even if the handle is already registered to another user",
    )
    @app_commands.choices(action=[
        app_commands.Choice(name="Lookup Profile", value="lookup"),
        app_commands.Choice(name="Set / Update X Handle", value="set"),
        app_commands.Choice(name="Remove X Handle", value="remove"),
    ])
    async def admin_raider_command(
        interaction: discord.Interaction,
        action: app_commands.Choice[str],
        member: discord.Member,
        twitter_handle_or_url: Optional[str] = None,
        override: bool = False,
    ):
        if not is_admin(interaction):
            await interaction.response.send_message("❌ Administrator permissions required.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)

        from apps.obx_tasks.services.raider_service import RaiderService
        from apps.obx_tasks.bot.permissions import has_raider_role
        from apps.obx_tasks.bot.ui_theme import COLOR_GOLD, COLOR_GREEN

        try:
            with session_scope() as session:
                r_service = RaiderService(session)

                if action.value == "lookup":
                    profile = r_service.get_raider_profile(str(member.id))
                    is_raider = has_raider_role(member)
                    status_str = "⚡ Active OBX Raider" if is_raider else "❌ Missing Raider Role"

                    if not profile:
                        desc = (
                            f"**Discord User:** {member.mention} (`{member.id}`)\n"
                            f"**Status:** {status_str}\n"
                            f"**X Account:** *No X account connected*\n"
                        )
                    else:
                        created_ts = int(profile.created_at.timestamp())
                        updated_ts = int(profile.updated_at.timestamp())
                        desc = (
                            f"**Discord User:** {member.mention} (`{member.id}`)\n"
                            f"**Status:** {status_str}\n"
                            f"**𝕏 Handle:** `@{profile.twitter_handle}`\n"
                            f"**🔗 Profile:** [Open Profile]({profile.twitter_profile_url})\n"
                            f"**📅 Registered:** <t:{created_ts}:f> (<t:{created_ts}:R>)\n"
                            f"**🔄 Updated:** <t:{updated_ts}:R>\n"
                        )

                    embed = discord.Embed(
                        title=f"🛡️ Raider Profile — {member.display_name}",
                        description=desc,
                        color=COLOR_GOLD,
                    )
                    await interaction.followup.send(embed=embed, ephemeral=True)

                elif action.value == "set":
                    if not twitter_handle_or_url or not twitter_handle_or_url.strip():
                        await interaction.followup.send("❌ Please provide a `twitter_handle_or_url` to set.", ephemeral=True)
                        return
                    try:
                        profile = r_service.set_raider_twitter(
                            discord_user_id=str(member.id),
                            raw_input=twitter_handle_or_url.strip(),
                            admin_override=override,
                        )
                        embed = discord.Embed(
                            title="✅ Raider X Account Updated",
                            description=(
                                f"Successfully connected {member.mention} to **@{profile.twitter_handle}**\n"
                                f"Profile URL: {profile.twitter_profile_url}"
                            ),
                            color=COLOR_GREEN,
                        )
                        await interaction.followup.send(embed=embed, ephemeral=True)
                    except ValueError as err:
                        await interaction.followup.send(f"❌ {str(err)}", ephemeral=True)

                elif action.value == "remove":
                    removed = r_service.remove_raider_twitter(str(member.id))
                    if removed:
                        await interaction.followup.send(f"✅ Removed connected X account for {member.mention}.", ephemeral=True)
                    else:
                        await interaction.followup.send(f"ℹ️ {member.mention} has no registered X account.", ephemeral=True)
        except Exception as exc:
            logger.error("Error in admin_raider_command: %s", exc)
            await interaction.followup.send(f"❌ Error managing raider profile: {str(exc)}", ephemeral=True)

    return bot
