import inspect
import uuid
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any, Tuple
import discord
from discord.ui import View, Button, Modal, TextInput, Select

from packages.database.session import session_scope
from packages.database.models.task import Task
from packages.database.models.submission import TaskSubmission
from packages.shared.enums import TaskStatus, TaskType, SubmissionStatus
from packages.shared.exceptions import TaskError, OBXError
from packages.shared.logging import get_logger
from packages.shared.utils import parse_duration_or_datetime
from apps.obx_tasks.services.task_service import TaskService
from apps.obx_tasks.bot.permissions import is_admin
from apps.obx_tasks.bot.ui_theme import (
    COLOR_GOLD, COLOR_TEAL, COLOR_BLUE, COLOR_PURPLE, COLOR_RED, COLOR_GREEN,
    BADGE_ACTIVE, BADGE_PAUSED, BADGE_COMPLETED,
)
from apps.obx_tasks.bot.announcement_service import announce_task, send_admin_log_event

logger = get_logger("obx.tasks.bot.task_management")

PAGE_SIZE = 5

STATUS_MAP = {
    "ACTIVE": (TaskStatus.ACTIVE, "🟢 Active Tasks"),
    "DRAFT": (TaskStatus.DRAFT, "🟡 Scheduled / Draft Tasks"),
    "PAUSED": (TaskStatus.PAUSED, "⏸️ Paused Tasks"),
    "CANCELLED": (TaskStatus.CANCELLED, "🔴 Cancelled Tasks"),
    "EXPIRED": (TaskStatus.EXPIRED, "⌛ Expired Tasks"),
    "COMPLETED": (TaskStatus.COMPLETED, "✅ Completed / Depleted Tasks"),
}


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


