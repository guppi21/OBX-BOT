import inspect
import traceback
import discord
from typing import Optional, Tuple, Union
from packages.database.models.task import Task
from packages.database.models.submission import TaskSubmission
from apps.obx_tasks.bot.ui_theme import COLOR_GOLD, COLOR_CRYSTAL_BLUE, FOOTER_REWARDS
from packages.shared.logging import get_logger

logger = get_logger("obx.tasks.bot.notifications")


def format_custom_template(
    template: str,
    user_mention: str,
    display_name: str,
    task_title: str,
    reward: int,
    new_balance: int,
) -> str:
    """Safely substitutes whitelist template variables in custom notification text.
    Allowed variables: {user}, {display_name}, {task_title}, {reward}, {new_balance}.
    Does not use eval or format() with unchecked kwargs to prevent injection.
    """
    if not template:
        return ""
    result = str(template)
    replacements = {
        "{user}": user_mention,
        "{display_name}": display_name,
        "{task_title}": task_title,
        "{reward}": f"{reward:,}",
        "{new_balance}": f"{new_balance:,}",
    }
    for placeholder, val in replacements.items():
        result = result.replace(placeholder, val)
    return result


class RewardCelebrationView(discord.ui.View):
    """Action buttons attached to reward congratulations notification."""
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(discord.ui.Button(
            label="My Wallet",
            style=discord.ButtonStyle.primary,
            custom_id="obx:dashboard:my_balance",
            row=0,
        ))


def build_reward_celebration_embed(
    task: Task,
    discord_user_id: str,
    reward_amount: int,
    new_balance: int,
    display_name: Optional[str] = None,
) -> Optional[Tuple[discord.Embed, discord.ui.View]]:
    """Builds a polished reward congratulations embed and action view.
    Returns None if notifications are disabled (notification_type='NONE').
    """
    notif_type = (task.notification_type or "DEFAULT").upper()
    if notif_type in ("NONE", "DISABLED", "MUTED"):
        return None

    name_str = display_name or f"<@{discord_user_id}>"
    user_mention = f"<@{discord_user_id}>"

    if notif_type == "CUSTOM" and task.custom_notification_template:
        formatted_desc = format_custom_template(
            template=task.custom_notification_template,
            user_mention=user_mention,
            display_name=name_str,
            task_title=task.title,
            reward=reward_amount,
            new_balance=new_balance,
        )
        embed = discord.Embed(
            title="✨ MISSION COMPLETE",
            description=formatted_desc,
            color=COLOR_CRYSTAL_BLUE,
        )
    else:
        # Default celebration message layout
        desc_lines = [
            f"Congratulations, {name_str}!",
            "",
            "You successfully completed:",
            f"**{task.title}**",
            "",
            "💎 **REWARD EARNED**",
            f"`+{reward_amount:,} OBX`",
            "",
            "💼 **NEW BALANCE**",
            f"`{new_balance:,} OBX`",
        ]
        embed = discord.Embed(
            title="✨ MISSION COMPLETE",
            description="\n".join(desc_lines),
            color=COLOR_CRYSTAL_BLUE,
        )

    embed.set_footer(text=FOOTER_REWARDS)
    view = RewardCelebrationView()
    return embed, view

# In-memory tracking to prevent duplicate DM notifications if interaction retries
_SENT_CELEBRATION_DMS = set()
_SENT_APPROVAL_DMS = set()


from packages.database.session import session_scope
from apps.obx_tasks.services.channel_service import ChannelService
from packages.shared.config import get_settings


