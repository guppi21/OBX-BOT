import time
import inspect
import re
import traceback
from datetime import datetime, timezone, timedelta
import discord
from discord.ui import Modal, TextInput, View, Button, Select
from typing import Optional, List
from sqlalchemy import text, desc

from packages.shared.config import get_settings
from packages.shared.enums import TaskStatus, TaskType, SubmissionStatus
from packages.shared.exceptions import TaskError, OBXError
from packages.shared.logging import get_logger
from packages.database.session import session_scope, get_engine
from packages.database.models.ledger import LedgerEntry
from packages.database.models.user import User
from apps.obx_tasks.services.task_service import TaskService
from apps.obx_core.services.wallet_service import WalletService
from apps.obx_tasks.bot.permissions import is_admin
from apps.obx_tasks.bot.ui_theme import (
    COLOR_GOLD, COLOR_TEAL, COLOR_PURPLE, COLOR_GREEN, COLOR_RED, COLOR_BLUE, COLOR_DARK, COLOR_ORANGE,
    BADGE_PENDING, BADGE_APPROVED, BADGE_REJECTED, BADGE_ACTIVE, BADGE_PAUSED, BADGE_COMPLETED
)

logger = get_logger("obx.tasks.bot.dashboard")


def _is_response_done(interaction: discord.Interaction) -> bool:
    """Helper to safely check if an interaction response has been deferred or sent."""
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


def create_dashboard_embed() -> discord.Embed:
    embed = discord.Embed(
        title="🎯 OBX TASK CENTER",
        description=(
            "Welcome to the **OBX Task Center** — your home for social tasks & OBX rewards!\n\n"
            "Complete tasks, submit your proof, and earn **OBX** directly to your personal vault.\n"
            "Use the buttons below to get started."
        ),
        color=COLOR_GOLD,
    )
    embed.add_field(
        name="📋 What you can do here",
        value=(
            "• **Browse Tasks** — View active tasks, rewards, and submission instructions\n"
            "• **My Wallet** — Inspect your available OBX balance & ledger history\n"
            "• **My Submissions** — Track your pending and approved reward proofs\n"
            "• **Help Center** — Beginner guides, reward FAQs, and safety rules"
        ),
        inline=False,
    )
    embed.add_field(
        name="📡 Other Community Channels",
        value=(
            "• 🏆 **Leaderboard** — Check OBX rankings in <#1544530753440710768>\n"
            "• 🔨 **Auctions** — Bid on exclusive whitelist rewards in <#1544530705919246406>\n"
            "• 🏅 **Winners** — View whitelist winner announcements in <#1544530794373193748>"
        ),
        inline=False,
    )
    embed.set_footer(text="OBX Economy Engine • Double-Entry Ledger Protected • Integer-Precision Accounting")
    return embed


class TaskSubmitSuccessView(View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="My Submissions", style=discord.ButtonStyle.primary, custom_id="obx:user:submissions", row=0)
    async def my_submissions_btn(self, interaction: discord.Interaction, button: Button):
        await handle_my_submissions(interaction)

    @discord.ui.button(label="More Missions", style=discord.ButtonStyle.secondary, custom_id="obx:help:browse_missions", row=0)
    async def more_missions_btn(self, interaction: discord.Interaction, button: Button):
        await handle_browse_tasks(interaction)


def parse_reward_and_pool(val: str) -> Tuple[int, int]:
    val = val.strip()
    if "/" in val:
        parts = val.split("/")
    elif "," in val:
        parts = val.split(",")
    else:
        parts = val.split()
    if len(parts) >= 2:
        return int(parts[0].strip()), int(parts[1].strip())
    elif len(parts) == 1:
        amt = int(parts[0].strip())
        return amt, amt
    raise ValueError("Please provide Reward per user and Total pool (e.g. '15 / 150').")


def parse_duration_or_datetime(val: Optional[str]) -> Optional[datetime]:
    if not val or not val.strip():
        return None
    val = val.strip().lower()
    now = datetime.now(timezone.utc)
    # Check simple duration like 30m, 2h, 24h, 3d, 7d, 2w
    m = re.match(r"^(\d+)\s*(m|min|mins|minutes|h|hr|hrs|hours|d|day|days|w|weeks)$", val)
    if m:
        amount = int(m.group(1))
        unit = m.group(2)
        if unit.startswith("m") and not unit.startswith("min"):
            delta = timedelta(minutes=amount)
        elif unit.startswith("min"):
            delta = timedelta(minutes=amount)
        elif unit.startswith("h"):
            delta = timedelta(hours=amount)
        elif unit.startswith("d"):
            delta = timedelta(days=amount)
        elif unit.startswith("w"):
            delta = timedelta(weeks=amount)
        else:
            delta = timedelta(hours=amount)
        return now + delta

    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%Y/%m/%d %H:%M", "%d-%m-%Y %H:%M"):
        try:
            parsed = datetime.strptime(val, fmt)
            return parsed.replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    raise ValueError(f"Could not parse duration/deadline '{val}'. Valid examples: '24h', '3d', '2026-09-10 18:00', or leave blank.")


class AdminCreateTaskTypeSelectView(View):
    """Admin selection view to choose task type before opening creation modal."""
    def __init__(self):
        super().__init__(timeout=180)

    @discord.ui.button(label="Like Task", style=discord.ButtonStyle.primary, row=0)
    async def like_btn(self, interaction: discord.Interaction, button: Button):
        await interaction.response.send_modal(AdminCreateTaskModal(task_type="LIKE"))

    @discord.ui.button(label="Repost Task", style=discord.ButtonStyle.primary, row=0)
    async def repost_btn(self, interaction: discord.Interaction, button: Button):
        await interaction.response.send_modal(AdminCreateTaskModal(task_type="RETWEET"))

    @discord.ui.button(label="Comment Task", style=discord.ButtonStyle.primary, row=0)
    async def comment_btn(self, interaction: discord.Interaction, button: Button):
        await interaction.response.send_modal(AdminCreateTaskModal(task_type="COMMENT"))

    @discord.ui.button(label="Follow Task", style=discord.ButtonStyle.primary, row=1)
    async def follow_btn(self, interaction: discord.Interaction, button: Button):
        await interaction.response.send_modal(AdminCreateTaskModal(task_type="FOLLOW"))

    @discord.ui.button(label="Discord Task", style=discord.ButtonStyle.primary, row=1)
    async def discord_btn(self, interaction: discord.Interaction, button: Button):
        await interaction.response.send_modal(AdminCreateTaskModal(task_type="JOIN_DISCORD"))

    @discord.ui.button(label="Custom Task", style=discord.ButtonStyle.secondary, row=1)
    async def custom_btn(self, interaction: discord.Interaction, button: Button):
        await interaction.response.send_modal(AdminCreateTaskModal(task_type="CUSTOM_TASK"))

    @discord.ui.button(label="Multi-Action", style=discord.ButtonStyle.success, row=2)
    async def multi_btn(self, interaction: discord.Interaction, button: Button):
        embed = discord.Embed(
            title="Select Required Mission Actions",
            description=(
                "Choose the required actions for this multi-step mission.\n"
                "Members will be required to fulfill each selected objective:"
            ),
            color=COLOR_GOLD,
        )
        view = AdminSelectMultiActionsView()
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)


