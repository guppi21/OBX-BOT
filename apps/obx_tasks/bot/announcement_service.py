import discord
import re
import html
from datetime import datetime, timezone
from typing import Optional, Tuple, List, Dict, Any

from packages.database.session import session_scope
from packages.database.models.auction import Auction, AuctionBid, AuctionClaim
from packages.database.models.task import Task
from packages.database.models.submission import TaskSubmission
from packages.shared.enums import AuctionType, AuctionStatus, TaskStatus, SubmissionStatus
from apps.obx_tasks.services.channel_service import ChannelService
from apps.obx_tasks.services.auction_service import AuctionService
from apps.obx_tasks.services.leaderboard_service import (
    LeaderboardService, LeaderboardCategory, LeaderboardPeriod
)
from apps.obx_tasks.bot.dashboard_views import OBXDashboardView, create_dashboard_embed
from apps.obx_tasks.bot.leaderboard_views import LeaderboardView, build_leaderboard_embed
from apps.obx_tasks.bot.ui_theme import (
    COLOR_GOLD, COLOR_PURPLE, COLOR_GREEN, COLOR_BLUE, COLOR_RED, COLOR_DARK,
    COLOR_CRYSTAL_BLUE, COLOR_CRIMSON,
    FOOTER_MISSION, FOOTER_AUCTION, FOOTER_RESULTS, FOOTER_REWARDS,
    BADGE_APPROVED, BADGE_PENDING, BADGE_REJECTED,
)
from packages.shared.config import get_settings
from packages.shared.logging import get_logger

logger = get_logger("obx.tasks.bot.announcements")


def resolve_raider_role(guild: Optional[discord.Guild]) -> Tuple[Optional[str], Optional[discord.Role]]:
    """Resolve the universal OBX Raider role ID and Role object for announcements.
    Checks settings.RAID_ROLE_ID first, then falls back to searching guild roles.
    """
    raid_role = None
    raid_role_id = get_settings().RAID_ROLE_ID
    if guild and raid_role_id:
        try:
            raid_role = guild.get_role(int(raid_role_id))
        except (ValueError, TypeError):
            pass
    if guild and not raid_role:
        for r in guild.roles:
            if "raider" in r.name.lower():
                raid_role = r
                raid_role_id = str(r.id)
                break
    return raid_role_id, raid_role

# Task type → (headline, emoji, action_verb)
_TASK_TYPE_LABELS = {
    "LIKE":         ("❤️ LIKE MISSION",        "❤️",  "Like the target post"),
    "RETWEET":      ("🔁 REPOST MISSION",      "🔁",  "Repost on X"),
    "COMMENT":      ("💬 COMMENT MISSION",     "💬",  "Comment on the post"),
    "FOLLOW":       ("👥 FOLLOW MISSION",      "👥",  "Follow the account on X"),
    "JOIN_DISCORD": ("📣 DISCORD MISSION",     "📣",  "Join the Discord server"),
    "CUSTOM_TASK":  ("📝 OBX MISSION",         "📝",  "Complete the mission"),
    "MULTI_ACTION": ("🔥 COMMUNITY RAID",      "🔥",  "Complete the required actions"),
}
_DEFAULT_TASK_LABEL = ("⚡ COMMUNITY MISSION", "⚡", "Complete the mission")

_ACTION_CONFIG = {
    "LIKE": ("❤️", "Like the target post"),
    "RETWEET": ("🔁", "Repost the target post"),
    "COMMENT": ("💬", "Comment on the target post"),
    "FOLLOW": ("👥", "Follow the target account"),
    "JOIN_DISCORD": ("📣", "Join the community Discord"),
    "CUSTOM": ("📝", "Complete custom objectives"),
}


class TaskAnnouncementCardView(discord.ui.View):
    """Minimal action buttons on live task announcement cards."""
    def __init__(
        self,
        task_id: str,
        is_active: bool = True,
        target_url: Optional[str] = None,
        is_cancelled: bool = False,
        is_expired: bool = False,
    ):
        super().__init__(timeout=None)
        self.task_id = task_id
        self.is_active = is_active

        if target_url:
            self.add_item(discord.ui.Button(
                label="OPEN TASK",
                url=target_url,
                style=discord.ButtonStyle.link,
                row=0,
            ))

        if is_active:
            self.add_item(discord.ui.Button(
                label="COMPLETE MISSION",
                style=discord.ButtonStyle.success,
                custom_id=f"obx:task_card:verify:{task_id}",
                row=0,
            ))
        elif is_cancelled:
            self.add_item(discord.ui.Button(
                label="MISSION CANCELLED",
                style=discord.ButtonStyle.secondary,
                custom_id=f"obx:task_card:closed:{task_id}",
                disabled=True,
                row=0,
            ))
        elif is_expired:
            self.add_item(discord.ui.Button(
                label="MISSION EXPIRED",
                style=discord.ButtonStyle.secondary,
                custom_id=f"obx:task_card:closed:{task_id}",
                disabled=True,
                row=0,
            ))
        else:
            self.add_item(discord.ui.Button(
                label="MISSION CLOSED",
                style=discord.ButtonStyle.secondary,
                custom_id=f"obx:task_card:closed:{task_id}",
                disabled=True,
                row=0,
            ))


def _format_mini_instruction(task) -> str:
    """Generate one concise instruction sentence with 📝 prefix."""
    req_actions_raw = getattr(task, "required_actions", None) or ""
    req_actions = [a.strip().upper() for a in req_actions_raw.split(",") if a.strip()]
    task_type = (getattr(task, "task_type", None) or "").upper()

    if req_actions:
        actions_map = {
            "LIKE": "like the post",
            "RETWEET": "retweet it",
            "REPOST": "retweet it",
            "COMMENT": "comment on it",
            "FOLLOW": "follow the account",
            "JOIN_DISCORD": "join the Discord server",
            "CUSTOM": "complete the task",
        }
        action_verbs = []
        for a in req_actions:
            verb = actions_map.get(a, a.lower())
            if verb not in action_verbs:
                action_verbs.append(verb)

        if len(action_verbs) == 1:
            return f"📝 {action_verbs[0].capitalize()} to complete this raid."
        elif len(action_verbs) == 2:
            return f"📝 {action_verbs[0].capitalize()} and {action_verbs[1]} to complete this raid."
        elif len(action_verbs) > 2:
            first_part = ", ".join(action_verbs[:-1])
            return f"📝 {first_part.capitalize()}, and {action_verbs[-1]} to complete this raid."

    if task_type == "LIKE":
        return "📝 Like the target post on X and complete this mission."
    elif task_type in ("RETWEET", "REPOST"):
        return "📝 Retweet the target post on X and complete this mission."
    elif task_type == "COMMENT":
        return "📝 Comment on the target post on X and complete this mission."
    elif task_type == "FOLLOW":
        return "📝 Follow the target account on X to complete this mission."
    elif task_type == "JOIN_DISCORD":
        return "📝 Join the Discord server and submit proof to complete this mission."

    desc = (getattr(task, "description", None) or "").strip()
    if desc:
        clean = desc.lstrip("📝⚡🔥•> \t\r\n").strip()
        if clean:
            if not clean.endswith("."):
                clean += "."
            return f"📝 {clean}"

    return "📝 Complete the required actions and submit your proof."


