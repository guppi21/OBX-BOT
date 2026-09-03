import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from contextlib import contextmanager
import discord

from apps.obx_tasks.services.task_service import TaskService
from apps.obx_core.services.wallet_service import WalletService
from apps.obx_tasks.bot.dashboard_views import (
    OBXDashboardView,
    TaskBrowserView,
    WalletView,
    MySubmissionsView,
    HelpCenterView,
    AdminPanelView,
    TaskSubmitSuccessView,
    handle_home,
    handle_browse_tasks,
    handle_my_wallet,
    handle_my_submissions,
    handle_help_center,
    handle_admin_review,
    handle_admin_health,
)
from apps.obx_tasks.bot.views import TaskSubmitModal, TaskReviewView, MemberRewardView
from packages.shared.enums import TaskStatus, SubmissionStatus
from packages.database.models.task import Task


@contextmanager
def mock_session_scope_for(db_session):
    yield db_session


def test_member_home_dashboard_structure():
    view = OBXDashboardView()
    # Task Center has exactly 4 task-scoped member buttons; no cross-channel nav
    assert len(view.children) == 4
    ids = [b.custom_id for b in view.children]
    assert "obx:dashboard:browse_tasks" in ids
    assert "obx:dashboard:my_balance" in ids
    assert "obx:dashboard:my_submissions" in ids
    assert "obx:dashboard:help_center" in ids
    # Leaderboard and Auctions are in their own dedicated channels now
    assert "obx:dashboard:leaderboard" not in ids
    assert "obx:dashboard:auctions" not in ids
    assert "obx:dashboard:admin_hub" not in ids


@pytest.mark.asyncio
async def test_task_browser_pagination_and_navigation(db_session):
    service = TaskService(db_session)
    t1 = service.create_task("Task 1", "Desc 1", "LIKE", "https://x.com/1", 10, 100, "admin_1")
    t2 = service.create_task("Task 2", "Desc 2", "RETWEET", "https://x.com/2", 20, 200, "admin_1")

    browser = TaskBrowserView(tasks=[t1, t2], current_index=0)
    assert browser.prev_btn.disabled is True
    assert browser.next_btn.disabled is False

    mock_interaction = MagicMock(spec=discord.Interaction)
    mock_interaction.response = AsyncMock()

    # Click next
    await browser.next_btn.callback(mock_interaction)
    assert browser.current_index == 1
    assert browser.prev_btn.disabled is False
    assert browser.next_btn.disabled is True
    mock_interaction.response.edit_message.assert_awaited_once()

    # Click submit proof opens modal
    mock_modal_interaction = MagicMock(spec=discord.Interaction)
    mock_modal_interaction.response = AsyncMock()
    await browser.submit_btn.callback(mock_modal_interaction)
    mock_modal_interaction.response.send_modal.assert_awaited_once()
    modal = mock_modal_interaction.response.send_modal.call_args[0][0]
    assert isinstance(modal, TaskSubmitModal)
    assert modal.task_id == str(t2.id)


@pytest.mark.asyncio
async def test_wallet_view_and_recent_ledger_entries(db_session):
    ws = WalletService(db_session)
    user, wallet, _ = ws.get_or_create_user("user_ux_wallet_1")
    ws.credit(discord_user_id="user_ux_wallet_1", amount=150, reference_type="task_reward", idempotency_key="ux_idem_1")

    mock_interaction = MagicMock(spec=discord.Interaction)
    mock_interaction.user = MagicMock(id="user_ux_wallet_1")
    mock_interaction.response = AsyncMock()
    mock_interaction.followup = AsyncMock()

    with patch("apps.obx_tasks.bot.dashboard_views.session_scope", lambda: mock_session_scope_for(db_session)):
        await handle_my_wallet(mock_interaction)

    mock_interaction.followup.send.assert_awaited_once()
    _, kwargs = mock_interaction.followup.send.call_args
    assert "150 OBX" in kwargs["embed"].fields[0].value
    assert "+150 OBX" in kwargs["embed"].fields[3].value
    assert isinstance(kwargs["view"], WalletView)