class AdminSelectMultiActionsView(View):
    """View allowing admin to select multiple required actions for a Multi-Action task."""
    def __init__(self):
        super().__init__(timeout=180)
        self.selected_actions = ["LIKE", "RETWEET"]
        self._build_components()

    def _build_components(self):
        self.clear_items()
        options = [
            discord.SelectOption(label="Like", value="LIKE", default=("LIKE" in self.selected_actions)),
            discord.SelectOption(label="Repost / Retweet", value="RETWEET", default=("RETWEET" in self.selected_actions)),
            discord.SelectOption(label="Comment", value="COMMENT", default=("COMMENT" in self.selected_actions)),
            discord.SelectOption(label="Follow Account", value="FOLLOW", default=("FOLLOW" in self.selected_actions)),
            discord.SelectOption(label="Join Discord", value="JOIN_DISCORD", default=("JOIN_DISCORD" in self.selected_actions)),
        ]
        select_menu = Select(
            placeholder="Select required actions...",
            min_values=1,
            max_values=len(options),
            options=options,
            row=0,
        )
        select_menu.callback = self._on_select_actions
        self.add_item(select_menu)

        btn_continue = Button(
            label="Continue to Task Details",
            style=discord.ButtonStyle.success,
            row=1,
        )
        btn_continue.callback = self._on_continue
        self.add_item(btn_continue)

    async def _on_select_actions(self, interaction: discord.Interaction):
        self.selected_actions = interaction.data.get("values", ["LIKE", "RETWEET"])
        self._build_components()
        await interaction.response.edit_message(view=self)

    async def _on_continue(self, interaction: discord.Interaction):
        req_actions_str = ",".join(self.selected_actions)
        modal = AdminCreateTaskModal(task_type="MULTI_ACTION", required_actions=req_actions_str)
        await interaction.response.send_modal(modal)


class AdminCreateTaskModal(Modal, title="⚡ CREATE TASK"):
    def __init__(self, task_type: str = "LIKE", required_actions: Optional[str] = None):
        super().__init__()
        self.task_type = task_type.upper()
        self.required_actions = required_actions

        placeholders = {
            "LIKE": ("Like Target Post", "Like the target post on X and click Verify Completion."),
            "RETWEET": ("Repost Target Post", "Repost the announcement on X and submit proof if requested."),
            "COMMENT": ("Comment on Post", "Comment on the target post with #OBX and submit your link."),
            "FOLLOW": ("Follow Account", "Follow @account on X and submit your profile link."),
            "JOIN_DISCORD": ("Join Discord", "Join the community Discord server and verify membership."),
            "CUSTOM_TASK": ("Community Task", "Complete the instructions below and submit proof."),
            "MULTI_ACTION": ("Multi-Action Raid", "Complete the required actions and submit proof."),
        }
        self.auto_title, def_instr = placeholders.get(self.task_type, (f"{self.task_type.capitalize()} Task", "Complete the task instructions."))

        self.target_url = TextInput(
            label="Target Link / Post URL",
            placeholder="https://x.com/obx/status/123456789",
            required=True,
            max_length=1024,
        )
        self.reward_and_pool = TextInput(
            label="Reward / Pool (OBX)",
            placeholder="e.g. 15 / 150 (Reward per user / Total pool)",
            required=True,
            max_length=32,
        )
        self.deadline = TextInput(
            label="Duration / Deadline (Optional UTC)",
            placeholder="e.g. 24h, 3d, 2026-09-10 18:00 (UTC), or blank",
            required=False,
            max_length=32,
        )
        self.instructions = TextInput(
            label="Task Instructions",
            default=def_instr,
            placeholder=def_instr,
            style=discord.TextStyle.paragraph,
            required=True,
            max_length=1000,
        )

        self.add_item(self.target_url)
        self.add_item(self.reward_and_pool)
        self.add_item(self.deadline)
        self.add_item(self.instructions)

    async def on_submit(self, interaction: discord.Interaction):
        if not is_admin(interaction):
            await interaction.response.send_message("❌ Permission Denied: You do not have the required administrator role.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)

        try:
            r_user, r_pool = parse_reward_and_pool(self.reward_and_pool.value)
            ends_at = parse_duration_or_datetime(self.deadline.value)
        except ValueError as val_err:
            await interaction.followup.send(f"❌ Input Error: {str(val_err)}", ephemeral=True)
            return

        platform_val = "Discord" if self.task_type == "JOIN_DISCORD" else "X"
        url_lower = self.target_url.value.strip().lower()
        if "discord" in url_lower:
            platform_val = "Discord"
        elif "x.com" in url_lower or "twitter.com" in url_lower:
            platform_val = "X"

        try:
            with session_scope() as session:
                service = TaskService(session)
                task = service.create_task(
                    title=self.auto_title,
                    description=self.instructions.value.strip(),
                    task_type=self.task_type,
                    target_url=self.target_url.value.strip(),
                    reward_per_user=r_user,
                    total_reward_pool=r_pool,
                    created_by=str(interaction.user.id),
                    platform=platform_val,
                    status=TaskStatus.ACTIVE,
                    ends_at=ends_at,
                    required_actions=self.required_actions,
                )

            # Auto-announce task card to configured Tasks channel
            ann_result_msg = ""
            try:
                if interaction.guild:
                    from apps.obx_tasks.bot.announcement_service import announce_task
                    ok_ann, msg_ann = await announce_task(task, interaction.guild, interaction.client)
                    ann_result_msg = f"\n\n📢 **Live Feed:** {msg_ann}"
            except Exception as ann_err:
                logger.warning("Auto-announcement for task %s failed: %s", task.id, ann_err)
                ann_result_msg = f"\n\n⚠️ **Live Feed Announcement Failed:** `{str(ann_err)}`"

            deadline_str = f"⏳ **Ends <t:{int(ends_at.timestamp())}:R>**" if ends_at else "♾️ **No Deadline**"

            embed = discord.Embed(
                title="🎉 Task Created Successfully!",
                description=f"Task **{task.title}** is now live and open for member submissions.{ann_result_msg}",
                color=COLOR_GREEN,
            )
            embed.add_field(name="Reward Per User", value=f"💎 **{task.reward_per_user:,} OBX**", inline=True)
            embed.add_field(name="Total Pool", value=f"🪙 `{task.total_reward_pool:,} OBX`", inline=True)
            embed.add_field(name="Max Approvals", value=f"`{task.max_approvals}`", inline=True)
            embed.add_field(name="Deadline (UTC)", value=deadline_str, inline=True)
            embed.add_field(name="Target URL", value=f"[Open Target Link]({task.target_url})", inline=False)
            embed.set_footer(text="Task is immediately active in the member task browser and live feed.")

            view = View()
            view.add_item(Button(label="Open Target Post", url=task.target_url))
            await interaction.followup.send(embed=embed, view=view, ephemeral=True)
        except TaskError as exc:
            await interaction.followup.send(f"❌ {exc.message}", ephemeral=True)
        except Exception as exc:
            logger.error("Error in AdminCreateTaskModal: %s", exc)
            await interaction.followup.send(f"❌ Error creating task: {str(exc)}", ephemeral=True)


class TaskBrowserView(View):
    def __init__(self, tasks: list, current_index: int = 0):
        super().__init__(timeout=300)
        self.tasks = tasks
        self.current_index = current_index
        self.update_buttons()

    def update_buttons(self):
        self.prev_btn.disabled = self.current_index <= 0
        self.next_btn.disabled = self.current_index >= len(self.tasks) - 1

    def get_current_embed(self) -> discord.Embed:
        task = self.tasks[self.current_index]
        embed = discord.Embed(
            title=f"🎯 {task.title}",
            description=task.description,
            color=COLOR_PURPLE,
        )
        embed.add_field(name="Reward Per User", value=f"💎 **{task.reward_per_user:,} OBX**", inline=True)
        embed.add_field(name="Remaining Pool", value=f"🪙 `{task.remaining_reward_pool:,} / {task.total_reward_pool:,} OBX`", inline=True)
        embed.add_field(name="Platform & Type", value=f"`{task.platform}` • `{task.task_type.value}`", inline=True)
        embed.add_field(name="Target Link", value=f"[Click to Open Post]({task.target_url})", inline=False)
        embed.set_footer(text=f"Task {self.current_index + 1} of {len(self.tasks)} • Click 'Submit Proof' to earn OBX!")
        return embed

    @discord.ui.button(label="Previous", style=discord.ButtonStyle.secondary, row=0)
    async def prev_btn(self, interaction: discord.Interaction, button: Button):
        if self.current_index > 0:
            self.current_index -= 1
            self.update_buttons()
            await interaction.response.edit_message(embed=self.get_current_embed(), view=self)

    @discord.ui.button(label="Submit Proof", style=discord.ButtonStyle.success, row=0)
    async def submit_btn(self, interaction: discord.Interaction, button: Button):
        from apps.obx_tasks.bot.views import TaskSubmitModal
        task = self.tasks[self.current_index]
        modal = TaskSubmitModal(task_id=str(task.id), task_title=task.title)
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="Next", style=discord.ButtonStyle.secondary, row=0)
    async def next_btn(self, interaction: discord.Interaction, button: Button):
        if self.current_index < len(self.tasks) - 1:
            self.current_index += 1
            self.update_buttons()
            await interaction.response.edit_message(embed=self.get_current_embed(), view=self)

    @discord.ui.button(label="Refresh", style=discord.ButtonStyle.secondary, row=1)
    async def refresh_btn(self, interaction: discord.Interaction, button: Button):
        await handle_browse_tasks(interaction)

    @discord.ui.button(label="Home", style=discord.ButtonStyle.secondary, row=1)
    async def home_btn(self, interaction: discord.Interaction, button: Button):
        await handle_home(interaction)