def build_task_announcement_embed(task) -> discord.Embed:
    """Build the extremely minimal OBX task card with strictly:
    1. Tweet/link preview
    2. One short task description
    3. Reward / time / spots row
    """
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc)
    ends_at = task.ends_at
    if ends_at is not None and ends_at.tzinfo is None:
        ends_at = ends_at.replace(tzinfo=timezone.utc)

    is_cancelled = (task.status == TaskStatus.CANCELLED)
    is_expired = (
        task.status == TaskStatus.EXPIRED
        or (ends_at is not None and now > ends_at)
    )
    remaining_pool = task.total_reward_pool - task.distributed_reward
    is_exhausted = remaining_pool <= 0
    is_active = (
        task.status == TaskStatus.ACTIVE
        and not is_expired
        and not is_exhausted
        and not is_cancelled
    )

    color = COLOR_GOLD if is_active else COLOR_CRIMSON

    # Platform detection
    url_str = getattr(task, "target_url", "") or ""
    url_lower = url_str.lower()
    platform_val = getattr(task, "preview_platform", None) or getattr(task, "platform", "") or ""

    if "x.com" in url_lower or "twitter.com" in url_lower or str(platform_val).upper() in ("X", "TWITTER"):
        platform_name = "X"
        platform_icon = "𝕏"
    elif "youtube" in url_lower or str(platform_val).upper() == "YOUTUBE":
        platform_name = "YouTube"
        platform_icon = "▶️"
    elif "discord" in url_lower or str(platform_val).upper() == "DISCORD":
        platform_name = "Discord"
        platform_icon = "📱"
    else:
        platform_name = platform_val or "Web"
        platform_icon = "🌐"

    # Handle from target URL
    handle = None
    if platform_name == "X" and url_str:
        m = re.search(r"(?:x\.com|twitter\.com)/([A-Za-z0-9_]{1,20})", url_str, re.IGNORECASE)
        if m and m.group(1).lower() not in ("home", "explore", "messages", "notifications", "i"):
            handle = f"@{m.group(1)}"

    author_text_raw = (
        getattr(task, "preview_author_override", None)
        or getattr(task, "preview_author", None)
    )
    author_text = str(author_text_raw).strip() if isinstance(author_text_raw, str) and str(author_text_raw).strip() else None

    # STRICT DATA SEPARATION:
    manual_override_text = getattr(task, "preview_text_override", None)
    extracted_post_text = getattr(task, "preview_description", None)

    task_instructions = str(getattr(task, "description", "") or "").strip()
    if extracted_post_text and str(extracted_post_text).strip() == task_instructions:
        extracted_post_text = None

    post_text = (
        str(manual_override_text).strip() if manual_override_text and str(manual_override_text).strip()
        else (str(extracted_post_text).strip() if extracted_post_text and str(extracted_post_text).strip() else None)
    )

    image_url_raw = (
        getattr(task, "preview_image_override", None)
        or getattr(task, "preview_image_url", None)
    )
    image_url = str(image_url_raw) if isinstance(image_url_raw, str) and str(image_url_raw).startswith("http") else None

    # 1. LINK / TWEET PREVIEW BLOCK
    preview_lines = []
    if platform_name == "X":
        preview_lines.append("𝕏")
        preview_lines.append("")
        if author_text and handle:
            if "\n" in author_text:
                parts = [p.strip() for p in author_text.split("\n") if p.strip()]
                preview_lines.append(f"**{parts[0]}**  {parts[1] if len(parts) > 1 else handle}")
            elif handle.lower() in author_text.lower():
                preview_lines.append(f"**{author_text}**")
            else:
                preview_lines.append(f"**{author_text}**  {handle}")
        elif author_text:
            if author_text.startswith("@"):
                preview_lines.append(f"**{author_text[1:]}**  {author_text}")
            else:
                preview_lines.append(f"**{author_text}**")
        elif handle:
            preview_lines.append(f"**{handle[1:]}**  {handle}")
        else:
            preview_lines.append("**Official X Post**")

        preview_lines.append("")
        if post_text:
            clean_snippet = post_text.strip().strip('"').strip("'").strip("“").strip("”")
            if len(clean_snippet) > 280:
                clean_snippet = clean_snippet[:277] + "..."

            quote_lines = []
            for line in clean_snippet.split("\n"):
                s = line.strip()
                if s:
                    quote_lines.append(f"> *{s}*")
                else:
                    quote_lines.append(">")

            if quote_lines:
                first_i = next((i for i, l in enumerate(quote_lines) if l.startswith("> *")), None)
                last_i = next((i for i in reversed(range(len(quote_lines))) if quote_lines[i].startswith("> *")), None)
                if first_i is not None:
                    quote_lines[first_i] = quote_lines[first_i].replace("> *", "> *“", 1)
                if last_i is not None:
                    if quote_lines[last_i].endswith("*"):
                        quote_lines[last_i] = quote_lines[last_i][:-1] + "”*"
                    else:
                        quote_lines[last_i] += "”"
            preview_lines.extend(quote_lines)
        else:
            fallback_text = getattr(task, "title", None) or "View the target post on X."
            preview_lines.append(f"> *“{fallback_text}”*")
    else:
        preview_lines.append(platform_icon)
        preview_lines.append("")
        header_name = author_text or getattr(task, "title", None) or platform_name
        preview_lines.append(f"**{header_name}**")
        preview_lines.append("")
        if post_text:
            clean_snippet = post_text.strip().strip('"').strip("'").strip("“").strip("”")
            if len(clean_snippet) > 280:
                clean_snippet = clean_snippet[:277] + "..."
            quote_lines = [f"> *{line.strip()}*" if line.strip() else ">" for line in clean_snippet.split("\n")]
            preview_lines.extend(quote_lines)
        else:
            fallback_text = getattr(task, "title", None) or "Open the mission to view content."
            preview_lines.append(f"> *“{fallback_text}”*")

    # 2. MINI TASK DESCRIPTION
    instruction_sentence = _format_mini_instruction(task)

    # 3. METRICS ROW
    max_slots = task.max_approvals
    claimed = task.approved_count
    available_slots = max(0, max_slots - claimed)
    spots_str = f"{available_slots} SPOT" if available_slots == 1 else f"{available_slots} SPOTS"

    if is_expired and ends_at:
        time_text = f"ENDED <t:{int(ends_at.timestamp())}:R>"
    elif ends_at:
        time_text = f"<t:{int(ends_at.timestamp())}:R>"
    else:
        time_text = "NO DEADLINE"

    metrics_row = (
        f"💎 **{task.reward_per_user:,} OBX**   •   "
        f"⏳ **{time_text}**   •   "
        f"👥 **{spots_str}**"
    )

    body_blocks = [
        "\n".join(preview_lines),
        instruction_sentence,
        metrics_row,
    ]

    embed = discord.Embed(
        description="\n\n".join(body_blocks),
        color=color,
    )

    if image_url and str(image_url).startswith("http"):
        clean_img = str(image_url)
        if "pbs.twimg.com" in clean_img and "name=orig" in clean_img:
            clean_img = clean_img.replace("name=orig", "name=large")
        embed.set_image(url=clean_img)

    return embed