async def send_approval_dm(
    bot: discord.Client,
    submission: Optional[TaskSubmission] = None,
    new_balance: int = 0,
    *,
    discord_user_id: Optional[str] = None,
    approved_amount: Optional[int] = None,
    submission_id: Optional[str] = None,
    is_test: bool = False,
    return_detail: bool = False,
) -> Union[bool, Tuple[bool, str]]:
    """Send a minimal congratulations DM to the user after approval.

    Idempotent: will not send a duplicate DM for the same submission.
    Persists across bot restarts and deployment cycles via memory and database.
    If the user's DMs are closed, the reward is still credited — failure is logged privately.
    """
    target_user_id = str(discord_user_id).strip() if discord_user_id else (
        str(submission.discord_user_id).strip() if submission and hasattr(submission, "discord_user_id") else None
    )
    reward = int(approved_amount) if approved_amount is not None else (
        int(submission.reward_amount or 0) if submission and hasattr(submission, "reward_amount") else 0
    )
    sub_id = str(submission_id) if submission_id else (
        str(submission.id) if submission and hasattr(submission, "id") else "test"
    )

    logger.info("[DM] Starting approval DM (is_test=%s, submission_id=%s)", is_test, sub_id)
    logger.info("[DM] Target Discord user ID: %s", target_user_id)

    if not target_user_id:
        err_msg = "Missing or invalid target Discord user ID"
        logger.error("[DM] %s for submission %s", err_msg, sub_id)
        if return_detail:
            return False, err_msg
        return False

    try:
        uid_int = int(target_user_id)
    except (ValueError, TypeError):
        err_msg = f"Target Discord user ID '{target_user_id}' is not a valid integer"
        logger.error("[DM] %s for submission %s", err_msg, sub_id)
        if return_detail:
            return False, err_msg
        return False

    dm_key = f"approval_dm:{sub_id}"
    if not is_test and sub_id != "test":
        if dm_key in _SENT_APPROVAL_DMS:
            logger.info("[DM] Approval DM already sent for submission %s. Skipping duplicate.", sub_id)
            if return_detail:
                return True, "Approval DM already sent (idempotent duplicate skipped)"
            return False

        try:
            with session_scope() as session:
                ch_service = ChannelService(session)
                pub_rec = ch_service.get_published_message(
                    guild_id="dm",
                    feature_type="APPROVAL_DM",
                    source_id=sub_id,
                )
                if pub_rec:
                    _SENT_APPROVAL_DMS.add(dm_key)
                    logger.info("[DM] Approval DM already recorded in DB for submission %s. Skipping duplicate.", sub_id)
                    if return_detail:
                        return True, "Approval DM already recorded in DB (idempotent duplicate skipped)"
                    return False
        except Exception as db_chk_err:
            logger.warning("[DM] Idempotency DB check failed: %s (continuing send)", db_chk_err)

    # Section 4 Required DM format:
    # 🎉 **CONGRATULATIONS!**
    #
    # Your submission has been approved.
    #
    # ━━━━━━━━━━━━━━━━━━━━
    #
    # 💎 **REWARD EARNED**
    # +{approved_amount} OBX
    #
    # 💰 **NEW BALANCE**
    # {new_balance} OBX
    #
    # ━━━━━━━━━━━━━━━━━━━━
    #
    # **Keep raiding. ⚡**
    desc_lines = [
        "Your submission has been approved.",
        "",
        "━━━━━━━━━━━━━━━━━━━━",
        "",
        "💎 **REWARD EARNED**",
        f"+{reward:,} OBX",
        "",
        "💰 **NEW BALANCE**",
        f"{new_balance:,} OBX",
        "",
        "━━━━━━━━━━━━━━━━━━━━",
        "",
        "**Keep raiding. ⚡**",
    ]

    embed = discord.Embed(
        title="🎉 CONGRATULATIONS!",
        description="\n".join(desc_lines),
        color=COLOR_GOLD,
    )

    # Reliable Discord User Resolution (Section 3)
    user = None

    # 1. Cached user from bot client
    if hasattr(bot, "get_user"):
        user = bot.get_user(uid_int)

    # 2. Cached member from any joined guild
    if not user and hasattr(bot, "guilds"):
        for g in bot.guilds:
            m = g.get_member(uid_int)
            if m:
                user = m
                break

    # 3. Fetch user via Discord REST API if missing from cache
    if not user and hasattr(bot, "fetch_user"):
        try:
            res = bot.fetch_user(uid_int)
            user = await res if inspect.isawaitable(res) else res
        except discord.NotFound:
            err_msg = f"Discord user ID {uid_int} was not found (404 NotFound)"
            logger.error("[DM] User resolution failed: %s", err_msg)
            if return_detail:
                return False, err_msg
            return False
        except discord.HTTPException as http_err:
            err_msg = f"Discord API error fetching user {uid_int}: {http_err.text or str(http_err)}"
            logger.error("[DM] User resolution HTTPException for %s: %s\n%s", uid_int, http_err, traceback.format_exc())
            if return_detail:
                return False, err_msg
            return False
        except Exception as u_err:
            err_msg = f"Unexpected error resolving user {uid_int}: {str(u_err)}"
            logger.error("[DM] %s\n%s", err_msg, traceback.format_exc())
            if return_detail:
                return False, err_msg
            return False

    if not user:
        err_msg = f"Could not resolve Discord user ID {uid_int} via cache or API"
        logger.error("[DM] %s", err_msg)
        if return_detail:
            return False, err_msg
        return False

    logger.info("[DM] User resolved successfully: %s (ID: %s)", getattr(user, "name", "User"), user.id)

    # Verification: Ensure resolved user ID matches expected target (when user.id is set)
    if hasattr(user, "id") and isinstance(getattr(user, "id"), (int, str)):
        if str(user.id) != str(uid_int):
            err_msg = f"Resolved user ID ({user.id}) does not match expected target ({uid_int})"
            logger.error("[DM] %s", err_msg)
            if return_detail:
                return False, err_msg
            return False

    # Send DM
    logger.info("[DM] Attempting DM send to user %s", user.id)
    try:
        send_coro = user.send(embed=embed)
        if inspect.isawaitable(send_coro):
            await send_coro
        logger.info("[DM] Approval DM sent successfully to user %s", user.id)

        if not is_test and sub_id != "test":
            _SENT_APPROVAL_DMS.add(dm_key)
            try:
                with session_scope() as session:
                    ch_service = ChannelService(session)
                    ch_service.record_published_message(
                        guild_id="dm",
                        feature_type="APPROVAL_DM",
                        channel_id=str(uid_int),
                        message_id="sent",
                        source_id=sub_id,
                    )
            except Exception as rec_err:
                logger.warning("[DM] Could not record APPROVAL_DM in DB: %s", rec_err)

        if return_detail:
            return True, "DM sent successfully"
        return True

    except discord.Forbidden as f_err:
        err_msg = "User has DMs closed or has blocked the bot (Forbidden 50007)"
        logger.error("[DM] DM delivery failed: User %s has DMs closed or blocked: %s", uid_int, f_err)
        if not is_test and sub_id != "test":
            _SENT_APPROVAL_DMS.add(dm_key)
            try:
                with session_scope() as session:
                    ch_service = ChannelService(session)
                    ch_service.record_published_message(
                        guild_id="dm",
                        feature_type="APPROVAL_DM",
                        channel_id=str(uid_int),
                        message_id="forbidden",
                        source_id=sub_id,
                    )
            except Exception:
                pass
        if return_detail:
            return False, err_msg
        return False

    except discord.HTTPException as http_err:
        err_msg = f"Discord HTTPException sending DM: {http_err.text or str(http_err)}"
        logger.error("[DM] DM send HTTPException for user %s: %s\n%s", uid_int, http_err, traceback.format_exc())
        if return_detail:
            return False, err_msg
        return False

    except Exception as send_err:
        err_msg = f"Unexpected DM send failure: {str(send_err)}"
        logger.error("[DM] %s for user %s\n%s", err_msg, uid_int, traceback.format_exc())
        if return_detail:
            return False, err_msg
        return False