class WalletView(View):
    def __init__(self, user_discord_id: str):
        super().__init__(timeout=180)
        self.user_discord_id = user_discord_id

    @discord.ui.button(label="Earn More OBX", style=discord.ButtonStyle.success)
    async def earn_more_btn(self, interaction: discord.Interaction, button: Button):
        await handle_browse_tasks(interaction)

    @discord.ui.button(label="Refresh Balance", style=discord.ButtonStyle.secondary)
    async def refresh_btn(self, interaction: discord.Interaction, button: Button):
        await handle_my_wallet(interaction)

    @discord.ui.button(label="Home", style=discord.ButtonStyle.secondary)
    async def home_btn(self, interaction: discord.Interaction, button: Button):
        await handle_home(interaction)


class MySubmissionsView(View):
    def __init__(self):
        super().__init__(timeout=180)

    @discord.ui.button(label="Browse Active Tasks", style=discord.ButtonStyle.primary)
    async def browse_btn(self, interaction: discord.Interaction, button: Button):
        await handle_browse_tasks(interaction)

    @discord.ui.button(label="My Wallet", style=discord.ButtonStyle.secondary)
    async def wallet_btn(self, interaction: discord.Interaction, button: Button):
        await handle_my_wallet(interaction)

    @discord.ui.button(label="Home", style=discord.ButtonStyle.secondary)
    async def home_btn(self, interaction: discord.Interaction, button: Button):
        await handle_home(interaction)