@pytest.mark.asyncio
async def test_my_submissions_view_badges(db_session):
    service = TaskService(db_session)
    task = service.create_task("Badge Task", "Desc", "LIKE", "https://x.com/badge", 25, 250, "admin_1")
    service.submit_task(str(task.id), "user_ux_sub_1", "user_x", "https://x.com/proof", "Context proof")

    mock_interaction = MagicMock(spec=discord.Interaction)
    mock_interaction.user = MagicMock(id="user_ux_sub_1")
    mock_interaction.response = AsyncMock()
    mock_interaction.followup = AsyncMock()

    with patch("apps.obx_tasks.bot.dashboard_views.session_scope", lambda: mock_session_scope_for(db_session)):
        await handle_my_submissions(mock_interaction)

    mock_interaction.followup.send.assert_awaited_once()
    _, kwargs = mock_interaction.followup.send.call_args
    assert "Pending Verification" in kwargs["embed"].fields[0].name
    assert isinstance(kwargs["view"], MySubmissionsView)


@pytest.mark.asyncio
async def test_help_center_navigation():
    help_view = HelpCenterView()
    assert len(help_view.children) == 8

    mock_interaction = MagicMock(spec=discord.Interaction)
    mock_interaction.response = AsyncMock()

    # Click getting started
    btn = [b for b in help_view.children if b.label == "Getting Started"][0]
    await btn.callback(mock_interaction)
    mock_interaction.response.edit_message.assert_awaited_once()
    _, kwargs = mock_interaction.response.edit_message.call_args
    assert "Getting Started with OBX" in kwargs["embed"].title


@pytest.mark.asyncio
async def test_admin_panel_permission_and_actions():
    admin_view = AdminPanelView()

    # 1. Non-admin
    mock_non_admin = MagicMock(spec=discord.Interaction)
    mock_non_admin.user = MagicMock(spec=discord.Member)
    mock_non_admin.user.guild_permissions.administrator = False
    mock_non_admin.user.guild_permissions.manage_guild = False
    mock_non_admin.user.roles = []
    mock_non_admin.response = AsyncMock()

    btn = [b for b in admin_view.children if b.label == "Create Task"][0]
    await btn.callback(mock_non_admin)
    mock_non_admin.response.send_message.assert_awaited_once()
    assert "Permission Denied" in mock_non_admin.response.send_message.call_args[0][0]

    # 2. Admin
    mock_admin = MagicMock(spec=discord.Interaction)
    mock_admin.user = MagicMock(spec=discord.Member)
    mock_admin.user.guild_permissions.administrator = True
    mock_admin.response = AsyncMock()

    await btn.callback(mock_admin)
    assert mock_admin.response.send_message.await_count == 1 or mock_admin.response.send_modal.await_count == 1


@pytest.mark.asyncio
async def test_member_reward_notification_resilience(db_session):
    service = TaskService(db_session)
    task = service.create_task("Reward Task", "Desc", "RETWEET", "https://x.com/r", 50, 500, "admin_1")
    sub = service.submit_task(str(task.id), "user_to_reward_1", "user_x", "https://x.com/r_proof", "Proof")

    review_view = TaskReviewView(submission_id=str(sub.id), submitter_discord_id="user_to_reward_1")

    # Mock admin interaction with mocked client and user DM failure (Forbidden)
    mock_interaction = MagicMock(spec=discord.Interaction)
    mock_interaction.user = MagicMock(spec=discord.Member, id="admin_99")
    mock_interaction.user.guild_permissions.administrator = True
    mock_interaction.response = AsyncMock()
    mock_interaction.followup = AsyncMock()
    mock_interaction.message = AsyncMock()

    mock_member = MagicMock(spec=discord.User)
    mock_member.send = AsyncMock(side_effect=discord.Forbidden(MagicMock(), "DMs disabled"))
    mock_interaction.client.get_user.return_value = mock_member

    with patch("apps.obx_tasks.bot.views.session_scope", lambda: mock_session_scope_for(db_session)):
        await review_view.approve_button.callback(mock_interaction)

    # Database operation succeeded and admin got confirmation
    mock_interaction.followup.send.assert_awaited_once()
    _, kwargs = mock_interaction.followup.send.call_args
    assert kwargs["embed"].title == "🎉 Submission Approved!"
    assert "50 OBX" in kwargs["embed"].description
