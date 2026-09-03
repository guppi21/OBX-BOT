import uuid
import traceback
import discord
from discord.ui import Modal, TextInput, View, Button
from typing import Optional

from packages.database.session import session_scope
from apps.obx_tasks.services.task_service import TaskService
from apps.obx_core.services.wallet_service import WalletService
from apps.obx_tasks.bot.permissions import is_admin
from apps.obx_tasks.bot.ui_theme import COLOR_GREEN, COLOR_RED, COLOR_GOLD, COLOR_TEAL, BADGE_PENDING, BADGE_APPROVED, BADGE_REJECTED
from packages.shared.exceptions import TaskError, OBXError
from packages.shared.logging import get_logger

logger = get_logger("obx.tasks.bot.views")

_FRIENDLY_TASK_TYPES = {
    "LIKE": "Like Task",
    "RETWEET": "Repost Task",
    "REPOST": "Repost Task",
    "COMMENT": "Comment Task",
    "FOLLOW": "Follow Task",
    "JOIN_DISCORD": "Discord Task",
    "CUSTOM_TASK": "Custom Task",
    "MULTI_ACTION": "Multi-Action Task",
}


def format_task_display_name(task) -> str:
    """Return a human-readable, non-enum name for the task."""
    if not task:
        return "Custom Task"
    raw_type = (task.task_type or "").upper()
    title = (task.title or "").strip()
    if title and title.upper() not in (
        "CUSTOM_TASK",
        "LIKE",
        "RETWEET",
        "REPOST",
        "COMMENT",
        "FOLLOW",
        "JOIN_DISCORD",
        "TASK",
        "MULTI_ACTION",
    ):
        return title
    return _FRIENDLY_TASK_TYPES.get(raw_type, title.replace("_", " ").title() if title else "Custom Task")


class TaskSubmitModal(Modal, title="📎 SUBMIT PROOF"):
    def __init__(self, task_id: str, task_title: str):
        super().__init__()
        self.task_id = task_id
        self.task_title = task_title

        self.proof_url = TextInput(
            label="Proof Link",
            placeholder="Paste your proof link",
            required=True,
            max_length=1024,
        )
        self.add_item(self.proof_url)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        url = self.proof_url.value.strip()

        # Validate URL format
        if not (url.startswith("http://") or url.startswith("https://")) or "." not in url:
            await interaction.followup.send(
                "❌ Please provide a valid HTTP or HTTPS proof link (e.g. `https://x.com/username/status/123456789`).",
                ephemeral=True,
            )
            return

        try:
            with session_scope() as session:
                from apps.obx_tasks.services.raider_service import RaiderService
                r_service = RaiderService(session)
                raider = r_service.get_raider_profile(str(interaction.user.id))
                x_handle = raider.twitter_handle if raider else interaction.user.name

                service = TaskService(session)
                sub = service.submit_task(
                    task_id=self.task_id,
                    discord_user_id=str(interaction.user.id),
                    x_username=x_handle,
                    proof_url=url,
                    proof_text="Proof Link",
                )

                task = sub.task
                task_name = format_task_display_name(task)
                reward = sub.reward_amount or (task.reward_per_user if task else 0)

            embed = discord.Embed(
                title="📨 PROOF SUBMITTED",
                description=(
                    "Thank you for your submission!\n\n"
                    f"Estimated Reward: {reward:,} OBX\n\n"
                    "A moderator will review your proof.\n"
                    "You'll receive a DM when approved."
                ),
                color=COLOR_GREEN,
            )
            await interaction.followup.send(embed=embed, ephemeral=True)
        except (TaskError, ValueError) as exc:
            msg = exc.message if hasattr(exc, "message") else str(exc)
            await interaction.followup.send(f"❌ {msg}", ephemeral=True)
        except Exception as exc:
            logger.error("Error in TaskSubmitModal: %s", exc)
            await interaction.followup.send("❌ An unexpected error occurred while submitting proof.", ephemeral=True)


class RejectReasonModal(Modal, title="❌ REJECT SUBMISSION"):
    def __init__(self, submission_id: str, parent_view: Optional["TaskReviewView"] = None):
        super().__init__()
        self.submission_id = submission_id
        self.parent_view = parent_view

        self.reason = TextInput(
            label="Rejection Reason",
            placeholder="e.g. Invalid link, account does not match handle, tweet was deleted...",
            style=discord.TextStyle.paragraph,
            required=True,
            max_length=500,
        )
        self.add_item(self.reason)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        sub = None
        try:
            with session_scope() as session:
                service = TaskService(session)
                sub = service.reject_submission(
                    submission_id=self.submission_id,
                    reviewer_discord_id=str(interaction.user.id),
                    rejection_reason=self.reason.value,
                )
        except TaskError as exc:
            await interaction.followup.send(f"❌ {exc.message}", ephemeral=True)
            return
        except Exception as exc:
            logger.error("Error in RejectReasonModal: %s", exc)
            await interaction.followup.send("❌ Error rejecting submission.", ephemeral=True)
            return

        # Best-effort UI update to disable buttons
        if self.parent_view:
            try:
                self.parent_view.disable_all_items()
                if interaction.message:
                    await interaction.message.edit(view=self.parent_view)
            except Exception as ui_exc:
                logger.warning("Could not update review message view to disabled on rejection: %s", ui_exc)

        embed = discord.Embed(
            title="❌ Submission Rejected",
            description=(
                f"Submission for <@{sub.discord_user_id}> was rejected.\n"
                f"**Reason:** *{sub.rejection_reason}*"
            ),
            color=COLOR_RED,
        )
        await interaction.followup.send(embed=embed, ephemeral=True)


