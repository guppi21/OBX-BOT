import pytest
from unittest.mock import MagicMock, AsyncMock, patch
import discord

from packages.shared.enums import TaskStatus, TaskType
from packages.database.models.task import Task
from apps.obx_tasks.bot.task_management_views import (
    AdminTaskBrowserView,
    AdminTaskDetailView,
    build_task_browser_embed,
    build_task_detail_embed,
    handle_admin_mgmt_interaction,
    handle_admin_manage_tasks,
    render_task_browser,
    render_task_detail,
    render_admin_hub,
)
from apps.obx_tasks.services.task_service import TaskService


def create_mock_task(i: int, status=TaskStatus.ACTIVE) -> Task:
    task = MagicMock(spec=Task)
    task.id = f"00000000-0000-0000-0000-00000000000{i:x}"
    task.title = f"Campaign Task {i}"
    task.description = f"Instructions for task {i}"
    task.task_type = TaskType.LIKE
    task.platform = "X"
    task.target_url = f"https://x.com/user/status/{i}"
    task.reward_per_user = 10 * i
    task.total_reward_pool = 100 * i
    task.distributed_reward = 0
    task.max_approvals = 10
    task.approved_count = 0
    task.status = status
    task.ends_at = None
    task.proof_required = True
    task.allow_image_proof = True
    task.notification_type = "DEFAULT"
    task.cancellation_reason = None
    return task


def test_task_browser_embed_and_components():
    tasks = [create_mock_task(i) for i in range(1, 19)]  # 18 tasks
    view = AdminTaskBrowserView(tasks=tasks, status_filter=TaskStatus.ACTIVE, current_page=0)
    embed = view.get_current_embed()

    # 1. Shows Page 1 of 4, total 18
    assert "Page 1 of 4" in embed.description
    assert "18" in embed.description
    assert "Campaign Task 1" in embed.description
    assert "Campaign Task 5" in embed.description

    # 2. Components check
    # Row 0: Select task dropdown with 5 options
    select_items = [item for item in view.children if isinstance(item, discord.ui.Select)]
    assert len(select_items) == 2  # Task Select + Status Filter

    task_select = [s for s in select_items if "select_task" in s.custom_id][0]
    assert len(task_select.options) == 5
    assert "Campaign Task 1" in task_select.options[0].label

    # Row 2: Page prev (disabled), Page next (enabled)
    prev_btn = [b for b in view.children if isinstance(b, discord.ui.Button) and "page_prev" in b.custom_id][0]
    next_btn = [b for b in view.children if isinstance(b, discord.ui.Button) and "page_next" in b.custom_id][0]
    assert prev_btn.disabled is True
    assert next_btn.disabled is False

    # Row 3: Back to Admin Hub
    hub_btn = [b for b in view.children if isinstance(b, discord.ui.Button) and "obx:mgmt:hub" in b.custom_id][0]
    assert hub_btn.label == "Back to Admin Hub"


def test_task_detail_previous_disabled_on_first_task():
    tasks = [create_mock_task(i) for i in range(1, 19)]
    view = AdminTaskDetailView(
        task=tasks[0],
        status_filter=TaskStatus.ACTIVE,
        current_index=0,
        total_tasks=18,
        current_page=0,
    )
    embed = view.get_current_embed()
    assert "Task 1 of 18" in embed.footer.text

    prev_btn = [b for b in view.children if "obx:mgmt:prev" in getattr(b, "custom_id", "")][0]
    next_btn = [b for b in view.children if "obx:mgmt:next" in getattr(b, "custom_id", "")][0]
    back_list_btn = [b for b in view.children if "obx:mgmt:back_to_list" in getattr(b, "custom_id", "")][0]
    back_hub_btn = [b for b in view.children if "obx:mgmt:hub" in getattr(b, "custom_id", "")][0]

    # Previous is disabled on first task
    assert prev_btn.disabled is True
    # Next is enabled
    assert next_btn.disabled is False
    # Back to list and Back to Admin Hub present
    assert back_list_btn.label == "Back to Task List"
    assert back_hub_btn.label == "Back to Admin Hub"