class HelpCenterView(View):
    def __init__(self):
        super().__init__(timeout=300)

    @discord.ui.button(label="Getting Started", style=discord.ButtonStyle.primary, row=0)
    async def getting_started_btn(self, interaction: discord.Interaction, button: Button):
        embed = discord.Embed(
            title="🚀 Getting Started with OBX",
            description=(
                "**Welcome to the OBX Economy Platform!**\n\n"
                "1. Click **Browse Tasks** to view all active social reward tasks.\n"
                "2. Click **Submit Proof** on any task to submit your X (Twitter) handle and link.\n"
                "3. Once verified by an administrator, **OBX tokens** are instantly credited to your wallet!\n"
                "4. Check your balance anytime using **My Wallet**."
            ),
            color=COLOR_BLUE,
        )
        embed.set_footer(text="OBX Economy • All rewards are integer-based and double-entry backed")
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="Tasks & Rewards", style=discord.ButtonStyle.primary, row=0)
    async def tasks_rewards_btn(self, interaction: discord.Interaction, button: Button):
        embed = discord.Embed(
            title="🎯 Tasks & Reward Pools",
            description=(
                "**How OBX Reward Pools Work:**\n\n"
                "• **Reward Pools**: Each task has a fixed total reward pool (e.g. 1,000 OBX).\n"
                "• **Reward Rate**: Each approved user receives the configured reward amount.\n"
                "• **First-Come, First-Served**: Submissions are reviewed in order until the pool is exhausted.\n"
                "• **One Reward Per User**: Duplicate submissions for the same task are automatically rejected."
            ),
            color=COLOR_PURPLE,
        )
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="Submissions Guide", style=discord.ButtonStyle.primary, row=0)
    async def submissions_guide_btn(self, interaction: discord.Interaction, button: Button):
        embed = discord.Embed(
            title="📨 Submissions & Verification Guide",
            description=(
                "**How to Submit Valid Proof:**\n\n"
                "• **X Handle**: Enter your public handle (e.g. `satoshi_nakamoto`).\n"
                "• **Proof Link**: Provide the exact URL to your retweet, reply, or quote.\n"
                "• **Public Accounts**: Ensure your X account is public so reviewers can verify your post.\n"
                "• **Status Tracking**: Track your pending and approved proofs via **My Submissions**."
            ),
            color=COLOR_TEAL,
        )
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="Wallet & Balances", style=discord.ButtonStyle.primary, row=1)
    async def wallet_guide_btn(self, interaction: discord.Interaction, button: Button):
        embed = discord.Embed(
            title="💰 Wallet & Balance Types",
            description=(
                "**Understanding Your OBX Balance:**\n\n"
                "• **Available Balance**: Tokens ready to be used or transferred.\n"
                "• **Locked Balance**: Tokens committed in pending auctions or actions.\n"
                "• **Total Balance**: Available Balance + Locked Balance.\n"
                "• **Double-Entry Ledger**: Every single credit or debit is permanently audited."
            ),
            color=COLOR_GOLD,
        )
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="Leaderboard Guide", style=discord.ButtonStyle.secondary, row=1)
    async def leaderboards_btn(self, interaction: discord.Interaction, button: Button):
        embed = discord.Embed(
            title="🏆 OBX Community Leaderboard",
            description=(
                "**The OBX Leaderboard is live!**\n\n"
                "Head to <#1544530753440710768> to check your ranking and compete with others.\n\n"
                "**How Rankings Work:**\n"
                "• Raiders are ranked by current OBX balance\n"
                "• Your balance and rank always appear at the top\n"
                "• Rankings update live every time submissions are approved\n"
                "• Ties are broken by earliest activity"
            ),
            color=COLOR_GOLD,
        )
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="Auctions Guide", style=discord.ButtonStyle.secondary, row=1)
    async def auctions_btn(self, interaction: discord.Interaction, button: Button):
        embed = discord.Embed(
            title="🔨 OBX Whitelist Rewards & Auction Guide",
            description=(
                "**How Whitelist Rewards Work in OBX:**\n\n"
                "• **⚡ FCFS Whitelist Sales**: Fixed OBX price. Instant atomic claim with zero overselling.\n"
                "• **🏆 GTD Allocation Auctions**: Top bids win guaranteed whitelist spots (Pay-As-Bid).\n"
                "• **🔒 Safe Fund Locking**: Bidding locks funds from available balance; losing bids are automatically refunded upon auction completion.\n"
                "• **🛡 Double-Entry Security**: All transactions are verified by the ledger and cannot double charge."
            ),
            color=COLOR_GOLD,
        )
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="Safety & Rules", style=discord.ButtonStyle.secondary, row=2)
    async def safety_btn(self, interaction: discord.Interaction, button: Button):
        embed = discord.Embed(
            title="🛡️ Safety, Security & Anti-Abuse",
            description=(
                "**Platform Security Guarantees:**\n\n"
                "• **Anti-Self-Approval**: Administrators cannot approve their own submissions.\n"
                "• **Immutable Records**: Approved reward amounts are permanently recorded and cannot be altered.\n"
                "• **Pool Overspend Prevention**: Automated locking prevents pools from overdrawing.\n"
                "• **Zero Secret Leakage**: Credentials and tokens are strictly isolated."
            ),
            color=COLOR_GREEN,
        )
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="Home", style=discord.ButtonStyle.success, row=2)
    async def home_btn(self, interaction: discord.Interaction, button: Button):
        await handle_home(interaction)


def create_admin_hub_embed() -> discord.Embed:
    embed = discord.Embed(
        title="🔒 OBX ADMINISTRATIVE CONTROL CENTER",
        description=(
            "**Private Operations & Command Hub**\n"
            "Manage ecosystem tasks, whitelist auctions, custom rewards, channel routing, and verify platform health."
        ),
        color=COLOR_RED,
    )
    embed.add_field(
        name="🛠️ Task & Reward Management",
        value=(
            "• **➕ Create Task**: Deploy new community tasks\n"
            "• **📝 Manage Tasks**: Edit content, economics, cancel, or delete tasks\n"
            "• **📋 Review Queue**: Approve/reject user submissions\n"
            "• **🎁 Grant Reward**: Manually credit OBX rewards"
        ),
        inline=False,
    )
    embed.add_field(
        name="🔨 Auctions & Whitelists",
        value="• **🔨 Create Auction**: Launch FCFS whitelist sales or ranked GTD auctions",
        inline=False,
    )
    embed.add_field(
        name="👥 Raiders & Community",
        value="• **👥 Raiders**: Inspect member roster, connected X accounts, balances, and ranks",
        inline=False,
    )
    embed.add_field(
        name="⚙️ Platform & Routing Operations",
        value="• **🏗️ Configure Channels**: Reassign feature channels\n• **📊 Refresh Public Systems**: Update all public cards\n• **🩺 System Health**: Telemetry & database diagnostic",
        inline=False,
    )
    embed.set_footer(text="Strictly Restricted: Haveli Owner Admin Role Only • Authoritative Ledger")
    return embed