class MemberRewardView(View):
    def __init__(self):
        super().__init__(timeout=300)

    @discord.ui.button(label="View Wallet", style=discord.ButtonStyle.success)
    async def view_wallet_btn(self, interaction: discord.Interaction, button: Button):
        from apps.obx_tasks.bot.dashboard_views import handle_my_wallet
        await handle_my_wallet(interaction)

    @discord.ui.button(label="Browse Tasks", style=discord.ButtonStyle.primary)
    async def browse_tasks_btn(self, interaction: discord.Interaction, button: Button):
        from apps.obx_tasks.bot.dashboard_views import handle_browse_tasks
        await handle_browse_tasks(interaction)


class TaskReviewView(View):
    def __init__(self, submission_id: str, submitter_discord_id: str):
        super().__init__(timeout=300)
        self.submission_id = submission_id
        self.submitter_discord_id = submitter_discord_id

    def disable_all_items(self):
        """Safely disable all interactive buttons and components in this view."""
        for item in self.children:
            if hasattr(item, "disabled"):
                item.disabled = True

    @discord.ui.button(label="Approve & Distribute OBX", style=discord.ButtonStyle.success)
    async def approve_button(self, interaction: discord.Interaction, button: Button):
        if not is_admin(interaction):
            await interaction.response.send_message("❌ You do not have permission to review submissions.", ephemeral=True)
            return

        if str(interaction.user.id) == self.submitter_discord_id:
            await interaction.response.send_message("❌ Anti-Self-Approval Rule: You cannot approve your own submission.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)
        try:
            with session_scope() as session:
                service = TaskService(session)
                sub = service.approve_submission(
                    submission_id=self.submission_id,
                    reviewer_discord_id=str(interaction.user.id),
                )
                ws = WalletService(session)
                _, user_wallet, _ = ws.get_or_create_user(sub.discord_user_id)
                new_bal = user_wallet.available_balance if user_wallet else 0
                sub_id = str(sub.id)
                user_id = str(sub.discord_user_id)
                reward_amt = int(sub.reward_amount or 0)

            logger.info("[APPROVAL] New balance: %d OBX for user %s", new_bal, user_id)
        except (TaskError, OBXError) as exc:
            await interaction.followup.send(f"❌ {exc.message}", ephemeral=True)
            return
        except Exception as exc:
            logger.error("DB error approving submission %s: %s", self.submission_id, exc)
            await interaction.followup.send(f"❌ Approval failed: {str(exc)}", ephemeral=True)
            return

        # Best-effort UI update to disable buttons
        try:
            self.disable_all_items()
            if interaction.message:
                await interaction.message.edit(view=self)
        except Exception as ui_exc:
            logger.warning("Could not update review message view to disabled: %s", ui_exc)

        # Admin confirmation (minimal, no transaction IDs or database IDs)
        embed = discord.Embed(
            title="🎉 Submission Approved!",
            description=(
                f"Successfully approved submission for <@{user_id}>!\n"
                f"Awarded **{reward_amt:,} OBX** directly to their wallet."
            ),
            color=COLOR_GREEN,
        )
        await interaction.followup.send(embed=embed, ephemeral=True)

        # Best-effort DM to the user
        try:
            from apps.obx_tasks.bot.notification_service import send_approval_dm
            await send_approval_dm(
                bot=interaction.client,
                discord_user_id=user_id,
                approved_amount=reward_amt,
                new_balance=new_bal,
                submission_id=sub_id,
            )
        except Exception as notify_exc:
            logger.error("[DM] Could not send approval DM to user %s: %s\n%s", user_id, notify_exc, traceback.format_exc())

    @discord.ui.button(label="Reject", style=discord.ButtonStyle.danger)
    async def reject_button(self, interaction: discord.Interaction, button: Button):
        if not is_admin(interaction):
            await interaction.response.send_message("❌ You do not have permission to review submissions.", ephemeral=True)
            return

        modal = RejectReasonModal(submission_id=self.submission_id, parent_view=self)
        await interaction.response.send_modal(modal)