def test_task_detail_next_disabled_on_last_task():
    tasks = [create_mock_task(i) for i in range(1, 19)]
    view = AdminTaskDetailView(
        task=tasks[17],
        status_filter=TaskStatus.ACTIVE,
        current_index=17,
        total_tasks=18,
        current_page=3,
    )
    embed = view.get_current_embed()
    assert "Task 18 of 18" in embed.footer.text

    prev_btn = [b for b in view.children if "obx:mgmt:prev" in getattr(b, "custom_id", "")][0]
    next_btn = [b for b in view.children if "obx:mgmt:next" in getattr(b, "custom_id", "")][0]

    # Previous is enabled
    assert prev_btn.disabled is False
    # Next is disabled on last task
    assert next_btn.disabled is True


def test_task_detail_middle_task_has_both_buttons_enabled():
    tasks = [create_mock_task(i) for i in range(1, 19)]
    view = AdminTaskDetailView(
        task=tasks[4],  # Task 5 of 18
        status_filter=TaskStatus.ACTIVE,
        current_index=4,
        total_tasks=18,
        current_page=0,
    )
    embed = view.get_current_embed()
    assert "Task 5 of 18" in embed.footer.text

    prev_btn = [b for b in view.children if "obx:mgmt:prev" in getattr(b, "custom_id", "")][0]
    next_btn = [b for b in view.children if "obx:mgmt:next" in getattr(b, "custom_id", "")][0]

    assert prev_btn.disabled is False
    assert next_btn.disabled is False


@pytest.mark.asyncio
async def test_navigation_router_handles_next_task_in_place(db_session):
    mock_interaction = MagicMock(spec=discord.Interaction)
    mock_interaction.response = AsyncMock()
    mock_interaction.response.is_done.return_value = False
    mock_interaction.user = MagicMock(id=111222)

    tasks = [create_mock_task(i) for i in range(1, 6)]

    with patch("apps.obx_tasks.bot.task_management_views.is_admin", return_value=True), \
         patch("apps.obx_tasks.services.task_service.TaskService.list_tasks", return_value=(tasks, len(tasks))):

        # Click Next from Task 1 (index=0) -> moves to Task 2 (index=1)
        await handle_admin_mgmt_interaction(mock_interaction, "obx:mgmt:next:ACTIVE:0:0")

    mock_interaction.response.edit_message.assert_awaited_once()
    call_args = mock_interaction.response.edit_message.call_args[1]
    embed = call_args["embed"]
    assert "Task 2 of 5" in embed.footer.text
    assert "Campaign Task 2" in embed.title


@pytest.mark.asyncio
async def test_navigation_router_handles_prev_task_in_place(db_session):
    mock_interaction = MagicMock(spec=discord.Interaction)
    mock_interaction.response = AsyncMock()
    mock_interaction.response.is_done.return_value = False
    mock_interaction.user = MagicMock(id=111222)

    tasks = [create_mock_task(i) for i in range(1, 6)]

    with patch("apps.obx_tasks.bot.task_management_views.is_admin", return_value=True), \
         patch("apps.obx_tasks.services.task_service.TaskService.list_tasks", return_value=(tasks, len(tasks))):

        # Click Prev from Task 3 (index=2) -> moves to Task 2 (index=1)
        await handle_admin_mgmt_interaction(mock_interaction, "obx:mgmt:prev:ACTIVE:0:2")

    mock_interaction.response.edit_message.assert_awaited_once()
    call_args = mock_interaction.response.edit_message.call_args[1]
    embed = call_args["embed"]
    assert "Task 2 of 5" in embed.footer.text
    assert "Campaign Task 2" in embed.title