class OBXAdminHubView(View):
    """Persistent Administrator Hub View deployed exclusively in the private admin operations channel."""
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Create Task", style=discord.ButtonStyle.primary, custom_id="obx:admin:create_task", row=0)
    async def create_task_btn(self, interaction: discord.Interaction, button: Button):
        if not is_admin(interaction):
            await interaction.response.send_message("❌ Permission Denied: Administrator role required.", ephemeral=True)
            return
        embed = discord.Embed(
            title="➕ Select Task Type",
            description=(
                "Choose the task type to create.\n"
                "The announcement headline, visuals, and verification steps will match your selection:"
            ),
            color=COLOR_GOLD,
        )
        embed.add_field(name="❤️ Like", value="Like a target post on X", inline=True)
        embed.add_field(name="🔁 Repost", value="Repost/Retweet a post on X", inline=True)
        embed.add_field(name="💬 Comment", value="Comment or reply on X", inline=True)
        embed.add_field(name="👥 Follow", value="Follow an account on X", inline=True)
        embed.add_field(name="📣 Discord", value="Join or participate on Discord", inline=True)
        embed.add_field(name="📝 Custom", value="Custom community raid or task", inline=True)
        view = AdminCreateTaskTypeSelectView()
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

    @discord.ui.button(label="Manage Tasks", style=discord.ButtonStyle.primary, custom_id="obx:admin:manage_tasks", row=0)
    async def manage_tasks_btn(self, interaction: discord.Interaction, button: Button):
        if not is_admin(interaction):
            await interaction.response.send_message("❌ Permission Denied: Administrator role required.", ephemeral=True)
            return
        from apps.obx_tasks.bot.task_management_views import handle_admin_manage_tasks
        await handle_admin_manage_tasks(interaction)

    @discord.ui.button(label="Review Queue", style=discord.ButtonStyle.danger, custom_id="obx:admin:review_queue", row=0)
    async def review_queue_btn(self, interaction: discord.Interaction, button: Button):
        if not is_admin(interaction):
            await interaction.response.send_message("❌ Permission Denied: Administrator role required.", ephemeral=True)
            return
        await handle_admin_review(interaction)

    @discord.ui.button(label="Create Auction", style=discord.ButtonStyle.primary, custom_id="obx:admin:create_auction", row=0)
    async def create_auction_btn(self, interaction: discord.Interaction, button: Button):
        if not is_admin(interaction):
            await interaction.response.send_message("❌ Permission Denied: Administrator role required.", ephemeral=True)
            return
        from apps.obx_tasks.bot.auction_views import AdminCreateAuctionModal
        modal = AdminCreateAuctionModal()
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="Raiders", style=discord.ButtonStyle.primary, custom_id="obx:admin:members", row=1)
    async def members_btn(self, interaction: discord.Interaction, button: Button):
        if not is_admin(interaction):
            await interaction.response.send_message("❌ Permission Denied: Administrator role required.", ephemeral=True)
            return
        from apps.obx_tasks.bot.admin_members_views import handle_admin_members
        await handle_admin_members(interaction)

    @discord.ui.button(label="Grant Reward", style=discord.ButtonStyle.success, custom_id="obx:admin:grant_reward", row=1)
    async def grant_reward_btn(self, interaction: discord.Interaction, button: Button):
        if not is_admin(interaction):
            await interaction.response.send_message("❌ Permission Denied: Administrator role required.", ephemeral=True)
            return
        from apps.obx_tasks.bot.auction_views import AdminGrantRewardModal
        modal = AdminGrantRewardModal()
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="Configure Channels", style=discord.ButtonStyle.primary, custom_id="obx:admin:configure_channels", row=1)
    async def configure_channels_btn(self, interaction: discord.Interaction, button: Button):
        if not is_admin(interaction):
            await interaction.response.send_message("❌ Permission Denied: Administrator role required.", ephemeral=True)
            return
        from apps.obx_tasks.bot.channel_views import handle_channel_config
        await handle_channel_config(interaction)

    @discord.ui.button(label="Refresh Public", style=discord.ButtonStyle.secondary, custom_id="obx:admin:refresh_public", row=1)
    async def refresh_public_btn(self, interaction: discord.Interaction, button: Button):
        if not is_admin(interaction):
            await interaction.response.send_message("❌ Permission Denied: Administrator role required.", ephemeral=True)
            return
        await handle_refresh_public_systems(interaction)

    @discord.ui.button(label="System Health", style=discord.ButtonStyle.secondary, custom_id="obx:admin:system_health", row=2)
    async def health_btn(self, interaction: discord.Interaction, button: Button):
        if not is_admin(interaction):
            await interaction.response.send_message("❌ Permission Denied: Administrator role required.", ephemeral=True)
            return
        await handle_admin_health(interaction)

    @discord.ui.button(label="Refresh Hub", style=discord.ButtonStyle.secondary, custom_id="obx:admin:refresh_hub", row=2)
    async def refresh_hub_btn(self, interaction: discord.Interaction, button: Button):
        if not is_admin(interaction):
            await interaction.response.send_message("❌ Permission Denied: Administrator role required.", ephemeral=True)
            return
        from apps.obx_tasks.bot.announcement_service import deploy_or_update_admin_hub
        await deploy_or_update_admin_hub(interaction.guild, interaction.client)
        if hasattr(interaction, "response") and not _is_response_done(interaction):
            await interaction.response.send_message("✅ Admin Hub refreshed in-place.", ephemeral=True)


AdminPanelView = OBXAdminHubView


class OBXDashboardView(View):
    def __init__(self):
        super().__init__(timeout=None)  # Persistent view across restarts!

    # Row 0: Member Action Buttons (task-center scoped only)
    @discord.ui.button(
        label="Browse Tasks",
        style=discord.ButtonStyle.primary,
        custom_id="obx:dashboard:browse_tasks",
        row=0,
    )
    async def browse_tasks_button(self, interaction: discord.Interaction, button: Button):
        await handle_browse_tasks(interaction)

    @discord.ui.button(
        label="My Wallet",
        style=discord.ButtonStyle.success,
        custom_id="obx:dashboard:my_balance",
        row=0,
    )
    async def my_balance_button(self, interaction: discord.Interaction, button: Button):
        await handle_my_wallet(interaction)

    @discord.ui.button(
        label="My Submissions",
        style=discord.ButtonStyle.secondary,
        custom_id="obx:dashboard:my_submissions",
        row=0,
    )
    async def my_submissions_button(self, interaction: discord.Interaction, button: Button):
        await handle_my_submissions(interaction)

    @discord.ui.button(
        label="Help Center",
        style=discord.ButtonStyle.secondary,
        custom_id="obx:dashboard:help_center",
        row=0,
    )
    async def help_center_button(self, interaction: discord.Interaction, button: Button):
        await handle_help_center(interaction)


# Core Navigation Handlers
async def handle_home(interaction: discord.Interaction):
    embed = create_dashboard_embed()
    view = OBXDashboardView()
    if _is_response_done(interaction):
        await interaction.followup.send(embed=embed, view=view, ephemeral=True)
    else:
        try:
            await interaction.response.edit_message(embed=embed, view=view)
        except Exception:
            await interaction.response.send_message(embed=embed, view=view, ephemeral=True)


async def handle_browse_tasks(interaction: discord.Interaction):
    from apps.obx_tasks.bot.permissions import check_raider_access
    if not await check_raider_access(interaction):
        return

    if not _is_response_done(interaction):
        await interaction.response.defer(ephemeral=True)

    try:
        with session_scope() as session:
            service = TaskService(session)
            tasks, total = service.list_tasks(status=TaskStatus.ACTIVE, limit=25)

            if not tasks:
                embed = discord.Embed(
                    title="📋 Active OBX Tasks",
                    description="There are currently no active tasks. Check back soon!",
                    color=COLOR_BLUE,
                )
                view = View()
                view.add_item(Button(label="Home", style=discord.ButtonStyle.secondary, custom_id="obx:home:return"))
                await interaction.followup.send(embed=embed, view=view, ephemeral=True)
                return

            browser = TaskBrowserView(tasks=tasks, current_index=0)
            await interaction.followup.send(embed=browser.get_current_embed(), view=browser, ephemeral=True)
    except Exception as exc:
        logger.error("Error in handle_browse_tasks: %s", exc)
        await interaction.followup.send("❌ Error fetching active tasks.", ephemeral=True)