class DismissRewardCelebrationView(discord.ui.View):
    """In-server celebration view with View Wallet and Dismiss buttons."""
    def __init__(self, submission_id: str, user_id: str):
        super().__init__(timeout=None)
        self.submission_id = str(submission_id)
        self.user_id = str(user_id)

        self.add_item(discord.ui.Button(
            label="View Wallet",
            style=discord.ButtonStyle.primary,
            custom_id="obx:user:wallet",
            row=0,
        ))
        self.add_item(discord.ui.Button(
            label="Dismiss",
            style=discord.ButtonStyle.secondary,
            custom_id=f"obx:celebrate:dismiss:{submission_id}:{user_id}",
            row=0,
        ))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        custom_id = interaction.data.get("custom_id", "") if hasattr(interaction, "data") and interaction.data else ""
        if custom_id.startswith("obx:celebrate:dismiss:"):
            if str(interaction.user.id) != self.user_id:
                await interaction.response.send_message(
                    "❌ Only the rewarded member can dismiss this celebration!",
                    ephemeral=True,
                )
                return False
            try:
                await interaction.message.delete()
            except discord.NotFound:
                pass
            except Exception as del_err:
                logger.warning("Could not delete dismissed celebration message: %s", del_err)
            return False
        return True


def build_in_server_celebration(
    task: Task,
    submission: TaskSubmission,
    reward_amount: int,
) -> Tuple[str, discord.Embed, discord.ui.View]:
    """Builds the temporary in-server celebration message, embed, and dismiss view.
    Example:
    ✨ MISSION COMPLETE
    # {user}, YOU JUST EARNED OBX.
    Your mission was verified successfully.
    ━━━━━━━━━━━━━━━━━━━━
    💎  +{reward} OBX
    ━━━━━━━━━━━━━━━━━━━━
    Your reward has been secured in your OBX wallet.
    🔥 Keep stacking. Bigger opportunities are ahead.
    [ 💼 View Wallet ] [ ✕ Dismiss ]
    """
    import random
    from packages.shared.typography import DIVIDER, CELEBRATION_QUOTES

    user_tag = f"<@{submission.discord_user_id}>"
    content = user_tag

    quote = random.choice(CELEBRATION_QUOTES)

    desc_lines = [
        f"# {user_tag}, YOU JUST EARNED OBX.",
        "",
        "Your mission was verified successfully.",
        "",
        DIVIDER,
        "",
        f"💎  **+{reward_amount:,} OBX**",
        "",
        DIVIDER,
        "",
        "Your reward has been secured in your OBX wallet.",
        "",
        f"🔥 *{quote}*",
    ]

    embed = discord.Embed(
        title="✨ MISSION COMPLETE",
        description="\n".join(desc_lines),
        color=COLOR_CRYSTAL_BLUE,
    )
    embed.set_footer(text="✦ OBX COMMUNITY REWARDS")

    view = DismissRewardCelebrationView(
        submission_id=str(submission.id),
        user_id=str(submission.discord_user_id),
    )
    return content, embed, view