@pytest.mark.asyncio
async def test_navigation_router_back_to_task_list(db_session):
    mock_interaction = MagicMock(spec=discord.Interaction)
    mock_interaction.response = AsyncMock()
    mock_interaction.response.is_done.return_value = False
    mock_interaction.user = MagicMock(id=111222)

    tasks = [create_mock_task(i) for i in range(1, 19)]

    with patch("apps.obx_tasks.bot.task_management_views.is_admin", return_value=True), \
         patch("apps.obx_tasks.services.task_service.TaskService.list_tasks", return_value=(tasks, len(tasks))):

        # Click Back to Task List from Page 2
        await handle_admin_mgmt_interaction(mock_interaction, "obx:mgmt:back_to_list:ACTIVE:2")

    mock_interaction.response.edit_message.assert_awaited_once()
    call_args = mock_interaction.response.edit_message.call_args[1]
    embed = call_args["embed"]
    assert "Page 3 of 4" in embed.description
    assert isinstance(call_args["view"], AdminTaskBrowserView)


@pytest.mark.asyncio
async def test_navigation_router_back_to_admin_hub():
    mock_interaction = MagicMock(spec=discord.Interaction)
    mock_interaction.response = AsyncMock()
    mock_interaction.response.is_done.return_value = False
    mock_interaction.user = MagicMock(id=111222)

    with patch("apps.obx_tasks.bot.task_management_views.is_admin", return_value=True):
        await handle_admin_mgmt_interaction(mock_interaction, "obx:mgmt:hub")

    mock_interaction.response.edit_message.assert_awaited_once()
    call_args = mock_interaction.response.edit_message.call_args[1]
    embed = call_args["embed"]
    assert "OBX ADMINISTRATIVE COMMAND CENTER" in embed.title or "ADMIN" in embed.title


@pytest.mark.asyncio
async def test_select_task_from_browser_opens_detail_view():
    mock_interaction = MagicMock(spec=discord.Interaction)
    mock_interaction.response = AsyncMock()
    mock_interaction.response.is_done.return_value = False
    mock_interaction.user = MagicMock(id=111222)
    mock_interaction.data = {"values": ["4"]}  # Selected task index 4 (Task 5)

    tasks = [create_mock_task(i) for i in range(1, 10)]

    with patch("apps.obx_tasks.bot.task_management_views.is_admin", return_value=True), \
         patch("apps.obx_tasks.services.task_service.TaskService.list_tasks", return_value=(tasks, len(tasks))):

        await handle_admin_mgmt_interaction(mock_interaction, "obx:mgmt:select_task:ACTIVE:0")

    mock_interaction.response.edit_message.assert_awaited_once()
    call_args = mock_interaction.response.edit_message.call_args[1]
    embed = call_args["embed"]
    assert "Task 5 of 9" in embed.footer.text
    assert "Campaign Task 5" in embed.title
    assert isinstance(call_args["view"], AdminTaskDetailView)


@pytest.mark.asyncio
async def test_status_filter_change_updates_browser():
    mock_interaction = MagicMock(spec=discord.Interaction)
    mock_interaction.response = AsyncMock()
    mock_interaction.response.is_done.return_value = False
    mock_interaction.user = MagicMock(id=111222)
    mock_interaction.data = {"values": ["PAUSED"]}

    paused_tasks = [create_mock_task(1, status=TaskStatus.PAUSED)]

    with patch("apps.obx_tasks.bot.task_management_views.is_admin", return_value=True), \
         patch("apps.obx_tasks.services.task_service.TaskService.list_tasks", return_value=(paused_tasks, 1)):

        await handle_admin_mgmt_interaction(mock_interaction, "obx:mgmt:filter_status:0")

    mock_interaction.response.edit_message.assert_awaited_once()
    call_args = mock_interaction.response.edit_message.call_args[1]
    embed = call_args["embed"]
    assert "Paused Tasks" in embed.title
    assert "Campaign Task 1" in embed.description