async def handle_my_wallet(interaction: discord.Interaction):
    from apps.obx_tasks.bot.permissions import check_raider_access
    if not await check_raider_access(interaction):
        return

    if not _is_response_done(interaction):
        await interaction.response.defer(ephemeral=True)

    try:
        with session_scope() as session:
            ws = WalletService(session)
            user, wallet, _ = ws.get_or_create_user(str(interaction.user.id))

            # Fetch recent ledger entries
            entries = (
                session.query(LedgerEntry)
                .filter(LedgerEntry.user_id == user.id)
                .order_by(desc(LedgerEntry.created_at))
                .limit(4)
                .all()
            )

            embed = discord.Embed(
                title="💰 Your OBX Wallet",
                description=f"Personal balance and accounting breakdown for <@{interaction.user.id}>:",
                color=COLOR_GOLD,
            )
            embed.add_field(name="💎 Available Balance", value=f"**{wallet.available_balance:,} OBX**", inline=True)
            embed.add_field(name="🔒 Locked Balance", value=f"`{wallet.locked_balance:,} OBX`", inline=True)
            embed.add_field(name="🪙 Total Balance", value=f"**{wallet.total_balance:,} OBX**", inline=True)

            if entries:
                activity_lines = []
                for e in entries:
                    sign = "+" if e.transaction_type.value == "CREDIT" else "-"
                    activity_lines.append(f"• `{sign}{e.amount:,} OBX` ({e.reference_type}) — <t:{int(e.created_at.timestamp())}:R>")
                embed.add_field(name="📜 Recent Ledger Activity", value="\n".join(activity_lines), inline=False)
            else:
                embed.add_field(name="📜 Recent Ledger Activity", value="*No transactions yet. Complete tasks to earn OBX!*", inline=False)

            embed.set_footer(text="Backed by double-entry ledger • Zero fractional slippage")
            view = WalletView(user_discord_id=str(interaction.user.id))
            await interaction.followup.send(embed=embed, view=view, ephemeral=True)
    except Exception as exc:
        logger.error("Error in handle_my_wallet: %s", exc)
        await interaction.followup.send("❌ Error fetching wallet balance.", ephemeral=True)


async def handle_my_submissions(interaction: discord.Interaction):
    from apps.obx_tasks.bot.permissions import check_raider_access
    if not await check_raider_access(interaction):
        return
    if not _is_response_done(interaction):
        await interaction.response.defer(ephemeral=True)

    try:
        with session_scope() as session:
            service = TaskService(session)
            submissions, total = service.list_submissions(
                discord_user_id=str(interaction.user.id),
                limit=6,
            )

            if not submissions:
                embed = discord.Embed(
                    title="📜 Your Task Submissions",
                    description="You haven't submitted any task proofs yet. Browse active tasks and start earning!",
                    color=COLOR_TEAL,
                )
                view = MySubmissionsView()
                await interaction.followup.send(embed=embed, view=view, ephemeral=True)
                return

            embed = discord.Embed(
                title="📜 Your Task Submissions & Activity",
                description=f"Showing recent {len(submissions)} submission(s) out of {total}:",
                color=COLOR_TEAL,
            )
            for s in submissions:
                badge = BADGE_PENDING if s.status == SubmissionStatus.PENDING else (BADGE_APPROVED if s.status == SubmissionStatus.APPROVED else BADGE_REJECTED)
                reward_str = f" • 💎 `{s.reward_amount:,} OBX`" if s.reward_amount else ""
                reason_str = f"\n*Reason:* {s.rejection_reason}" if s.rejection_reason else ""
                embed.add_field(
                    name=f"{badge} — {s.task.title}",
                    value=(
                        f"**Proof Link:** [Open Proof]({s.proof_url}){reward_str}{reason_str}\n"
                        f"**Submitted:** <t:{int(s.submitted_at.timestamp())}:R>"
                    ),
                    inline=False,
                )

            view = MySubmissionsView()
            await interaction.followup.send(embed=embed, view=view, ephemeral=True)
    except Exception as exc:
        logger.error("Error in handle_my_submissions: %s", exc)
        await interaction.followup.send("❌ Error fetching submissions.", ephemeral=True)


async def handle_help_center(interaction: discord.Interaction):
    embed = discord.Embed(
        title="❓ OBX Knowledge Base & Help Center",
        description=(
            "Welcome to the **OBX Help Center**!\n"
            "Select a topic below to learn how tasks, rewards, and wallets work."
        ),
        color=COLOR_BLUE,
    )
    embed.add_field(
        name="📚 Available Topics",
        value=(
            "• **🚀 Getting Started**: Beginner walkthrough\n"
            "• **🎯 Tasks & Rewards**: How task pools and rewards work\n"
            "• **📨 Submissions Guide**: How to submit valid social proof\n"
            "• **💰 Wallet & Balances**: Available vs Locked OBX\n"
            "• **🛡️ Safety & Rules**: Security guarantees and anti-abuse policies\n"
            "• **🏆 Leaderboard Guide**: How OBX rankings work (see <#1544530753440710768>)\n"
            "• **🔨 Auctions Guide**: How FCFS & GTD whitelist auctions work (see <#1544530705919246406>)"
        ),
        inline=False,
    )
    view = HelpCenterView()
    if _is_response_done(interaction):
        await interaction.followup.send(embed=embed, view=view, ephemeral=True)
    else:
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)