def build_task_browser_embed(
    tasks: List[Task],
    status_filter: TaskStatus,
    page: int,
    total_tasks: int,
) -> discord.Embed:
    """Builds the Task Browser (List screen) embed."""
    _, status_label = STATUS_MAP.get(status_filter.value, (None, status_filter.value))
    total_pages = max(1, (total_tasks + PAGE_SIZE - 1) // PAGE_SIZE)
    page = max(0, min(page, total_pages - 1))

    start_idx = page * PAGE_SIZE
    page_tasks = tasks[start_idx : start_idx + PAGE_SIZE]

    embed = discord.Embed(
        title=f"📝 Task Management — {status_label}",
        color=COLOR_GOLD if status_filter == TaskStatus.ACTIVE else COLOR_PURPLE,
    )

    if not tasks:
        embed.description = (
            f"No tasks found matching status `{status_filter.value}`.\n\n"
            "Use the filter menu below to switch categories or return to the Admin Hub."
        )
        embed.set_footer(text="Page 1 of 1 • 0 Total Tasks • OBX Admin Control")
        return embed

    lines = [
        f"**Page {page + 1} of {total_pages}** • Total: `{total_tasks}` tasks",
        "Select a task from the menu below to view details and administrative actions.\n",
    ]

    for i, t in enumerate(page_tasks):
        global_num = start_idx + i + 1
        platform_icon = "𝕏" if (t.platform and "X" in t.platform.upper()) else "🌐"
        claimed_info = f"{t.approved_count}/{t.max_approvals} claimed"
        lines.append(
            f"**{global_num}. {t.title}**\n"
            f"   {platform_icon} `{t.task_type.value}` • 💰 `{t.reward_per_user:,} OBX` • 📦 `{t.distributed_reward:,}/{t.total_reward_pool:,} OBX` • *({claimed_info})*"
        )

    embed.description = "\n".join(lines)
    embed.set_footer(text=f"Page {page + 1} of {total_pages} • Status: {status_filter.value} • OBX Admin Control")
    return embed


def build_task_detail_embed(
    task: Task,
    current_index: int,
    total_tasks: int,
) -> discord.Embed:
    """Builds the detailed task management embed for an individual task."""
    embed = discord.Embed(
        title=f"📝 Task Management — {task.title}",
        description=f"> {task.description}\n",
        color=COLOR_GOLD if task.status == TaskStatus.ACTIVE else COLOR_PURPLE,
    )

    embed.add_field(name="📌 Category / Type", value=f"`{task.task_type.value}` on `{task.platform}`", inline=True)
    embed.add_field(name="💰 Reward / User", value=f"**{task.reward_per_user:,} OBX**", inline=True)
    embed.add_field(
        name="📦 Reward Pool",
        value=f"`{task.distributed_reward:,} / {task.total_reward_pool:,} OBX`\n*({task.approved_count}/{task.max_approvals} claimed)*",
        inline=True,
    )

    if task.ends_at:
        ts = int(task.ends_at.timestamp())
        embed.add_field(name="⏳ Expiry / Deadline", value=f"<t:{ts}:F> (<t:{ts}:R>)", inline=True)
    else:
        embed.add_field(name="⏳ Expiry / Deadline", value="*No deadline set*", inline=True)

    proof_info = f"Proof URL: `{'Yes' if task.proof_required else 'No'}` • Image: `{'Allowed' if task.allow_image_proof else 'Blocked'}`"
    embed.add_field(name="📸 Proof Requirements", value=proof_info, inline=True)
    embed.add_field(name="🔔 Reward Notifications", value=f"`{task.notification_type}`", inline=True)

    if task.target_url:
        embed.add_field(name="🔗 Official Link", value=f"[Open Target URL]({task.target_url})", inline=False)

    if task.status == TaskStatus.CANCELLED and task.cancellation_reason:
        embed.add_field(name="🛑 Cancellation Reason", value=f"*{task.cancellation_reason}*", inline=False)

    embed.set_footer(
        text=f"Task {current_index + 1} of {total_tasks} • Status: {task.status.value} • ID: {task.id}"
    )
    return embed


class AdminTaskBrowserView(View):
    """Task Browser View (Tier 1: List Screen)."""
    def __init__(
        self,
        tasks: List[Task],
        status_filter: TaskStatus = TaskStatus.ACTIVE,
        current_page: int = 0,
    ):
        super().__init__(timeout=300)
        self.tasks = tasks
        self.status_filter = status_filter
        self.current_page = current_page
        self._build_components()

    def _build_components(self):
        self.clear_items()
        total_tasks = len(self.tasks)
        total_pages = max(1, (total_tasks + PAGE_SIZE - 1) // PAGE_SIZE)
        self.current_page = max(0, min(self.current_page, total_pages - 1))

        start_idx = self.current_page * PAGE_SIZE
        page_tasks = self.tasks[start_idx : start_idx + PAGE_SIZE]

        # Row 0: Select Task Dropdown (if tasks on page)
        if page_tasks:
            options = []
            for i, t in enumerate(page_tasks):
                global_idx = start_idx + i
                options.append(discord.SelectOption(
                    label=f"{global_idx + 1}. {t.title[:80]}",
                    description=f"{t.task_type.value} • {t.reward_per_user:,} OBX • Status: {t.status.value}",
                    value=str(global_idx),
                ))
            select_task = Select(
                placeholder="Select a task to inspect & manage...",
                options=options,
                custom_id=f"obx:mgmt:select_task:{self.status_filter.value}:{self.current_page}",
                row=0,
            )
            select_task.callback = self._on_select_task
            self.add_item(select_task)

        # Row 1: Status Filter Select Menu
        filter_options = [
            discord.SelectOption(label="🟢 Active Tasks", value="ACTIVE", default=(self.status_filter == TaskStatus.ACTIVE)),
            discord.SelectOption(label="🟡 Scheduled / Draft", value="DRAFT", default=(self.status_filter == TaskStatus.DRAFT)),
            discord.SelectOption(label="⏸️ Paused", value="PAUSED", default=(self.status_filter == TaskStatus.PAUSED)),
            discord.SelectOption(label="⌛ Expired", value="EXPIRED", default=(self.status_filter == TaskStatus.EXPIRED)),
            discord.SelectOption(label="✅ Completed / Depleted", value="COMPLETED", default=(self.status_filter == TaskStatus.COMPLETED)),
            discord.SelectOption(label="🔴 Cancelled", value="CANCELLED", default=(self.status_filter == TaskStatus.CANCELLED)),
        ]
        status_select = Select(
            placeholder="Filter tasks by status...",
            options=filter_options,
            custom_id=f"obx:mgmt:filter_status:{self.current_page}",
            row=1,
        )
        status_select.callback = self._on_status_select
        self.add_item(status_select)

        # Row 2: Pagination Controls
        btn_prev_page = Button(
            label="Previous Page",
            style=discord.ButtonStyle.secondary,
            custom_id=f"obx:mgmt:page_prev:{self.status_filter.value}:{self.current_page}",
            disabled=(self.current_page <= 0),
            row=2,
        )
        btn_prev_page.callback = self._on_page_prev
        self.add_item(btn_prev_page)

        btn_next_page = Button(
            label="Next Page",
            style=discord.ButtonStyle.secondary,
            custom_id=f"obx:mgmt:page_next:{self.status_filter.value}:{self.current_page}",
            disabled=(self.current_page >= total_pages - 1),
            row=2,
        )
        btn_next_page.callback = self._on_page_next
        self.add_item(btn_next_page)

        btn_reload = Button(
            label="Reload",
            style=discord.ButtonStyle.secondary,
            custom_id=f"obx:mgmt:page_reload:{self.status_filter.value}:{self.current_page}",
            row=2,
        )
        btn_reload.callback = self._on_reload
        self.add_item(btn_reload)

        # Row 3: Back to Admin Hub
        btn_hub = Button(
            label="Back to Admin Hub",
            style=discord.ButtonStyle.secondary,
            custom_id="obx:mgmt:hub",
            row=3,
        )
        btn_hub.callback = self._on_back_to_hub
        self.add_item(btn_hub)

    def get_current_embed(self) -> discord.Embed:
        return build_task_browser_embed(self.tasks, self.status_filter, self.current_page, len(self.tasks))

    async def _on_select_task(self, interaction: discord.Interaction):
        await handle_admin_mgmt_interaction(interaction, f"obx:mgmt:select_task:{self.status_filter.value}:{self.current_page}")

    async def _on_status_select(self, interaction: discord.Interaction):
        await handle_admin_mgmt_interaction(interaction, f"obx:mgmt:filter_status:{self.current_page}")

    async def _on_page_prev(self, interaction: discord.Interaction):
        await handle_admin_mgmt_interaction(interaction, f"obx:mgmt:page_prev:{self.status_filter.value}:{self.current_page}")

    async def _on_page_next(self, interaction: discord.Interaction):
        await handle_admin_mgmt_interaction(interaction, f"obx:mgmt:page_next:{self.status_filter.value}:{self.current_page}")

    async def _on_reload(self, interaction: discord.Interaction):
        await handle_admin_mgmt_interaction(interaction, f"obx:mgmt:page_reload:{self.status_filter.value}:{self.current_page}")

    async def _on_back_to_hub(self, interaction: discord.Interaction):
        await handle_admin_mgmt_interaction(interaction, "obx:mgmt:hub")


class AdminTaskDetailView(View):
    """Task Detail View (Tier 2: Single Task Screen)."""
    def __init__(
        self,
        task: Task,
        status_filter: TaskStatus,
        current_index: int,
        total_tasks: int,
        current_page: int,
    ):
        super().__init__(timeout=300)
        self.task = task
        self.status_filter = status_filter
        self.current_index = current_index
        self.total_tasks = total_tasks
        self.current_page = current_page
        self._build_components()

    def _build_components(self):
        self.clear_items()
        task_id = str(self.task.id)

        # Row 0: Previous Task / Next Task / Refresh Preview
        btn_prev = Button(
            label="Previous Task",
            style=discord.ButtonStyle.secondary,
            custom_id=f"obx:mgmt:prev:{self.status_filter.value}:{self.current_page}:{self.current_index}",
            disabled=(self.current_index <= 0),
            row=0,
        )
        btn_prev.callback = self._on_prev
        self.add_item(btn_prev)

        btn_next = Button(
            label="Next Task",
            style=discord.ButtonStyle.secondary,
            custom_id=f"obx:mgmt:next:{self.status_filter.value}:{self.current_page}:{self.current_index}",
            disabled=(self.current_index >= self.total_tasks - 1),
            row=0,
        )
        btn_next.callback = self._on_next
        self.add_item(btn_next)

        btn_refresh_prev = Button(
            label="Refresh Preview",
            style=discord.ButtonStyle.secondary,
            custom_id=f"obx:mgmt:refresh_prev:{task_id}:{self.status_filter.value}:{self.current_page}:{self.current_index}",
            row=0,
        )
        btn_refresh_prev.callback = self._on_refresh_preview
        self.add_item(btn_refresh_prev)

        # Row 1: Content & Reward Edits
        btn_edit_meta = Button(
            label="Edit Content",
            style=discord.ButtonStyle.primary,
            custom_id=f"obx:mgmt:edit_meta:{task_id}:{self.status_filter.value}:{self.current_page}:{self.current_index}",
            row=1,
        )
        btn_edit_meta.callback = self._on_edit_meta
        self.add_item(btn_edit_meta)

        btn_edit_reward = Button(
            label="Edit Reward & Pool",
            style=discord.ButtonStyle.primary,
            custom_id=f"obx:mgmt:edit_reward:{task_id}:{self.status_filter.value}:{self.current_page}:{self.current_index}",
            row=1,
        )
        btn_edit_reward.callback = self._on_edit_reward
        self.add_item(btn_edit_reward)

        btn_override_prev = Button(
            label="Preview Override",
            style=discord.ButtonStyle.primary,
            custom_id=f"obx:mgmt:override_prev:{task_id}:{self.status_filter.value}:{self.current_page}:{self.current_index}",
            row=1,
        )
        btn_override_prev.callback = self._on_override_preview
        self.add_item(btn_override_prev)

        btn_refresh_card = Button(
            label="Refresh Card",
            style=discord.ButtonStyle.secondary,
            custom_id=f"obx:mgmt:refresh:{task_id}:{self.status_filter.value}:{self.current_page}:{self.current_index}",
            row=1,
        )
        btn_refresh_card.callback = self._on_refresh_card
        self.add_item(btn_refresh_card)

        # Row 2: Dangerous Actions
        is_cancelled = (self.task.status == TaskStatus.CANCELLED)
        btn_cancel = Button(
            label="Cancel Task" if not is_cancelled else "Cancelled",
            style=discord.ButtonStyle.danger,
            custom_id=f"obx:mgmt:cancel:{task_id}:{self.status_filter.value}:{self.current_page}:{self.current_index}",
            disabled=is_cancelled,
            row=2,
        )
        btn_cancel.callback = self._on_cancel_click
        self.add_item(btn_cancel)

        btn_del = Button(
            label="Safe Delete",
            style=discord.ButtonStyle.danger,
            custom_id=f"obx:mgmt:delete:{task_id}:{self.status_filter.value}:{self.current_page}:{self.current_index}",
            row=2,
        )
        btn_del.callback = self._on_delete_click
        self.add_item(btn_del)

        # Row 3: Back Navigation
        btn_back_list = Button(
            label="Back to Task List",
            style=discord.ButtonStyle.secondary,
            custom_id=f"obx:mgmt:back_to_list:{self.status_filter.value}:{self.current_page}",
            row=3,
        )
        btn_back_list.callback = self._on_back_to_list
        self.add_item(btn_back_list)

        btn_hub = Button(
            label="Back to Admin Hub",
            style=discord.ButtonStyle.secondary,
            custom_id="obx:mgmt:hub",
            row=3,
        )
        btn_hub.callback = self._on_back_to_hub
        self.add_item(btn_hub)

    def get_current_embed(self) -> discord.Embed:
        return build_task_detail_embed(self.task, self.current_index, self.total_tasks)

    async def _on_prev(self, interaction: discord.Interaction):
        await handle_admin_mgmt_interaction(interaction, f"obx:mgmt:prev:{self.status_filter.value}:{self.current_page}:{self.current_index}")

    async def _on_next(self, interaction: discord.Interaction):
        await handle_admin_mgmt_interaction(interaction, f"obx:mgmt:next:{self.status_filter.value}:{self.current_page}:{self.current_index}")

    async def _on_refresh_preview(self, interaction: discord.Interaction):
        await handle_admin_mgmt_interaction(
            interaction,
            f"obx:mgmt:refresh_prev:{self.task.id}:{self.status_filter.value}:{self.current_page}:{self.current_index}",
        )

    async def _on_edit_meta(self, interaction: discord.Interaction):
        await handle_admin_mgmt_interaction(
            interaction,
            f"obx:mgmt:edit_meta:{self.task.id}:{self.status_filter.value}:{self.current_page}:{self.current_index}",
        )

    async def _on_edit_reward(self, interaction: discord.Interaction):
        await handle_admin_mgmt_interaction(
            interaction,
            f"obx:mgmt:edit_reward:{self.task.id}:{self.status_filter.value}:{self.current_page}:{self.current_index}",
        )

    async def _on_override_preview(self, interaction: discord.Interaction):
        await handle_admin_mgmt_interaction(
            interaction,
            f"obx:mgmt:override_prev:{self.task.id}:{self.status_filter.value}:{self.current_page}:{self.current_index}",
        )

    async def _on_refresh_card(self, interaction: discord.Interaction):
        await handle_admin_mgmt_interaction(
            interaction,
            f"obx:mgmt:refresh:{self.task.id}:{self.status_filter.value}:{self.current_page}:{self.current_index}",
        )

    async def _on_cancel_click(self, interaction: discord.Interaction):
        await handle_admin_mgmt_interaction(
            interaction,
            f"obx:mgmt:cancel:{self.task.id}:{self.status_filter.value}:{self.current_page}:{self.current_index}",
        )

    async def _on_delete_click(self, interaction: discord.Interaction):
        await handle_admin_mgmt_interaction(
            interaction,
            f"obx:mgmt:delete:{self.task.id}:{self.status_filter.value}:{self.current_page}:{self.current_index}",
        )

    async def _on_back_to_list(self, interaction: discord.Interaction):
        await handle_admin_mgmt_interaction(
            interaction,
            f"obx:mgmt:back_to_list:{self.status_filter.value}:{self.current_page}",
        )

    async def _on_back_to_hub(self, interaction: discord.Interaction):
        await handle_admin_mgmt_interaction(interaction, "obx:mgmt:hub")


# Compatibility alias for existing references
AdminTaskManageBrowserView = AdminTaskBrowserView


async def render_task_browser(
    interaction: discord.Interaction,
    status_filter: TaskStatus = TaskStatus.ACTIVE,
    page: int = 0,
):
    """Renders the Task Browser (List Screen) in place."""
    with session_scope() as session:
        service = TaskService(session)
        tasks, _ = service.list_tasks(status=status_filter, limit=100)

    total_tasks = len(tasks)
    total_pages = max(1, (total_tasks + PAGE_SIZE - 1) // PAGE_SIZE)
    page = max(0, min(page, total_pages - 1))

    view = AdminTaskBrowserView(tasks=tasks, status_filter=status_filter, current_page=page)
    embed = view.get_current_embed()
    await safe_edit_interaction(interaction, embed=embed, view=view, content=None)


async def render_task_detail(
    interaction: discord.Interaction,
    status_filter: TaskStatus = TaskStatus.ACTIVE,
    index: int = 0,
):
    """Renders the Task Detail screen for a specific task in place."""
    with session_scope() as session:
        service = TaskService(session)
        tasks, _ = service.list_tasks(status=status_filter, limit=100)

    if not tasks:
        await render_task_browser(interaction, status_filter=status_filter, page=0)
        return

    total_tasks = len(tasks)
    index = max(0, min(index, total_tasks - 1))
    page = index // PAGE_SIZE
    task = tasks[index]

    view = AdminTaskDetailView(
        task=task,
        status_filter=status_filter,
        current_index=index,
        total_tasks=total_tasks,
        current_page=page,
    )
    embed = view.get_current_embed()
    await safe_edit_interaction(interaction, embed=embed, view=view, content=None)


async def render_admin_hub(interaction: discord.Interaction):
    """Restores the main Admin Hub interface in place."""
    from apps.obx_tasks.bot.dashboard_views import create_admin_hub_embed, OBXAdminHubView
    embed = create_admin_hub_embed()
    view = OBXAdminHubView()
    await safe_edit_interaction(interaction, embed=embed, view=view, content=None)


async def handle_admin_mgmt_interaction(interaction: discord.Interaction, custom_id: str):
    """Central interaction router for all Admin Task Management navigation and actions.
    Guarantees state preservation across bot restarts and view timeouts.
    """
    if not is_admin(interaction):
        await interaction.response.send_message("❌ Permission Denied: Administrator role required.", ephemeral=True)
        return

    # 1. Back to Admin Hub
    if custom_id == "obx:mgmt:hub":
        await render_admin_hub(interaction)
        return

    # 2. Back to Task List Browser
    if custom_id.startswith("obx:mgmt:back_to_list:") or custom_id.startswith("obx:mgmt:list:"):
        parts = custom_id.split(":")
        status_str = parts[3] if len(parts) > 3 else "ACTIVE"
        page = int(parts[4]) if len(parts) > 4 and parts[4].isdigit() else 0
        status_enum, _ = STATUS_MAP.get(status_str, (TaskStatus.ACTIVE, "Active"))
        await render_task_browser(interaction, status_filter=status_enum, page=page)
        return

    # 3. Status Filter in Browser
    if custom_id.startswith("obx:mgmt:filter_status"):
        values = getattr(interaction, "data", {}).get("values", [])
        selected_val = values[0] if values else "ACTIVE"
        status_enum, _ = STATUS_MAP.get(selected_val, (TaskStatus.ACTIVE, "Active"))
        await render_task_browser(interaction, status_filter=status_enum, page=0)
        return

    # 4. Pagination in Browser (page_prev, page_next, page_reload)
    if custom_id.startswith("obx:mgmt:page_prev:") or custom_id.startswith("obx:mgmt:page_next:") or custom_id.startswith("obx:mgmt:page_reload:"):
        parts = custom_id.split(":")
        action = parts[2]
        status_str = parts[3] if len(parts) > 3 else "ACTIVE"
        page = int(parts[4]) if len(parts) > 4 and parts[4].isdigit() else 0
        status_enum, _ = STATUS_MAP.get(status_str, (TaskStatus.ACTIVE, "Active"))
        if action == "page_prev":
            page = max(0, page - 1)
        elif action == "page_next":
            page = page + 1
        await render_task_browser(interaction, status_filter=status_enum, page=page)
        return

    # 5. Select Task Dropdown in Browser
    if custom_id.startswith("obx:mgmt:select_task:"):
        parts = custom_id.split(":")
        status_str = parts[3] if len(parts) > 3 else "ACTIVE"
        status_enum, _ = STATUS_MAP.get(status_str, (TaskStatus.ACTIVE, "Active"))
        values = getattr(interaction, "data", {}).get("values", [])
        selected_val = values[0] if values else "0"
        index = int(selected_val) if selected_val.isdigit() else 0
        await render_task_detail(interaction, status_filter=status_enum, index=index)
        return

    # 6. Previous / Next Task in Detail View
    if custom_id.startswith("obx:mgmt:prev:") or custom_id.startswith("obx:mgmt:next:"):
        parts = custom_id.split(":")
        direction = parts[2]
        status_str = parts[3] if len(parts) > 3 else "ACTIVE"
        index = int(parts[5]) if len(parts) > 5 and parts[5].isdigit() else 0
        status_enum, _ = STATUS_MAP.get(status_str, (TaskStatus.ACTIVE, "Active"))
        if direction == "prev":
            index = max(0, index - 1)
        else:
            index = index + 1
        await render_task_detail(interaction, status_filter=status_enum, index=index)
        return

    # 7. Refresh Preview Action
    if custom_id.startswith("obx:mgmt:refresh_prev:"):
        parts = custom_id.split(":")
        task_id = parts[3]
        status_str = parts[4] if len(parts) > 4 else "ACTIVE"
        index = int(parts[6]) if len(parts) > 6 and parts[6].isdigit() else 0
        status_enum, _ = STATUS_MAP.get(status_str, (TaskStatus.ACTIVE, "Active"))

        await interaction.response.defer(ephemeral=True)
        with session_scope() as session:
            task_svc = TaskService(session)
            updated_task, meta = await task_svc.refresh_task_preview(task_id)

        ann_msg = ""
        if interaction.guild:
            ok, ann_msg = await announce_task(updated_task, interaction.guild, interaction.client)

        status_emoji = "✅" if meta.status == "SUCCESS" else "⚠️"
        snippet = f"\"{meta.description[:120]}...\"" if meta.description else "*No content extracted*"
        report = (
            f"{status_emoji} **Preview Refreshed for Task:** {updated_task.title}\n\n"
            f"• **Provider:** `{meta.source}`\n"
            f"• **Status:** `{meta.status}`\n"
            f"• **Author:** `{meta.author or 'N/A'}`\n"
            f"• **Snippet:** {snippet}\n"
            f"• **Media Image:** {'Yes' if meta.image_url else 'None'}\n\n"
            f"📢 **Public Announcement:** {ann_msg}"
        )
        await interaction.followup.send(report, ephemeral=True)
        await render_task_detail(interaction, status_filter=status_enum, index=index)
        return

    # 8. Refresh Public Card Action
    if custom_id.startswith("obx:mgmt:refresh:"):
        parts = custom_id.split(":")
        task_id = parts[3]
        await interaction.response.defer(ephemeral=True)
        with session_scope() as session:
            service = TaskService(session)
            task = service.get_task(task_id)
            if interaction.guild:
                ok, msg = await announce_task(task, interaction.guild, interaction.client)
                await interaction.followup.send(f"📢 **Announcement Refreshed:** {msg}", ephemeral=True)
            else:
                await interaction.followup.send("❌ Error: No guild context found.", ephemeral=True)
        return

    # 9. Cancel Task Prompt
    if custom_id.startswith("obx:mgmt:cancel:"):
        parts = custom_id.split(":")
        task_id = parts[3]
        with session_scope() as session:
            service = TaskService(session)
            task = service.get_task(task_id)
            from sqlalchemy import select, func
            pending_count = session.execute(
                select(func.count()).select_from(TaskSubmission).where(
                    TaskSubmission.task_id == task.id,
                    TaskSubmission.status == SubmissionStatus.PENDING,
                )
            ).scalar() or 0

        embed = discord.Embed(
            title=f"🛑 Cancel Task: {task.title}",
            description=(
                f"Are you sure you want to cancel this task?\n\n"
                f"• Submissions will be blocked immediately.\n"
                f"• Public card in `#tasks` will update to `🛑 CANCELLED`.\n"
                f"• Already distributed rewards (`{task.distributed_reward:,} OBX`) will remain protected.\n"
            ),
            color=COLOR_RED,
        )
        if pending_count > 0:
            embed.add_field(
                name=f"⚠️ Pending Submissions Detected ({pending_count})",
                value="Select an option below to decide how to handle pending submissions upon cancellation:",
                inline=False,
            )
        view = AdminCancelTaskPromptView(task_id=str(task.id), pending_count=pending_count)
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
        return

    # 10. Safe Delete Task Prompt
    if custom_id.startswith("obx:mgmt:delete:"):
        parts = custom_id.split(":")
        task_id = parts[3]
        with session_scope() as session:
            service = TaskService(session)
            task = service.get_task(task_id)
            from sqlalchemy import select, func
            total_subs = session.execute(
                select(func.count()).select_from(TaskSubmission).where(TaskSubmission.task_id == task.id)
            ).scalar() or 0
            is_safe = (task.distributed_reward == 0 and total_subs == 0)

        if not is_safe:
            embed = discord.Embed(
                title="❌ Cannot Delete Task",
                description=(
                    f"Task **{task.title}** cannot be deleted because it has activity history:\n\n"
                    f"• Distributed Rewards: `{task.distributed_reward:,} OBX`\n"
                    f"• Recorded Submissions: `{total_subs}`\n\n"
                    f"**Safety Rule:** Tasks with financial or user activity cannot be deleted.\n"
                    f"Use **Cancel Task** instead to archive it without losing audit history."
                ),
                color=COLOR_RED,
            )
            view = AdminDeleteTaskConfirmView(task_id=str(task.id), is_safe=False)
            await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
            return

        embed = discord.Embed(
            title="🗑️ Confirm Task Deletion",
            description=(
                f"Are you sure you want to permanently delete unused task **{task.title}**?\n\n"
                f"This task has 0 submissions and 0 distributed rewards.\n"
                f"Permanent deletion will remove the task record and cannot be undone."
            ),
            color=COLOR_RED,
        )
        view = AdminDeleteTaskConfirmView(task_id=str(task.id), is_safe=True)
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
        return

    # 11. Edit Metadata Modal
    if custom_id.startswith("obx:mgmt:edit_meta:"):
        parts = custom_id.split(":")
        task_id = parts[3]
        with session_scope() as session:
            service = TaskService(session)
            task = service.get_task(task_id)
        modal = AdminEditTaskMetadataModal(task=task)
        await interaction.response.send_modal(modal)
        return

    # 12. Edit Reward Modal
    if custom_id.startswith("obx:mgmt:edit_reward:"):
        parts = custom_id.split(":")
        task_id = parts[3]
        with session_scope() as session:
            service = TaskService(session)
            task = service.get_task(task_id)
        modal = AdminEditTaskRewardModal(task=task)
        await interaction.response.send_modal(modal)
        return

    # 13. Edit Preview Override Modal
    if custom_id.startswith("obx:mgmt:override_prev:"):
        parts = custom_id.split(":")
        task_id = parts[3]
        with session_scope() as session:
            service = TaskService(session)
            task = service.get_task(task_id)
        modal = AdminEditTaskPreviewModal(task=task)
        await interaction.response.send_modal(modal)
        return


class AdminEditTaskMetadataModal(Modal):
    """Modal for editing task content (Instructions, Type, Target URL, Deadline)."""
    def __init__(self, task: Task):
        super().__init__(title="✏️ EDIT TASK")
        self.task_id = str(task.id)

        self.desc_input = TextInput(
            label="Instructions / Description",
            default=task.description,
            style=discord.TextStyle.paragraph,
            max_length=1000,
            required=True,
        )
        self.add_item(self.desc_input)

        self.url_input = TextInput(
            label="Official Target URL",
            default=task.target_url,
            max_length=1000,
            required=True,
        )
        self.add_item(self.url_input)

        self.type_input = TextInput(
            label="Task Type (LIKE/RETWEET/COMMENT/CUSTOM_TASK)",
            default=task.task_type.value,
            max_length=32,
            required=True,
        )
        self.add_item(self.type_input)

        deadline_default = ""
        if task.ends_at:
            deadline_default = task.ends_at.strftime("%Y-%m-%d %H:%M")
        self.deadline_input = TextInput(
            label="Deadline (e.g. '2h', '24h', '3d', or blank)",
            default=deadline_default,
            max_length=50,
            required=False,
            placeholder="e.g. 2h, 24h, 3d, or 2026-09-05 18:00 (UTC)",
        )
        self.add_item(self.deadline_input)

    async def on_submit(self, interaction: discord.Interaction):
        if not is_admin(interaction):
            await interaction.response.send_message("❌ Permission Denied.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)
        try:
            deadline_dt = None
            if self.deadline_input.value and self.deadline_input.value.strip():
                deadline_dt = parse_duration_or_datetime(self.deadline_input.value.strip())

            raw_type = self.type_input.value.strip().upper()
            try:
                task_type_enum = TaskType(raw_type)
            except ValueError:
                task_type_enum = TaskType.CUSTOM_TASK

            with session_scope() as session:
                service = TaskService(session)
                updated_task = service.edit_task(
                    task_id=self.task_id,
                    changed_by=str(interaction.user.id),
                    description=self.desc_input.value.strip(),
                    target_url=self.url_input.value.strip(),
                    task_type=task_type_enum,
                    ends_at=deadline_dt,
                )

                if interaction.guild:
                    await announce_task(updated_task, interaction.guild, interaction.client)
                    await send_admin_log_event(
                        guild=interaction.guild,
                        title="✏️ [TASK EDITED — CONTENT]",
                        description=(
                            f"<@{interaction.user.id}> edited content for Task **{updated_task.title}**.\n"
                            f"**Type:** `{updated_task.task_type.value}`\n"
                            f"**Target URL:** {updated_task.target_url}\n"
                            f"**Public Card:** Synchronized in place"
                        ),
                        color=COLOR_BLUE,
                    )

            await interaction.followup.send(
                f"✅ **Task Content Updated Successfully!**\nPublic announcement in configured Tasks channel updated in place.",
                ephemeral=True,
            )
        except Exception as exc:
            logger.error("Error submitting task metadata edit modal: %s", exc)
            await interaction.followup.send(f"❌ Edit failed: {str(exc)}", ephemeral=True)


class AdminEditTaskRewardModal(Modal):
    """Modal for editing task financial and notification configuration."""
    def __init__(self, task: Task):
        super().__init__(title="💎 EDIT TASK ECONOMICS")
        self.task_id = str(task.id)

        self.reward_input = TextInput(
            label="Reward per User (OBX)",
            default=str(task.reward_per_user),
            max_length=20,
            required=True,
        )
        self.add_item(self.reward_input)

        self.pool_input = TextInput(
            label="Total Reward Pool (OBX)",
            default=str(task.total_reward_pool),
            max_length=20,
            required=True,
        )
        self.add_item(self.pool_input)

        self.notif_type_input = TextInput(
            label="Notification Type (DEFAULT/HIGH_REWARD/CUSTOM)",
            default=task.notification_type or "DEFAULT",
            max_length=32,
            required=False,
        )
        self.add_item(self.notif_type_input)

        self.notif_template_input = TextInput(
            label="Custom Template (use {user}, {reward})",
            default=task.custom_notification_template or "",
            style=discord.TextStyle.paragraph,
            max_length=500,
            required=False,
        )
        self.add_item(self.notif_template_input)

    async def on_submit(self, interaction: discord.Interaction):
        if not is_admin(interaction):
            await interaction.response.send_message("❌ Permission Denied.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)
        try:
            r_user = int(self.reward_input.value.strip())
            t_pool = int(self.pool_input.value.strip())
            n_type = self.notif_type_input.value.strip().upper() if self.notif_type_input.value else "DEFAULT"
            n_tpl = self.notif_template_input.value.strip() if self.notif_template_input.value else None

            with session_scope() as session:
                service = TaskService(session)
                updated_task = service.edit_task(
                    task_id=self.task_id,
                    changed_by=str(interaction.user.id),
                    reward_per_user=r_user,
                    total_reward_pool=t_pool,
                    notification_type=n_type,
                    custom_notification_template=n_tpl,
                )

                if interaction.guild:
                    await announce_task(updated_task, interaction.guild, interaction.client)
                    await send_admin_log_event(
                        guild=interaction.guild,
                        title="💎 [TASK EDITED — ECONOMICS]",
                        description=(
                            f"<@{interaction.user.id}> edited economics for Task **{updated_task.title}**.\n"
                            f"**Reward/User:** `{updated_task.reward_per_user:,} OBX`\n"
                            f"**Total Pool:** `{updated_task.total_reward_pool:,} OBX`\n"
                            f"**Public Card:** Synchronized in place"
                        ),
                        color=COLOR_GOLD,
                    )

            await interaction.followup.send(
                f"✅ **Economics Updated Successfully!**\nPublic announcement in configured Tasks channel updated in place.",
                ephemeral=True,
            )
        except Exception as exc:
            logger.error("Error submitting task reward edit modal: %s", exc)
            await interaction.followup.send(f"❌ Edit failed: {str(exc)}", ephemeral=True)


class AdminEditTaskPreviewModal(Modal):
    """Modal for manually overriding task preview metadata (Author, Snippet, Image URL)."""
    def __init__(self, task: Task):
        super().__init__(title="🖼️ EDIT TASK PREVIEW")
        self.task_id = str(task.id)

        self.author_input = TextInput(
            label="Preview Author / Handle",
            default=task.preview_author_override or task.preview_author or "",
            placeholder="e.g. BaconCheese21 (@BaconCheese21)",
            max_length=255,
            required=False,
        )
        self.add_item(self.author_input)

        self.text_input = TextInput(
            label="Preview Text / Announcement Snippet",
            default=task.preview_text_override or task.preview_description or "",
            placeholder="Enter the post text or announcement quote to display...",
            style=discord.TextStyle.paragraph,
            max_length=1000,
            required=False,
        )
        self.add_item(self.text_input)

        self.image_input = TextInput(
            label="Preview Image / Banner URL",
            default=task.preview_image_override or task.preview_image_url or "",
            placeholder="https://example.com/banner.png",
            max_length=1000,
            required=False,
        )
        self.add_item(self.image_input)

    async def on_submit(self, interaction: discord.Interaction):
        if not is_admin(interaction):
            await interaction.response.send_message("❌ Permission Denied.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)
        try:
            with session_scope() as session:
                service = TaskService(session)
                auth_val = self.author_input.value.strip() or None
                txt_val = self.text_input.value.strip() or None
                img_val = self.image_input.value.strip() or None

                updated_task = service.edit_task(
                    task_id=self.task_id,
                    changed_by=str(interaction.user.id),
                    preview_author_override=auth_val,
                    preview_text_override=txt_val,
                    preview_image_override=img_val,
                )

                if interaction.guild:
                    await announce_task(updated_task, interaction.guild, interaction.client)
                    await send_admin_log_event(
                        guild=interaction.guild,
                        title="🖼️ [TASK PREVIEW OVERRIDDEN]",
                        description=(
                            f"<@{interaction.user.id}> manually set preview overrides for **{updated_task.title}**.\n"
                            f"**Author:** `{auth_val or 'None'}`\n"
                            f"**Text:** `{txt_val[:60] + '...' if txt_val else 'None'}`\n"
                            f"**Image:** `{img_val or 'None'}`\n"
                            f"**Public Card:** Synchronized in place"
                        ),
                        color=COLOR_TEAL,
                    )

            await interaction.followup.send(
                "✅ **Preview Override Saved!**\nPublic announcement in configured Tasks channel updated in place.",
                ephemeral=True,
            )
        except Exception as exc:
            logger.error("Error submitting task preview override modal: %s", exc)
            await interaction.followup.send(f"❌ Preview override failed: {str(exc)}", ephemeral=True)


class AdminCancelTaskPromptView(View):
    """View presenting cancellation confirmation and pending submission resolution options."""
    def __init__(self, task_id: str, pending_count: int = 0):
        super().__init__(timeout=180)
        self.task_id = task_id
        self.pending_count = pending_count
        self._build_components()

    def _build_components(self):
        self.clear_items()
        if self.pending_count > 0:
            btn_leave = Button(
                label=f"Leave Pending ({self.pending_count})",
                style=discord.ButtonStyle.primary,
                custom_id=f"obx:cnc_act:leave:{self.task_id}",
                row=0,
            )
            btn_leave.callback = self._on_leave_pending
            self.add_item(btn_leave)

            btn_reject = Button(
                label=f"Reject All Pending ({self.pending_count})",
                style=discord.ButtonStyle.danger,
                custom_id=f"obx:cnc_act:reject:{self.task_id}",
                row=0,
            )
            btn_reject.callback = self._on_reject_pending
            self.add_item(btn_reject)
        else:
            btn_confirm = Button(
                label="Confirm Task Cancellation",
                style=discord.ButtonStyle.danger,
                custom_id=f"obx:cnc_act:confirm:{self.task_id}",
                row=0,
            )
            btn_confirm.callback = self._on_confirm_simple
            self.add_item(btn_confirm)

        btn_abort = Button(
            label="Abort / Keep Active",
            style=discord.ButtonStyle.secondary,
            custom_id=f"obx:cnc_act:abort:{self.task_id}",
            row=1,
        )
        btn_abort.callback = self._on_abort
        self.add_item(btn_abort)

    async def _on_abort(self, interaction: discord.Interaction):
        await interaction.response.edit_message(content="↩️ Cancellation aborted. Task remains active.", embed=None, view=None)

    async def _on_confirm_simple(self, interaction: discord.Interaction):
        await self._execute_cancellation(interaction, pending_action="LEAVE_PENDING")

    async def _on_leave_pending(self, interaction: discord.Interaction):
        await self._execute_cancellation(interaction, pending_action="LEAVE_PENDING")

    async def _on_reject_pending(self, interaction: discord.Interaction):
        await self._execute_cancellation(interaction, pending_action="REJECT")

    async def _execute_cancellation(self, interaction: discord.Interaction, pending_action: str):
        await interaction.response.defer(ephemeral=True)
        try:
            with session_scope() as session:
                service = TaskService(session)
                cancelled_task = service.cancel_task(
                    task_id=self.task_id,
                    cancelled_by=str(interaction.user.id),
                    reason="Cancelled by administrator",
                    pending_action=pending_action,
                )

                if interaction.guild:
                    await announce_task(cancelled_task, interaction.guild, interaction.client)
                    await send_admin_log_event(
                        guild=interaction.guild,
                        title="🛑 [TASK CANCELLED]",
                        description=(
                            f"<@{interaction.user.id}> cancelled Task **{cancelled_task.title}**.\n"
                            f"**Pending Submissions Action:** `{pending_action}`\n"
                            f"**Protected Distributed Rewards:** `{cancelled_task.distributed_reward:,} OBX`\n"
                            f"**Public Card:** Updated to `🛑 CANCELLED`"
                        ),
                        color=COLOR_RED,
                    )

            await interaction.edit_original_response(
                content=(
                    f"✅ **Task Successfully Cancelled.**\n\n"
                    f"• Status set to `CANCELLED`\n"
                    f"• Public card updated to `🛑 CANCELLED`\n"
                    f"• Pending action `{pending_action}` applied."
                ),
                embed=None,
                view=None,
            )
        except Exception as exc:
            logger.error("Error executing task cancellation: %s", exc)
            await interaction.edit_original_response(content=f"❌ Cancellation failed: {str(exc)}", embed=None, view=None)


class AdminDeleteTaskConfirmView(View):
    """View confirming permanent deletion of completely unused tasks."""
    def __init__(self, task_id: str, is_safe: bool = True):
        super().__init__(timeout=180)
        self.task_id = task_id
        self.is_safe = is_safe
        self._build_components()

    def _build_components(self):
        self.clear_items()
        task_id = self.task_id
        if self.is_safe:
            btn_conf = Button(
                label="Confirm Permanent Delete",
                style=discord.ButtonStyle.danger,
                custom_id=f"obx:del_act:confirm:{task_id}",
                row=0,
            )
            btn_conf.callback = self._on_confirm
            self.add_item(btn_conf)

        btn_cancel = Button(
            label="Back / Cancel",
            style=discord.ButtonStyle.secondary,
            custom_id=f"obx:del_act:cancel:{task_id}",
            row=0,
        )
        btn_cancel.callback = self._on_cancel
        self.add_item(btn_cancel)

    async def _on_cancel(self, interaction: discord.Interaction):
        await interaction.response.edit_message(content="↩️ Deletion cancelled.", embed=None, view=None)

    async def _on_confirm(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        try:
            with session_scope() as session:
                service = TaskService(session)
                service.safe_delete_task(task_id=self.task_id, deleted_by=str(interaction.user.id))

                if interaction.guild:
                    await send_admin_log_event(
                        guild=interaction.guild,
                        title="🗑️ [TASK DELETED SAFELY]",
                        description=f"<@{interaction.user.id}> safely deleted unused task `{self.task_id}`.",
                        color=COLOR_RED,
                    )
            await interaction.edit_original_response(content="✅ Task permanently deleted from the system.", embed=None, view=None)
        except Exception as exc:
            logger.error("Error in safe delete confirm: %s", exc)
            await interaction.edit_original_response(content=f"❌ Safe deletion blocked: {str(exc)}", embed=None, view=None)


async def handle_admin_manage_tasks(
    interaction: discord.Interaction,
    status_filter: TaskStatus = TaskStatus.ACTIVE,
):
    """Entry point for the Admin Task Management browser."""
    if not is_admin(interaction):
        await interaction.response.send_message("❌ Permission Denied: Administrator role required.", ephemeral=True)
        return

    if not _is_response_done(interaction):
        await interaction.response.defer(ephemeral=True)

    try:
        await render_task_browser(interaction, status_filter=status_filter, page=0)
    except Exception as exc:
        logger.error("Error in handle_admin_manage_tasks: %s", exc)
        await interaction.followup.send("❌ Error loading task management system.", ephemeral=True)