async def send_reward_notification(
    bot: discord.Client,
    task: Task,
    submission: TaskSubmission,
    new_balance: int,
    guild: Optional[discord.Guild] = None,
) -> bool:
    """Dispatches a temporary dismissible celebration message in the configured Tasks channel.
    Do NOT send the reward congratulations as a DM.
    Dispatched strictly AFTER the financial reward transaction has committed.
    Failures in notification dispatch never affect the ledger or submission.
    """
    notif_type = (task.notification_type or "DEFAULT").upper()
    if notif_type in ("NONE", "DISABLED", "MUTED"):
        logger.info("Reward notification disabled for Task %s (type=%s)", task.id, notif_type)
        return False

    sub_key = f"celebration:{submission.id}"
    if sub_key in _SENT_CELEBRATION_DMS:
        logger.info("Celebration already posted for submission %s. Skipping duplicate.", submission.id)
        return False

    target_guild = guild
    if not target_guild:
        settings = get_settings()
        g_id = int(settings.DISCORD_GUILD_ID or 0)
        target_guild = bot.get_guild(g_id) if g_id else (bot.guilds[0] if getattr(bot, "guilds", None) else None)

    if not target_guild:
        logger.warning("No guild found for reward celebration (submission=%s)", submission.id)
        return False

    with session_scope() as session:
        ch_service = ChannelService(session)
        config = ch_service.get_or_create_guild_config(str(target_guild.id))
        channel_id = config.tasks_channel_id
        if not channel_id:
            logger.info("Tasks channel not configured for guild %s; skipping reward celebration.", target_guild.id)
            return False

        channel = target_guild.get_channel(int(channel_id))
        if not channel or not isinstance(channel, discord.TextChannel):
            logger.warning("Tasks channel %s not found in guild %s", channel_id, target_guild.id)
            return False

        # Idempotency via PublishedMessage
        pub_rec = ch_service.get_published_message(
            guild_id=str(target_guild.id),
            feature_type="REWARD_CELEBRATION",
            source_id=str(submission.id),
        )
        if pub_rec:
            _SENT_CELEBRATION_DMS.add(sub_key)
            return False

        reward = submission.reward_amount or task.reward_per_user
        content, embed, view = build_in_server_celebration(
            task=task,
            submission=submission,
            reward_amount=reward,
        )

        try:
            msg = await channel.send(content=content, embed=embed, view=view)
            ch_service.record_published_message(
                guild_id=str(target_guild.id),
                feature_type="REWARD_CELEBRATION",
                channel_id=str(channel.id),
                message_id=str(msg.id),
                source_id=str(submission.id),
            )
            _SENT_CELEBRATION_DMS.add(sub_key)
            logger.info(
                "Posted in-server celebration for user %s in #%s (Msg ID: %s)",
                submission.discord_user_id, channel.name, msg.id,
            )
            return True
        except Exception as send_err:
            logger.warning("Could not post celebration message in #%s: %s", channel.name, send_err)
            return False