def check_channel_permissions(channel: discord.TextChannel, me: discord.Member) -> Tuple[bool, List[str]]:
    """Check required bot permissions in a target Discord channel."""
    perms = channel.permissions_for(me)
    missing = []
    if not perms.view_channel:
        missing.append("View Channel")
    if not perms.send_messages:
        missing.append("Send Messages")
    if not perms.embed_links:
        missing.append("Embed Links")
    return (len(missing) == 0, missing)


def resolve_channel_for_feature(
    guild: discord.Guild,
    explicit_id: Optional[str] = None,
    name_keywords: Optional[List[str]] = None,
) -> Optional[discord.TextChannel]:
    """Resolve target TextChannel by explicit ID, or auto-discover by channel name keyword."""
    if explicit_id:
        try:
            ch = guild.get_channel(int(explicit_id))
            if ch and isinstance(ch, discord.TextChannel):
                return ch
        except (ValueError, TypeError):
            pass

    if name_keywords and hasattr(guild, "text_channels"):
        for ch in guild.text_channels:
            name_lower = ch.name.lower()
            if any(kw.lower() in name_lower for kw in name_keywords):
                return ch
    return None


async def deploy_or_update_task_center(guild: discord.Guild, bot: discord.Client) -> Tuple[bool, str]:
    """Deploy or update persistent Task Center dashboard in the configured channel."""
    with session_scope() as session:
        service = ChannelService(session)
        config = service.get_or_create_guild_config(str(guild.id))
        channel = resolve_channel_for_feature(
            guild,
            explicit_id=config.tasks_channel_id or get_settings().DISCORD_TASK_CHANNEL_ID,
            name_keywords=["tasks", "missions", "task", "mission"],
        )

        if not channel:
            return False, "Tasks channel is not configured. Use Admin Hub -> Configure Channels."

        if not config.tasks_channel_id:
            config.tasks_channel_id = str(channel.id)
            session.commit()

        me = guild.me or guild.get_member(bot.user.id)
        if me:
            valid, missing = check_channel_permissions(channel, me)
            if not valid:
                return False, f"Missing bot permissions in {channel.mention}: {', '.join(missing)}"

        pub_rec = service.get_published_message(str(guild.id), feature_type="TASK_DASHBOARD")
        desc = (
            "Complete community missions.\n"
            "Submit proof.\n"
            "Earn OBX."
        )
        embed = discord.Embed(
            title="🎯 OBX MISSIONS",
            description=desc,
            color=COLOR_GOLD,
        )
        embed.set_footer(text="✦ OBX COMMUNITY MISSIONS")
        from apps.obx_tasks.bot.help_views import TaskCenterIntroView
        view = TaskCenterIntroView()

        # Try editing existing message in the same channel
        if pub_rec and pub_rec.channel_id == str(channel.id):
            try:
                msg = await channel.fetch_message(int(pub_rec.message_id))
                await msg.edit(embed=embed, view=view)
                logger.info("Updated existing Task Center in %s (Msg ID: %s)", channel.name, msg.id)
                return True, f"✅ Task Center refreshed in {channel.mention}."
            except Exception as exc:
                logger.warning("Could not edit previous Task Center message (%s), posting new: %s", pub_rec.message_id, exc)

        # Post new dashboard message
        try:
            msg = await channel.send(embed=embed, view=view)
            service.record_published_message(
                guild_id=str(guild.id),
                feature_type="TASK_DASHBOARD",
                channel_id=str(channel.id),
                message_id=str(msg.id),
            )
            logger.info("Deployed new Task Center in %s (Msg ID: %s)", channel.name, msg.id)
            return True, f"✅ Task Center deployed successfully to {channel.mention}."
        except Exception as exc:
            logger.error("Error posting Task Center to %s: %s", channel.name, exc)
            return False, f"Error sending message to {channel.mention}: {str(exc)}"