class AdminReviewQueueView(View):
    def __init__(self, submissions: list, current_index: int = 0):
        super().__init__(timeout=300)
        self.submissions = submissions
        self.current_index = current_index
        self.update_buttons()

    def update_buttons(self):
        n = len(self.submissions)
        self.prev_btn.disabled = (n <= 1 or self.current_index <= 0)
        self.next_btn.disabled = (n <= 1 or self.current_index >= n - 1)
        self.skip_btn.disabled = (n <= 1)
        self.approve_btn.disabled = (n == 0)
        self.reject_btn.disabled = (n == 0)

    def get_current_embed(self) -> discord.Embed:
        if not self.submissions:
            embed = discord.Embed(
                title="🎉 Review Queue Caught Up!",
                description="🎉 **All caught up — no pending submissions awaiting review.**",
                color=COLOR_GREEN,
            )
            embed.set_footer(text="OBX Administrative Control Center")
            return embed

        sub = self.submissions[self.current_index]
        embed = discord.Embed(
            title=f"🔍 Review Queue: {sub.task.title}",
            description=f"**Submission {self.current_index + 1} of {len(self.submissions)}** (Pending Review Queue)\n`ID: {sub.id}`",
            color=COLOR_GOLD,
        )
        embed.add_field(name="👤 Submitting Member", value=f"<@{sub.discord_user_id}> (`@{sub.x_username}`)", inline=True)
        embed.add_field(name="💎 Expected Reward", value=f"**{sub.task.reward_per_user:,} OBX**", inline=True)
        embed.add_field(name="🕒 Submitted", value=f"<t:{int(sub.submitted_at.timestamp())}:R>", inline=True)

        # Task Deadline info
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc)
        if sub.task.ends_at:
            is_expired = now > (sub.task.ends_at if sub.task.ends_at.tzinfo else sub.task.ends_at.replace(tzinfo=timezone.utc))
            if is_expired:
                embed.add_field(name="⏳ Task Deadline", value=f"🔴 **EXPIRED** (<t:{int(sub.task.ends_at.timestamp())}:R>)", inline=True)
            else:
                embed.add_field(name="⏳ Task Deadline", value=f"⏳ **Ends <t:{int(sub.task.ends_at.timestamp())}:R>**", inline=True)
        else:
            embed.add_field(name="⏳ Task Deadline", value="♾️ **No Deadline**", inline=True)

        embed.add_field(name="🔗 Proof Link", value=f"[Open Submitted Proof Link]({sub.proof_url})", inline=False)
        embed.add_field(name="📝 Proof Context / Explanation", value=sub.proof_text or "*No explanation provided.*", inline=False)

        if sub.proof_screenshot_url:
            if sub.proof_screenshot_url.startswith("http://") or sub.proof_screenshot_url.startswith("https://"):
                embed.set_image(url=sub.proof_screenshot_url)
                embed.add_field(name="📸 Proof Image", value=f"[View Image]({sub.proof_screenshot_url})", inline=False)
            else:
                embed.add_field(name="📸 Proof Image", value=f"`{sub.proof_screenshot_url}`", inline=False)

        embed.set_footer(text="OBX Admin Review Queue • Double-Entry Vault Approval")
        return embed

    @discord.ui.button(label="Approve", style=discord.ButtonStyle.success, row=0)
    async def approve_btn(self, interaction: discord.Interaction, button: Button):
        if not is_admin(interaction):
            await interaction.response.send_message("❌ Permission Denied.", ephemeral=True)
            return

        if not self.submissions:
            await interaction.response.edit_message(embed=self.get_current_embed(), view=self)
            return

        sub = self.submissions[self.current_index]
        if str(interaction.user.id) == sub.discord_user_id:
            await interaction.response.send_message("❌ Anti-Self-Approval Rule: You cannot approve your own submission.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)
        try:
            with session_scope() as session:
                service = TaskService(session)
                approved_sub = service.approve_submission(
                    submission_id=sub.id,
                    reviewer_discord_id=str(interaction.user.id),
                )
                ws = WalletService(session)
                _, u_wallet, _ = ws.get_or_create_user(approved_sub.discord_user_id)
                new_bal = u_wallet.available_balance if u_wallet else 0
                sub_id = str(approved_sub.id)
                user_id = str(approved_sub.discord_user_id)
                reward_amt = int(approved_sub.reward_amount or 0)
                tx_id = str(approved_sub.obx_transaction_id or "")
                task_title = str(sub.task.title) if hasattr(sub, "task") and sub.task else "Task"

            logger.info("[APPROVAL] New balance: %d OBX for user %s", new_bal, user_id)

            # Send member congratulations DM (safe, non-blocking for transaction)
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
                logger.error("[DM] Could not send approval DM: %s\n%s", notif_err, traceback.format_exc())

            # Send private operational log to #obx-admin-logs
            from apps.obx_tasks.bot.announcement_service import send_admin_log_event
            if interaction.guild:
                await send_admin_log_event(
                    guild=interaction.guild,
                    title="✅ [SUBMISSION APPROVED]",
                    description=(
                        f"<@{interaction.user.id}> approved submission for <@{user_id}>!\n"
                        f"**Task:** {task_title}\n"
                        f"**Reward Credited:** `+{reward_amt:,} OBX`\n"
                        f"**OBX Transaction ID:** `{tx_id}`\n"
                        f"**Proof Media Deleted:** `{'Yes (Retention Policy)' if approved_sub.proof_media_deleted else 'Retained'}`"
                    ),
                    color=COLOR_GREEN,
                )

            # Remove from active queue
            self.submissions.pop(self.current_index)
            if self.current_index >= len(self.submissions):
                self.current_index = max(0, len(self.submissions) - 1)
            self.update_buttons()

            await interaction.edit_original_response(embed=self.get_current_embed(), view=self)
        except Exception as exc:
            logger.error("Error approving submission in queue: %s", exc)
            await interaction.followup.send(f"❌ Approval failed: {str(exc)}", ephemeral=True)

    @discord.ui.button(label="Reject", style=discord.ButtonStyle.danger, row=0)
    async def reject_btn(self, interaction: discord.Interaction, button: Button):
        if not is_admin(interaction):
            await interaction.response.send_message("❌ Permission Denied.", ephemeral=True)
            return

        if not self.submissions:
            await interaction.response.edit_message(embed=self.get_current_embed(), view=self)
            return

        sub = self.submissions[self.current_index]
        modal = AdminQueueRejectModal(queue_view=self, submission_id=str(sub.id), submitter_id=str(sub.discord_user_id))
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="Skip", style=discord.ButtonStyle.secondary, row=0)
    async def skip_btn(self, interaction: discord.Interaction, button: Button):
        if not is_admin(interaction):
            await interaction.response.send_message("❌ Permission Denied.", ephemeral=True)
            return

        if self.submissions:
            self.current_index = (self.current_index + 1) % len(self.submissions)
        self.update_buttons()
        await interaction.response.edit_message(embed=self.get_current_embed(), view=self)

    @discord.ui.button(label="Previous", style=discord.ButtonStyle.secondary, row=1)
    async def prev_btn(self, interaction: discord.Interaction, button: Button):
        if self.current_index > 0:
            self.current_index -= 1
        self.update_buttons()
        await interaction.response.edit_message(embed=self.get_current_embed(), view=self)

    @discord.ui.button(label="Next", style=discord.ButtonStyle.secondary, row=1)
    async def next_btn(self, interaction: discord.Interaction, button: Button):
        if self.current_index < len(self.submissions) - 1:
            self.current_index += 1
        self.update_buttons()
        await interaction.response.edit_message(embed=self.get_current_embed(), view=self)

    @discord.ui.button(label="Refresh", style=discord.ButtonStyle.primary, row=1)
    async def refresh_btn(self, interaction: discord.Interaction, button: Button):
        await interaction.response.defer(ephemeral=True)
        try:
            with session_scope() as session:
                service = TaskService(session)
                self.submissions, _ = service.list_submissions(status=SubmissionStatus.PENDING, limit=100)
            self.current_index = 0
            self.update_buttons()
            await interaction.edit_original_response(embed=self.get_current_embed(), view=self)
        except Exception as exc:
            logger.error("Error refreshing queue: %s", exc)
            await interaction.followup.send("❌ Error refreshing queue.", ephemeral=True)

    @discord.ui.button(label="Test DM", style=discord.ButtonStyle.secondary, row=1)
    async def test_dm_btn(self, interaction: discord.Interaction, button: Button):
        if not is_admin(interaction):
            await interaction.response.send_message("❌ Permission Denied.", ephemeral=True)
            return

        if not self.submissions:
            await interaction.response.send_message("ℹ️ No submissions currently in queue to test.", ephemeral=True)
            return

        sub = self.submissions[self.current_index]
        target_uid = str(sub.discord_user_id)

        await interaction.response.defer(ephemeral=True)
        try:
            with session_scope() as session:
                ws = WalletService(session)
                _, u_wallet, _ = ws.get_or_create_user(target_uid)
                current_bal = u_wallet.available_balance if u_wallet else 0
                test_reward = sub.task.reward_per_user if hasattr(sub, "task") and sub.task else 10

            from apps.obx_tasks.bot.notification_service import send_approval_dm
            ok, detail = await send_approval_dm(
                bot=interaction.client,
                discord_user_id=target_uid,
                approved_amount=test_reward,
                new_balance=current_bal,
                is_test=True,
                return_detail=True,
            )
            if ok:
                await interaction.followup.send(
                    f"✅ **TEST DM SENT SUCCESSFULLY**\n\n"
                    f"Sent approval DM to <@{target_uid}>.\n"
                    f"Reward Displayed: `+{test_reward:,} OBX`\n"
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
            logger.error("[TEST DM] Failed: %s\n%s", exc, traceback.format_exc())
            await interaction.followup.send(f"❌ Test DM error: {str(exc)}", ephemeral=True)


class AdminQueueRejectModal(Modal, title="❌ REJECT SUBMISSION"):
    def __init__(self, queue_view: AdminReviewQueueView, submission_id: str, submitter_id: str):
        super().__init__()
        self.queue_view = queue_view
        self.submission_id = submission_id
        self.submitter_id = submitter_id

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
        try:
            with session_scope() as session:
                service = TaskService(session)
                sub = service.reject_submission(
                    submission_id=self.submission_id,
                    reviewer_discord_id=str(interaction.user.id),
                    rejection_reason=self.reason.value,
                )

            # Send private operational log to #obx-admin-logs
            from apps.obx_tasks.bot.announcement_service import send_admin_log_event
            if interaction.guild:
                await send_admin_log_event(
                    guild=interaction.guild,
                    title="❌ [SUBMISSION REJECTED]",
                    description=(
                        f"<@{interaction.user.id}> rejected submission for <@{self.submitter_id}>.\n"
                        f"**Reason:** *{self.reason.value.strip()}*\n"
                        f"**Proof Media Deleted:** `{'Yes (Retention Policy)' if sub.proof_media_deleted else 'Retained'}`"
                    ),
                    color=COLOR_RED,
                )

            # Remove from queue view and advance
            self.queue_view.submissions = [s for s in self.queue_view.submissions if str(s.id) != self.submission_id]
            if self.queue_view.current_index >= len(self.queue_view.submissions):
                self.queue_view.current_index = max(0, len(self.queue_view.submissions) - 1)
            self.queue_view.update_buttons()

            if interaction.message:
                await interaction.message.edit(embed=self.queue_view.get_current_embed(), view=self.queue_view)
            await interaction.followup.send(f"❌ Submission `{self.submission_id}` rejected.", ephemeral=True)
        except Exception as exc:
            logger.error("Error in AdminQueueRejectModal: %s", exc)
            await interaction.followup.send(f"❌ Rejection failed: {str(exc)}", ephemeral=True)


async def handle_admin_review(interaction: discord.Interaction):
    if not is_admin(interaction):
        await interaction.response.send_message("❌ Permission Denied: Administrator role required.", ephemeral=True)
        return

    if not _is_response_done(interaction):
        await interaction.response.defer(ephemeral=True)

    try:
        with session_scope() as session:
            service = TaskService(session)
            submissions, total = service.list_submissions(
                status=SubmissionStatus.PENDING,
                limit=50,
            )

            if not submissions:
                embed = discord.Embed(
                    title="🎉 Review Queue Caught Up!",
                    description="🎉 **All caught up — no pending submissions awaiting review.**",
                    color=COLOR_GREEN,
                )
                embed.set_footer(text="OBX Administrative Control Center")
                await interaction.followup.send(embed=embed, ephemeral=True)
                return

            queue_view = AdminReviewQueueView(submissions=submissions, current_index=0)
            await interaction.followup.send(embed=queue_view.get_current_embed(), view=queue_view, ephemeral=True)
    except Exception as exc:
        logger.error("Error in handle_admin_review: %s", exc)
        await interaction.followup.send("❌ Error fetching review queue.", ephemeral=True)


async def handle_admin_health(interaction: discord.Interaction):
    if not is_admin(interaction):
        await interaction.response.send_message("❌ Permission Denied: Administrator role required.", ephemeral=True)
        return

    if not _is_response_done(interaction):
        await interaction.response.defer(ephemeral=True)

    settings = get_settings()
    t0 = time.perf_counter()
    db_status = "Disconnected"
    try:
        with get_engine().connect() as conn:
            conn.execute(text("SELECT 1"))
        latency_ms = (time.perf_counter() - t0) * 1000
        db_status = f"Connected ({latency_ms:.1f}ms)"
    except Exception as e:
        db_status = f"Error: {str(e)[:50]}"

    guild_info = "Not Configured"
    role_info = "Not Configured"
    if settings.DISCORD_GUILD_ID:
        guild = interaction.client.get_guild(int(settings.DISCORD_GUILD_ID))
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
        description="Live operational telemetry and configuration status:",
        color=COLOR_GREEN,
    )
    embed.add_field(name="Bot Gateway Status", value="🟢 `Online & Operational`", inline=True)
    embed.add_field(name="Database Engine", value=f"`{db_status}`", inline=True)
    embed.add_field(name="Migration Head", value="`005_guild_channel_configuration`", inline=True)
    embed.add_field(name="Connected Guild", value=f"`{guild_info}`", inline=False)
    embed.add_field(name="Verified Admin Roles", value=f"`{role_info}`", inline=False)
    embed.add_field(name="Application Environment", value=f"`{settings.ENVIRONMENT}`", inline=True)

    embed.set_footer(text="All credentials, connection strings, and tokens remain secured.")
    await interaction.followup.send(embed=embed, ephemeral=True)


async def handle_refresh_public_systems(interaction: discord.Interaction):
    if not is_admin(interaction):
        await interaction.response.send_message("❌ Permission Denied: Administrator role required.", ephemeral=True)
        return

    if not _is_response_done(interaction):
        await interaction.response.defer(ephemeral=True)

    from apps.obx_tasks.bot.announcement_service import refresh_all_public_systems
    results = await refresh_all_public_systems(interaction.guild, interaction.client)

    embed = discord.Embed(
        title="📊 Public Systems Refreshed",
        description="Refreshed all active public dashboards and leaderboards across configured channels:\n",
        color=COLOR_GREEN,
    )
    for sys_name, res_str in results.items():
        embed.add_field(name=f"📍 {sys_name}", value=res_str, inline=False)

    await interaction.followup.send(embed=embed, ephemeral=True)
