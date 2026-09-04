import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from contextlib import contextmanager
import discord

from apps.obx_tasks.services.task_service import TaskService
from apps.obx_core.services.wallet_service import WalletService
from apps.obx_tasks.bot.dashboard_views import (
    OBXDashboardView,
    AdminCreateTaskModal,
    TaskBrowserView,
    create_dashboard_embed,
    handle_browse_tasks,
    handle_my_wallet,
)
from apps.obx_tasks.bot.views import TaskSubmitModal
from packages.shared.enums import TaskStatus, SubmissionStatus
from packages.database.models.task import Task


@contextmanager
def mock_session_scope_for(db_session):
    yield db_session


def test_dashboard_view_structure_and_custom_ids():
    view = OBXDashboardView()
    # Exactly 4 task-scoped member buttons; NO leaderboard, auctions, or admin buttons
    assert len(view.children) == 4
    custom_ids = [item.custom_id for item in view.children]
    assert "obx:dashboard:browse_tasks" in custom_ids
    assert "obx:dashboard:my_balance" in custom_ids
    assert "obx:dashboard:my_submissions" in custom_ids
    assert "obx:dashboard:help_center" in custom_ids
    # Cross-channel buttons must NOT appear here — each lives in its own channel
    assert "obx:dashboard:leaderboard" not in custom_ids
    assert "obx:dashboard:auctions" not in custom_ids
    assert "obx:dashboard:admin_hub" not in custom_ids

    embed = create_dashboard_embed()
    assert "TASK CENTER" in embed.title


def test_admin_hub_view_structure_and_custom_ids():
    from apps.obx_tasks.bot.dashboard_views import OBXAdminHubView, create_admin_hub_embed
    admin_view = OBXAdminHubView()
    assert len(admin_view.children) == 11
    custom_ids = [item.custom_id for item in admin_view.children]
    assert "obx:admin:create_task" in custom_ids
    assert "obx:admin:manage_tasks" in custom_ids
    assert "obx:admin:review_queue" in custom_ids
    assert "obx:admin:create_auction" in custom_ids
    assert "obx:admin:members" in custom_ids
    assert "obx:admin:grant_reward" in custom_ids
    assert "obx:admin:configure_channels" in custom_ids
    assert "obx:admin:refresh_public" in custom_ids
    assert "obx:admin:system_health" in custom_ids
    assert "obx:admin:reset_lb" in custom_ids
    assert "obx:admin:refresh_hub" in custom_ids

    embed = create_admin_hub_embed()
    assert "ADMINISTRATIVE CONTROL CENTER" in embed.title


@pytest.mark.asyncio
async def test_dashboard_browse_tasks_button(db_session):
    service = TaskService(db_session)
    service.create_task(
        title="Active Dashboard Task",
        description="Task for dashboard test",
        task_type="LIKE",
        target_url="https://x.com/dash",
        reward_per_user=10,
        total_reward_pool=100,
        created_by="admin_1",
    )

    view = OBXDashboardView()
    mock_interaction = MagicMock(spec=discord.Interaction)
    mock_interaction.response = AsyncMock()
    mock_interaction.followup = AsyncMock()

    with patch("apps.obx_tasks.bot.dashboard_views.session_scope", lambda: mock_session_scope_for(db_session)):
        btn = [b for b in view.children if b.custom_id == "obx:dashboard:browse_tasks"][0]
        await btn.callback(mock_interaction)

    mock_interaction.followup.send.assert_awaited_once()
    _, kwargs = mock_interaction.followup.send.call_args
    assert "view" in kwargs
    assert isinstance(kwargs["view"], TaskBrowserView)
    assert "Active Dashboard Task" in kwargs["embed"].title


@pytest.mark.asyncio
async def test_dashboard_my_balance_button(db_session):
    ws = WalletService(db_session)
    user, wallet, _ = ws.get_or_create_user("user_balance_test_99")
    ws.credit(discord_user_id="user_balance_test_99", amount=75, reference_type="test", idempotency_key="bal_idem_1")

    view = OBXDashboardView()
    mock_interaction = MagicMock(spec=discord.Interaction)
    mock_interaction.user = MagicMock(id="user_balance_test_99")
    mock_interaction.response = AsyncMock()
    mock_interaction.followup = AsyncMock()

    with patch("apps.obx_tasks.bot.dashboard_views.session_scope", lambda: mock_session_scope_for(db_session)):
        btn = [b for b in view.children if b.custom_id == "obx:dashboard:my_balance"][0]
        await btn.callback(mock_interaction)

    mock_interaction.followup.send.assert_awaited_once()
    _, kwargs = mock_interaction.followup.send.call_args
    assert "75 OBX" in kwargs["embed"].fields[0].value


@pytest.mark.asyncio
async def test_dashboard_admin_create_task_permission_and_modal(db_session):
    # 1. Non-admin click modal directly
    modal = AdminCreateTaskModal()
    mock_non_admin = MagicMock(spec=discord.Interaction)
    mock_non_admin.user = MagicMock(spec=discord.Member)
    mock_non_admin.user.guild_permissions.administrator = False
    mock_non_admin.user.guild_permissions.manage_guild = False
    mock_non_admin.user.roles = []
    mock_non_admin.response = AsyncMock()

    await modal.on_submit(mock_non_admin)
    mock_non_admin.response.send_message.assert_awaited_once()
    assert "Permission Denied" in mock_non_admin.response.send_message.call_args[0][0]

    # 2. Admin submit
    mock_admin = MagicMock(spec=discord.Interaction)
    mock_admin.user = MagicMock(spec=discord.Member)
    mock_admin.user.guild_permissions.administrator = True
    mock_admin.response = AsyncMock()
    mock_admin.followup = AsyncMock()

    assert modal.title == "⚡ CREATE TASK"
    assert not hasattr(modal, "task_title")
    modal.target_url._value = "https://x.com/modal_post"
    modal.reward_and_pool._value = "50 / 500"
    modal.deadline._value = "24h"
    modal.instructions._value = "Instructions from modal"

    with patch("apps.obx_tasks.bot.dashboard_views.session_scope", lambda: mock_session_scope_for(db_session)):
        await modal.on_submit(mock_admin)

    mock_admin.followup.send.assert_awaited_once()
    _, kwargs = mock_admin.followup.send.call_args
    assert kwargs["embed"].title == "🎉 Task Created Successfully!"

    # Verify task exists in DB with auto-generated title
    created_task = db_session.query(Task).filter_by(target_url="https://x.com/modal_post").first()
    assert created_task is not None
    assert created_task.title == "Like Target Post"
    assert created_task.total_reward_pool == 500
    assert created_task.reward_per_user == 50
    assert created_task.max_approvals == 10