async def deploy_or_update_leaderboard(guild: discord.Guild, bot: discord.Client) -> Tuple[bool, str]:
    """Deploy or update persistent public leaderboard in the configured channel."""
    with session_scope() as session:
        ch_service = ChannelService(session)
        config = ch_service.get_or_create_guild_config(str(guild.id))
        channel = resolve_channel_for_feature(
            guild,
            explicit_id=config.leaderboard_channel_id or get_settings().DISCORD_LEADERBOARD_CHANNEL_ID,
            name_keywords=["leaderboard", "top-raiders", "ranks"],
        )

        if not channel:
            return False, "Leaderboard channel is not configured. Use Admin Hub -> Configure Channels."

        if not config.leaderboard_channel_id:
            config.leaderboard_channel_id = str(channel.id)
            session.commit()

        me = guild.me or guild.get_member(bot.user.id)
        if me:
            valid, missing = check_channel_permissions(channel, me)
            if not valid:
                return False, f"Missing bot permissions in {channel.mention}: {', '.join(missing)}"

        lb_service = LeaderboardService(session)
        entries, total_count = lb_service.get_leaderboard(
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
        pub_rec = ch_service.get_published_message(str(guild.id), feature_type="LEADERBOARD")

        if pub_rec and pub_rec.channel_id == str(channel.id):
            try:
                msg = await channel.fetch_message(int(pub_rec.message_id))
                await msg.edit(embed=embed, view=view)
                logger.info("Updated existing Leaderboard in %s (Msg ID: %s)", channel.name, msg.id)
                return True, f"✅ Leaderboard refreshed in {channel.mention}."
            except Exception as exc:
                logger.warning("Could not edit previous Leaderboard message (%s), posting new: %s", pub_rec.message_id, exc)

        try:
            msg = await channel.send(embed=embed, view=view)
            ch_service.record_published_message(
                guild_id=str(guild.id),
                feature_type="LEADERBOARD",
                channel_id=str(channel.id),
                message_id=str(msg.id),
            )
            logger.info("Deployed new Leaderboard in %s (Msg ID: %s)", channel.name, msg.id)
            return True, f"✅ Leaderboard deployed successfully to {channel.mention}."
        except Exception as exc:
            logger.error("Error posting Leaderboard to %s: %s", channel.name, exc)
            return False, f"Error sending message to {channel.mention}: {str(exc)}"


async def announce_task(
    task, guild: discord.Guild, bot: discord.Client
) -> Tuple[bool, str]:
    """Publish or update a premium task card in the configured Tasks channel.

    On first publish of an active task with a social URL, a SECOND standalone
    plain-text message containing only the raw URL is posted immediately after
    the embed card. Discord suppresses URL unfurling when an embed is present in
    the same message; the separate message allows the native link preview to render.

    On refresh/update, only the existing embed message is edited — no second URL
    message is re-sent.
    """
    with session_scope() as session:
        ch_service = ChannelService(session)
        config = ch_service.get_or_create_guild_config(str(guild.id))
        channel = resolve_channel_for_feature(
            guild,
            explicit_id=config.tasks_channel_id or get_settings().DISCORD_TASK_CHANNEL_ID,
            name_keywords=["tasks", "missions", "task", "mission"],
        )

        if not channel:
            logger.info("Tasks channel not configured for guild %s; skipping task announcement.", guild.id)
            return False, "Tasks channel is not configured."

        if not config.tasks_channel_id:
            config.tasks_channel_id = str(channel.id)
            session.commit()

        me = guild.me or guild.get_member(bot.user.id)
        if me:
            valid, missing = check_channel_permissions(channel, me)
            if not valid:
                return False, f"Missing bot permissions in {channel.mention}: {', '.join(missing)}"

        import uuid
        task_uuid = task.id if isinstance(task.id, uuid.UUID) else uuid.UUID(str(task.id))
        db_task = session.query(Task).filter_by(id=task_uuid).first() or task

        from datetime import datetime, timezone
        now = datetime.now(timezone.utc)
        ends_at = db_task.ends_at
        if ends_at is not None and ends_at.tzinfo is None:
            ends_at = ends_at.replace(tzinfo=timezone.utc)
        is_expired = (db_task.status == TaskStatus.EXPIRED) or (
            ends_at is not None and now > ends_at
        )
        is_cancelled = (db_task.status == TaskStatus.CANCELLED)
        is_active = (
            db_task.status == TaskStatus.ACTIVE
            and not is_expired
            and not is_cancelled
            and db_task.distributed_reward < db_task.total_reward_pool
        )
        # Enrich preview metadata if not yet fetched or if description is missing
        needs_preview_fetch = (
            bool(db_task.target_url)
            and (
                not getattr(db_task, "preview_fetched_at", None)
                or (not getattr(db_task, "preview_description", None) and getattr(db_task, "preview_status", "") != "SUCCESS")
            )
            and not getattr(db_task, "preview_text_override", None)
        )
        if needs_preview_fetch:
            try:
                from apps.obx_tasks.services.url_preview_service import UrlPreviewService
                preview_meta = await UrlPreviewService.fetch_preview(
                    db_task.target_url,
                    task_id=str(db_task.id),
                )
                task_service = TaskService(session)
                author_to_save = preview_meta.author
                if preview_meta.handle and preview_meta.handle != author_to_save:
                    author_to_save = f"{preview_meta.author}\n   {preview_meta.handle}" if preview_meta.author else preview_meta.handle

                db_task = task_service.update_task_preview(
                    task_id=db_task.id,
                    preview_platform=preview_meta.platform,
                    preview_author=author_to_save,
                    preview_title=preview_meta.title,
                    preview_description=preview_meta.description,
                    preview_image_url=preview_meta.image_url,
                    preview_source=preview_meta.source,
                    preview_status=preview_meta.status,
                )
            except Exception as prev_err:
                logger.debug("Could not fetch preview metadata for task %s: %s", db_task.id, prev_err)

        # Log exact X preview pipeline state as requested
        post_id = "N/A"
        if db_task.target_url and "/status/" in db_task.target_url:
            post_id = db_task.target_url.split("/status/")[1].split("?")[0]

        logger.info(
            "\n[X_PREVIEW_PIPELINE_DEBUG]\n"
            "TARGET URL: %s\n"
            "EXTRACTED PLATFORM: %s\n"
            "EXTRACTED POST ID: %s\n"
            "EXTRACTED AUTHOR: %s\n"
            "EXTRACTED POST TEXT: %s\n"
            "EXTRACTED IMAGE: %s\n"
            "PREVIEW PROVIDER USED: %s\n"
            "FINAL preview_description: %s\n"
            "FINAL task.instructions: %s",
            db_task.target_url,
            getattr(db_task, "preview_platform", None),
            post_id,
            getattr(db_task, "preview_author", None),
            (getattr(db_task, "preview_description", None) or "None"),
            getattr(db_task, "preview_image_url", None),
            getattr(db_task, "preview_source", None),
            (getattr(db_task, "preview_description", None) or "None"),
            (getattr(db_task, "description", None) or "None"),
        )

        embed = build_task_announcement_embed(db_task)
        view = TaskAnnouncementCardView(
            task_id=str(db_task.id),
            is_active=is_active,
            target_url=db_task.target_url,
            is_cancelled=is_cancelled,
            is_expired=is_expired,
        )

        # Determine notification content for initial publication (only RAID_ROLE_ID, NO @everyone, NO generic text)
        raid_role_id, raid_role = resolve_raider_role(guild)
        if is_active and raid_role_id:
            initial_content = f"<@&{raid_role_id}>"
        else:
            initial_content = None

        pub_rec = ch_service.get_published_message(
            str(guild.id), feature_type="TASK_ANNOUNCEMENT", source_id=str(db_task.id)
        )

        if pub_rec and pub_rec.channel_id == str(channel.id):
            try:
                msg = await channel.fetch_message(int(pub_rec.message_id))
                # Never re-ping on in-place update/edit/refresh
                await msg.edit(content=None, embed=embed, view=view)
                logger.info(
                    "Updated existing task announcement in %s (Msg ID: %s, Task ID: %s)",
                    channel.name, msg.id, db_task.id,
                )
                return True, f"✅ Task announcement updated in {channel.mention}."
            except Exception as exc:
                logger.warning(
                    "Could not edit previous task announcement (%s), posting new: %s",
                    pub_rec.message_id, exc,
                )

        # First publish: post the single embed card with initial notification
        try:
            msg = await channel.send(
                content=initial_content,
                embed=embed,
                view=view,
                allowed_mentions=discord.AllowedMentions(roles=[raid_role] if raid_role else True),
            )
            ch_service.record_published_message(
                guild_id=str(guild.id),
                feature_type="TASK_ANNOUNCEMENT",
                channel_id=str(channel.id),
                message_id=str(msg.id),
                source_id=str(db_task.id),
            )
            logger.info(
                "Published new task announcement in %s (Msg ID: %s, Task ID: %s)",
                channel.name, msg.id, db_task.id,
            )
            return True, f"✅ Task card published to {channel.mention}."
        except Exception as exc:
            logger.error("Error posting task announcement to %s: %s", channel.name, exc)
            return False, f"Error sending task announcement: {str(exc)}"




async def send_admin_log_event(
    guild: discord.Guild,
    title: str,
    description: str,
    color: int,
    fields: Optional[List[Tuple[str, str, bool]]] = None,
) -> None:
    """Send a private operational event log to the configured Admin Logs channel."""
    try:
        with session_scope() as session:
            ch_service = ChannelService(session)
            config = ch_service.get_or_create_guild_config(str(guild.id))
            channel_id = getattr(config, "admin_logs_channel_id", None) or getattr(config, "admin_channel_id", None) or get_settings().DISCORD_ADMIN_LOG_CHANNEL_ID
            if not channel_id:
                return

            channel = guild.get_channel(int(channel_id))
            if not channel or not isinstance(channel, discord.TextChannel):
                return

            embed = discord.Embed(title=title, description=description, color=color, timestamp=datetime.now(timezone.utc))
            if fields:
                for name, value, inline in fields:
                    embed.add_field(name=name, value=value, inline=inline)
            embed.set_footer(text="OBX Administrative Audit Log • Private Operations")

            await channel.send(embed=embed)
    except Exception as exc:
        logger.warning("Could not send private admin log: %s", exc)


async def announce_auction(auction: Auction, guild: discord.Guild, bot: discord.Client) -> Tuple[bool, str]:
    """Publish or update a persistent interactive auction card in the configured Auctions channel."""
    with session_scope() as session:
        ch_service = ChannelService(session)
        config = ch_service.get_or_create_guild_config(str(guild.id))
        channel = resolve_channel_for_feature(
            guild,
            explicit_id=config.auctions_channel_id or get_settings().DISCORD_AUCTION_CHANNEL_ID,
            name_keywords=["auctions", "auction", "whitelist"],
        )

        if not channel:
            logger.info("Auctions channel not configured for guild %s; skipping auto-announcement.", guild.id)
            return False, "Auctions channel is not configured."

        if not config.auctions_channel_id:
            config.auctions_channel_id = str(channel.id)
            session.commit()

        me = guild.me or guild.get_member(bot.user.id)
        if me:
            valid, missing = check_channel_permissions(channel, me)
            if not valid:
                return False, f"Missing bot permissions in {channel.mention}: {', '.join(missing)}"

        import uuid
        auc_uuid = auction.id if isinstance(auction.id, uuid.UUID) else uuid.UUID(str(auction.id))
        db_auc = session.query(Auction).filter_by(id=auc_uuid).first() or auction

        auc_service = AuctionService(session)
        standings = auc_service.get_auction_standings(db_auc.id)

        from datetime import datetime, timezone
        now = datetime.now(timezone.utc)
        is_ended = (db_auc.status in (AuctionStatus.COMPLETED, AuctionStatus.CANCELLED)) or (
            db_auc.ends_at is not None and now > (db_auc.ends_at if db_auc.ends_at.tzinfo else db_auc.ends_at.replace(tzinfo=timezone.utc))
        )
        is_active = (db_auc.status == AuctionStatus.ACTIVE) and (not is_ended)

        from apps.obx_tasks.bot.auction_views import build_auction_notification_embed, AuctionNotificationCardView
        embed = build_auction_notification_embed(db_auc, standings=standings)
        view = AuctionNotificationCardView(
            auction_id=str(db_auc.id),
            is_active=is_active,
            is_fcfs=(db_auc.auction_type == AuctionType.FCFS),
            external_url=db_auc.external_url,
        )

        raid_role_id, raid_role = resolve_raider_role(guild)
        if is_active and raid_role_id:
            initial_content = f"<@&{raid_role_id}>"
        else:
            initial_content = None

        pub_rec = (
            ch_service.get_published_message(str(guild.id), feature_type="AUCTION_ANNOUNCEMENT", source_id=str(db_auc.id))
            or ch_service.get_published_message(str(guild.id), feature_type="AUCTION", source_id=str(db_auc.id))
        )

        if pub_rec and pub_rec.channel_id == str(channel.id):
            try:
                msg = await channel.fetch_message(int(pub_rec.message_id))
                # Never re-ping on in-place update/edit/refresh
                await msg.edit(content=None, embed=embed, view=view)
                logger.info("Updated existing auction card in %s (Msg ID: %s, Auc ID: %s)", channel.name, msg.id, db_auc.id)
                return True, f"✅ Auction card updated in {channel.mention}."
            except Exception as exc:
                logger.warning("Could not edit previous auction announcement (%s), posting new: %s", pub_rec.message_id, exc)

        try:
            logger.info(
                "[AUCTION] Publishing auction %s to %s with role ping %s (Role: %s)",
                db_auc.id, channel.name, initial_content, raid_role.name if raid_role else "None",
            )
            msg = await channel.send(
                content=initial_content,
                embed=embed,
                view=view,
                allowed_mentions=discord.AllowedMentions(roles=[raid_role] if raid_role else True),
            )
            ch_service.record_published_message(
                guild_id=str(guild.id),
                feature_type="AUCTION_ANNOUNCEMENT",
                channel_id=str(channel.id),
                message_id=str(msg.id),
                source_id=str(db_auc.id),
            )
            logger.info("Published new auction card in %s (Msg ID: %s, Auc ID: %s)", channel.name, msg.id, db_auc.id)
            return True, f"✅ Auction card published to {channel.mention}."
        except Exception as exc:
            logger.error("Error posting auction announcement to %s: %s", channel.name, exc)
            return False, f"Error sending auction card: {str(exc)}"


async def announce_auction_winners(
    auction: Auction,
    winners: List[AuctionBid],
    total_bidders: int,
    guild: discord.Guild,
    bot: discord.Client,
) -> Tuple[bool, str]:
    """Publish sanitized public winner announcements in the configured Winners channel."""
    with session_scope() as session:
        ch_service = ChannelService(session)
        config = ch_service.get_or_create_guild_config(str(guild.id))
        channel = resolve_channel_for_feature(
            guild,
            explicit_id=config.winners_channel_id or get_settings().DISCORD_WINNERS_CHANNEL_ID or config.auctions_channel_id or get_settings().DISCORD_AUCTION_CHANNEL_ID,
            name_keywords=["winners", "winner", "auctions", "auction"],
        )

        if not channel:
            logger.info("Neither Winners nor Auctions channel configured for guild %s; skipping public winner announcement.", guild.id)
            return False, "Winners channel not configured."

        if not config.winners_channel_id:
            config.winners_channel_id = str(channel.id)
            session.commit()

        me = guild.me or guild.get_member(bot.user.id)
        if me:
            valid, missing = check_channel_permissions(channel, me)
            if not valid:
                return False, f"Missing bot permissions in {channel.mention}: {', '.join(missing)}"

        raid_role_id, raid_role = resolve_raider_role(guild)
        initial_content = f"<@&{raid_role_id}>" if raid_role_id else None

        from packages.database.models.raider_profile import RaiderProfile
        from packages.database.models.auction import Auction

        db_auc = session.query(Auction).filter_by(id=auction.id).first() or auction

        project_name = db_auc.preview_x_display_name or db_auc.title
        project_desc = db_auc.preview_x_bio or db_auc.description

        winner_user_ids = [str(w.discord_user_id) for w in winners]
        profiles_by_uid = {
            p.discord_user_id: p
            for p in session.query(RaiderProfile).filter(RaiderProfile.discord_user_id.in_(winner_user_ids)).all()
        } if winner_user_ids else {}

        win_lines = []
        medals = {1: "🥇", 2: "🥈", 3: "🥉"}
        for idx, w in enumerate(winners, start=1):
            badge = medals.get(idx, f"`#{idx}`")
            uid = str(w.discord_user_id)
            member = guild.get_member(int(uid)) if (guild and uid.isdigit()) else None
            if member:
                name = (member.display_name or member.name or "").lstrip("@")
                winner_tag = f"@{name}" if name else f"<@{uid}>"
            else:
                winner_tag = f"<@{uid}>"

            win_lines.append(f"{badge} {winner_tag}")

        winners_block = "\n\n".join(win_lines) if win_lines else "*No bids placed.*"

        desc_lines = [
            f"**{project_name}**",
            "",
            f"{project_desc}",
            "",
            "━━━━━━━━━━━━━━━━━━━━",
            "",
            "🏆 **WINNERS**",
            "",
            winners_block,
            "",
            "━━━━━━━━━━━━━━━━━━━━",
            "",
            "🎟️ **SPOTS AWARDED**",
            f"{len(winners)} / {db_auc.total_slots}",
        ]

        embed = discord.Embed(
            title="🏆 AUCTION RESULTS",
            description="\n".join(desc_lines),
            color=COLOR_GOLD,
        )

        if db_auc.preview_image_url:
            embed.set_image(url=db_auc.preview_image_url)

        from apps.obx_tasks.bot.channel_views import AuctionWinnerResultView
        view = AuctionWinnerResultView(auction_id=str(db_auc.id))

        pub_rec = ch_service.get_published_message(str(guild.id), feature_type="AUCTION_RESULTS", source_id=str(auction.id))
        if pub_rec and pub_rec.channel_id == str(channel.id):
            try:
                msg = await channel.fetch_message(int(pub_rec.message_id))
                await msg.edit(content=None, embed=embed, view=view)
                return True, f"✅ Winner announcement updated in {channel.mention}."
            except Exception as exc:
                logger.warning("Could not edit previous winner announcement (%s), posting new: %s", pub_rec.message_id, exc)

        try:
            msg = await channel.send(
                content=initial_content,
                embed=embed,
                view=view,
                allowed_mentions=discord.AllowedMentions(roles=[raid_role] if raid_role else True),
            )
            ch_service.record_published_message(
                guild_id=str(guild.id),
                feature_type="AUCTION_RESULTS",
                channel_id=str(channel.id),
                message_id=str(msg.id),
                source_id=str(auction.id),
            )
            logger.info("Published new winner announcement in %s (Msg ID: %s, Auc ID: %s)", channel.name, msg.id, auction.id)

            # Update the original auction card in #auctions in place to ENDED!
            try:
                await announce_auction(auction, guild, bot)
            except Exception as upd_err:
                logger.warning("Could not update auction card after winner announcement: %s", upd_err)

            return True, f"✅ Winner announcement posted in {channel.mention}."
        except Exception as exc:
            logger.error("Error posting winner announcement to %s: %s", channel.name, exc)
            return False, f"Error sending winner announcement: {str(exc)}"


async def announce_auction_ending_soon(
    auction: Auction,
    guild: discord.Guild,
    bot: discord.Client,
) -> Tuple[bool, str]:
    """Publish an auction ending warning notification to configured channel."""
    with session_scope() as session:
        ch_service = ChannelService(session)
        config = ch_service.get_or_create_guild_config(str(guild.id))
        channel_id = config.auctions_channel_id
        if not channel_id:
            return False, "Auctions channel not configured."

        channel = guild.get_channel(int(channel_id))
        if not channel or not isinstance(channel, discord.TextChannel):
            return False, "Auctions channel not found."

        # Check if warning was already published
        pub_rec = ch_service.get_published_message(str(guild.id), feature_type="AUCTION_ENDING_WARNING", source_id=str(auction.id))
        if pub_rec:
            return True, "Ending warning already posted."

        raid_role_id, raid_role = resolve_raider_role(guild)
        warn_content = f"<@&{raid_role_id}>" if raid_role_id else None
        ends_ts = int(auction.ends_at.timestamp()) if auction.ends_at else 0
        embed = discord.Embed(
            title=f"⏳ CLOSING SOON — {auction.title.upper()}",
            description=(
                f"**{auction.reward_title}**\n\n"
                f"Auction closes <t:{ends_ts}:R>.\n"
                "Finalize your bids before the countdown expires!"
            ),
            color=COLOR_GOLD,
        )
        embed.set_footer(text="OBX Whitelist Auctions")
        from apps.obx_tasks.bot.auction_views import AuctionNotificationCardView
        view = AuctionNotificationCardView(
            auction_id=str(auction.id),
            is_active=True,
            is_fcfs=(auction.auction_type == AuctionType.FCFS),
            external_url=auction.external_url,
        )
        try:
            msg = await channel.send(
                content=warn_content,
                embed=embed,
                view=view,
                allowed_mentions=discord.AllowedMentions(roles=[raid_role] if raid_role else True),
            )
            ch_service.record_published_message(
                guild_id=str(guild.id),
                feature_type="AUCTION_ENDING_WARNING",
                channel_id=str(channel.id),
                message_id=str(msg.id),
                source_id=str(auction.id),
            )
            return True, "Ending warning posted."
        except Exception as exc:
            logger.error("Error posting auction ending warning to %s: %s", channel.name, exc)
            return False, f"Error sending ending warning: {str(exc)}"


async def deploy_or_update_auction_center(guild: discord.Guild, bot: discord.Client) -> Tuple[bool, str]:
    """Deploy or update persistent Auction Center dashboard in the configured channel."""
    with session_scope() as session:
        ch_service = ChannelService(session)
        config = ch_service.get_or_create_guild_config(str(guild.id))
        channel = resolve_channel_for_feature(
            guild,
            explicit_id=config.auctions_channel_id or get_settings().DISCORD_AUCTION_CHANNEL_ID,
            name_keywords=["auctions", "auction", "whitelist"],
        )

        if not channel:
            return False, "Auctions channel is not configured. Use Admin Hub -> Configure Channels."

        if not config.auctions_channel_id:
            config.auctions_channel_id = str(channel.id)
            session.commit()

        me = guild.me or guild.get_member(bot.user.id)
        if me:
            valid, missing = check_channel_permissions(channel, me)
            if not valid:
                return False, f"Missing bot permissions in {channel.mention}: {', '.join(missing)}"

        desc = (
            "Bid your OBX.\n"
            "Compete for opportunities.\n"
            "Secure your position."
        )
        embed = discord.Embed(
            title="🔨 OBX AUCTIONS",
            description=desc,
            color=COLOR_PURPLE,
        )
        embed.set_footer(text="✦ OBX COMMUNITY AUCTIONS")

        from apps.obx_tasks.bot.help_views import AuctionCenterIntroView
        view = AuctionCenterIntroView()
        pub_rec = ch_service.get_published_message(str(guild.id), feature_type="AUCTION_CENTER")

        if pub_rec and pub_rec.channel_id == str(channel.id):
            try:
                msg = await channel.fetch_message(int(pub_rec.message_id))
                await msg.edit(embed=embed, view=view)
                logger.info("Updated existing Auction Center in %s (Msg ID: %s)", channel.name, msg.id)
                return True, f"✅ Auction Center refreshed in {channel.mention}."
            except Exception as exc:
                logger.warning("Could not edit previous Auction Center message (%s), posting new: %s", pub_rec.message_id, exc)

        try:
            msg = await channel.send(embed=embed, view=view)
            ch_service.record_published_message(
                guild_id=str(guild.id),
                feature_type="AUCTION_CENTER",
                channel_id=str(channel.id),
                message_id=str(msg.id),
            )
            logger.info("Deployed new Auction Center in %s (Msg ID: %s)", channel.name, msg.id)
            return True, f"✅ Auction Center deployed successfully to {channel.mention}."
        except Exception as exc:
            logger.error("Error posting Auction Center to %s: %s", channel.name, exc)
            return False, f"Error sending message to {channel.mention}: {str(exc)}"


async def deploy_or_update_admin_hub(guild: discord.Guild, bot: discord.Client) -> Tuple[bool, str]:
    """Deploy or update persistent Admin Hub in the configured private admin channel."""
    with session_scope() as session:
        ch_service = ChannelService(session)
        config = ch_service.get_or_create_guild_config(str(guild.id))
        channel = resolve_channel_for_feature(
            guild,
            explicit_id=config.admin_channel_id or get_settings().DISCORD_ADMIN_LOG_CHANNEL_ID,
            name_keywords=["admin-logs", "admin-log", "admin_logs", "admin_log", "admin-hub", "admin"],
        )

        if not channel:
            return False, "Admin channel is not configured. Use Admin Hub -> Configure Channels."

        if not config.admin_channel_id:
            config.admin_channel_id = str(channel.id)
            session.commit()

        me = guild.me or guild.get_member(bot.user.id)
        if me:
            valid, missing = check_channel_permissions(channel, me)
            if not valid:
                return False, f"Missing bot permissions in {channel.mention}: {', '.join(missing)}"

            # Enforce Channel Privacy: Ensure @everyone cannot view, only admin role & bot can view
            try:
                bot_perms = channel.permissions_for(me)
                if bot_perms.manage_channels or bot_perms.manage_roles:
                    settings = get_settings()
                    overwrites = dict(channel.overwrites)
                    # Deny @everyone
                    overwrites[guild.default_role] = discord.PermissionOverwrite(view_channel=False)
                    # Allow bot
                    overwrites[me] = discord.PermissionOverwrite(view_channel=True, send_messages=True, embed_links=True, read_message_history=True)
                    # Allow configured admin roles
                    for rid in settings.DISCORD_ADMIN_ROLE_IDS:
                        admin_role = guild.get_role(int(rid))
                        if admin_role:
                            overwrites[admin_role] = discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True)
                    await channel.edit(overwrites=overwrites)
                    logger.info("Enforced private admin channel permissions on #%s", channel.name)
            except Exception as perm_err:
                logger.warning("Could not auto-enforce channel permissions on %s: %s", channel.name, perm_err)

        from apps.obx_tasks.bot.dashboard_views import create_admin_hub_embed, OBXAdminHubView
        embed = create_admin_hub_embed()
        view = OBXAdminHubView()
        pub_rec = ch_service.get_published_message(str(guild.id), feature_type="ADMIN_HUB")

        if pub_rec and pub_rec.channel_id == str(channel.id):
            try:
                msg = await channel.fetch_message(int(pub_rec.message_id))
                await msg.edit(embed=embed, view=view)
                logger.info("Updated existing Admin Hub in %s (Msg ID: %s)", channel.name, msg.id)
                return True, f"✅ Admin Hub refreshed in {channel.mention}."
            except Exception as exc:
                logger.warning("Could not edit previous Admin Hub message (%s), posting new: %s", pub_rec.message_id, exc)

        try:
            msg = await channel.send(embed=embed, view=view)
            ch_service.record_published_message(
                guild_id=str(guild.id),
                feature_type="ADMIN_HUB",
                channel_id=str(channel.id),
                message_id=str(msg.id),
            )
            logger.info("Deployed new Admin Hub in %s (Msg ID: %s)", channel.name, msg.id)
            return True, f"✅ Admin Hub deployed successfully to {channel.mention}."
        except Exception as exc:
            logger.error("Error posting Admin Hub to %s: %s", channel.name, exc)
            return False, f"Error sending message to {channel.mention}: {str(exc)}"


async def deploy_or_update_winners_center(guild: discord.Guild, bot: discord.Client) -> Tuple[bool, str]:
    """Deploy or update persistent Winners/Results dashboard in the configured channel."""
    with session_scope() as session:
        ch_service = ChannelService(session)
        config = ch_service.get_or_create_guild_config(str(guild.id))
        channel = resolve_channel_for_feature(
            guild,
            explicit_id=config.winners_channel_id or get_settings().DISCORD_WINNERS_CHANNEL_ID or config.auctions_channel_id or get_settings().DISCORD_AUCTION_CHANNEL_ID,
            name_keywords=["winners", "winner", "auctions", "auction"],
        )

        if not channel:
            return False, "Winners channel is not configured. Use Admin Hub -> Configure Channels."

        if not config.winners_channel_id:
            config.winners_channel_id = str(channel.id)
            session.commit()

        me = guild.me or guild.get_member(bot.user.id)
        if me:
            valid, missing = check_channel_permissions(channel, me)
            if not valid:
                return False, f"Missing bot permissions in {channel.mention}: {', '.join(missing)}"

        desc = "See confirmed winners and completed results."
        embed = discord.Embed(
            title="🏆 OBX RESULTS",
            description=desc,
            color=COLOR_GOLD,
        )
        embed.set_footer(text="✦ OBX RESULTS")
        from apps.obx_tasks.bot.help_views import WinnersCenterIntroView
        view = WinnersCenterIntroView()

        pub_rec = ch_service.get_published_message(str(guild.id), feature_type="WINNERS_CENTER")

        if pub_rec and pub_rec.channel_id == str(channel.id):
            try:
                msg = await channel.fetch_message(int(pub_rec.message_id))
                await msg.edit(embed=embed, view=view)
                logger.info("Updated existing Winners Center in %s (Msg ID: %s)", channel.name, msg.id)
                return True, f"✅ Winners Center refreshed in {channel.mention}."
            except Exception as exc:
                logger.warning("Could not edit previous Winners Center message (%s), posting new: %s", pub_rec.message_id, exc)

        try:
            msg = await channel.send(embed=embed, view=view)
            ch_service.record_published_message(
                guild_id=str(guild.id),
                feature_type="WINNERS_CENTER",
                channel_id=str(channel.id),
                message_id=str(msg.id),
            )
            logger.info("Deployed new Winners Center in %s (Msg ID: %s)", channel.name, msg.id)
            return True, f"✅ Winners Center deployed successfully to {channel.mention}."
        except Exception as exc:
            logger.error("Error posting Winners Center to %s: %s", channel.name, exc)
            return False, f"Error sending message to {channel.mention}: {str(exc)}"


async def deploy_or_update_join_raid_center(guild: discord.Guild, bot: discord.Client) -> Tuple[bool, str]:
    """Deploy or update persistent Join Raid onboarding card in the configured #join-raid channel."""
    settings = get_settings()
    channel_id = settings.RAID_JOIN_CHANNEL_ID

    channel = None
    if channel_id:
        try:
            channel = guild.get_channel(int(channel_id))
        except (ValueError, TypeError):
            pass

    if not channel:
        for ch in guild.text_channels:
            if ch.name in ("join-raid", "⚡・join-raid", "⚡-join-raid"):
                channel = ch
                break

    if not channel:
        logger.info("Join raid channel is not configured or found for guild %s", guild.id)
        return False, "Join raid channel not found."

    me = guild.me or guild.get_member(bot.user.id)
    if me:
        valid, missing = check_channel_permissions(channel, me)
        if not valid:
            return False, f"Missing bot permissions in {channel.mention}: {', '.join(missing)}"

    from apps.obx_tasks.bot.join_raid_views import build_join_raid_embed, JoinRaidView
    embed = build_join_raid_embed()
    view = JoinRaidView()

    with session_scope() as session:
        ch_service = ChannelService(session)
        pub_rec = ch_service.get_published_message(str(guild.id), feature_type="JOIN_RAID_CENTER")

        if pub_rec and pub_rec.channel_id == str(channel.id):
            try:
                msg = await channel.fetch_message(int(pub_rec.message_id))
                await msg.edit(embed=embed, view=view)
                logger.info("Updated existing Join Raid card in %s (Msg ID: %s)", channel.name, msg.id)
                return True, f"✅ Join Raid card updated in {channel.mention}."
            except Exception as exc:
                logger.warning("Could not edit existing Join Raid card, re-posting: %s", exc)

        try:
            msg = await channel.send(embed=embed, view=view)
            ch_service.record_published_message(
                guild_id=str(guild.id),
                feature_type="JOIN_RAID_CENTER",
                channel_id=str(channel.id),
                message_id=str(msg.id),
            )
            logger.info("Deployed new Join Raid card in %s (Msg ID: %s)", channel.name, msg.id)
            return True, f"✅ Join Raid card deployed successfully to {channel.mention}."
        except Exception as exc:
            logger.error("Error posting Join Raid card to %s: %s", channel.name, exc)
            return False, f"Error sending message to {channel.mention}: {str(exc)}"


async def refresh_all_public_systems(guild: discord.Guild, bot: discord.Client) -> Dict[str, str]:
    """Safely refresh Join Raid, Task Center, Leaderboard, Auction Center, Winners Center, and Admin Hub across configured channels."""
    results = {}
    ok_raid, msg_raid = await deploy_or_update_join_raid_center(guild, bot)
    results["Join Raid"] = msg_raid

    ok_task, msg_task = await deploy_or_update_task_center(guild, bot)
    results["Tasks"] = msg_task

    ok_lb, msg_lb = await deploy_or_update_leaderboard(guild, bot)
    results["Leaderboard"] = msg_lb

    ok_auc, msg_auc = await deploy_or_update_auction_center(guild, bot)
    results["Auctions"] = msg_auc

    ok_win, msg_win = await deploy_or_update_winners_center(guild, bot)
    results["Winners"] = msg_win

    ok_adm, msg_adm = await deploy_or_update_admin_hub(guild, bot)
    results["Admin Hub"] = msg_adm

    return results
