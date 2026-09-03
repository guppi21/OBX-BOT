import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from contextlib import contextmanager
import discord

from apps.obx_tasks.services.task_service import TaskService
from apps.obx_tasks.bot.views import TaskReviewView, RejectReasonModal
from packages.shared.enums import TaskStatus, SubmissionStatus
from packages.database.models.wallet import Wallet
from packages.database.models.user import User


@contextmanager
def mock_session_scope_for(db_session):
    yield db_session


@pytest.mark.asyncio
async def test_task_review_view_disable_all_items_helper():
    view = TaskReviewView(submission_id="sub-123", submitter_discord_id="user-456")
    assert len(view.children) == 2
    for item in view.children:
        assert item.disabled is False

    view.disable_all_items()
    for item in view.children:
        assert item.disabled is True


@pytest.mark.asyncio
async def test_task_review_view_approve_callback_success_and_disables_items(db_session):
    service = TaskService(db_session)
    task = service.create_task(
        title="View Test Task",
        description="Testing TaskReviewView",
        task_type="LIKE",
        target_url="https://x.com/test",
        reward_per_user=50,
        total_reward_pool=500,
        created_by="admin_999",
    )
    sub = service.submit_task(
        task_id=str(task.id),
        discord_user_id="submitter_888",
        x_username="alice_test",
        proof_url="https://x.com/alice/status/123",
        proof_text="Liked",
    )

    view = TaskReviewView(submission_id=str(sub.id), submitter_discord_id="submitter_888")

    mock_interaction = MagicMock(spec=discord.Interaction)
    mock_interaction.response = AsyncMock()
    mock_interaction.followup = AsyncMock()
    mock_message = AsyncMock()
    mock_interaction.message = mock_message

    mock_admin = MagicMock(spec=discord.Member)
    mock_admin.id = 999999
    mock_admin.guild_permissions.administrator = True
    mock_interaction.user = mock_admin

    with patch("apps.obx_tasks.bot.views.session_scope", lambda: mock_session_scope_for(db_session)):
        await view.approve_button.callback(mock_interaction)

    # Verify view items are now disabled
    for item in view.children:
        assert item.disabled is True

    # Verify message edit was attempted
    mock_message.edit.assert_awaited_once_with(view=view)

    # Verify followup embed confirmed approval
    mock_interaction.followup.send.assert_awaited_once()
    _, kwargs = mock_interaction.followup.send.call_args
    assert "embed" in kwargs
    assert "Submission Approved" in kwargs["embed"].title

    # Verify DB state
    db_session.refresh(sub)
    assert sub.status == SubmissionStatus.APPROVED
    assert sub.reward_amount == 50


@pytest.mark.asyncio
async def test_task_review_view_approve_ui_failure_resilience(db_session):
    """If the message.edit fails, the user is still correctly informed of successful approval."""
    service = TaskService(db_session)
    task = service.create_task(
        title="Resilience Test Task",
        description="Testing UI failure isolation",
        task_type="LIKE",
        target_url="https://x.com/test2",
        reward_per_user=30,
        total_reward_pool=300,
        created_by="admin_999",
    )
    sub = service.submit_task(
        task_id=str(task.id),
        discord_user_id="submitter_777",
        x_username="bob_test",
        proof_url="https://x.com/bob/status/123",
        proof_text="Liked post",
    )

    view = TaskReviewView(submission_id=str(sub.id), submitter_discord_id="submitter_777")

    mock_interaction = MagicMock(spec=discord.Interaction)
    mock_interaction.response = AsyncMock()
    mock_interaction.followup = AsyncMock()
    mock_message = AsyncMock()
    mock_message.edit.side_effect = discord.HTTPException(response=MagicMock(status=500), message="Discord 500 error")
    mock_interaction.message = mock_message

    mock_admin = MagicMock(spec=discord.Member)
    mock_admin.id = 999999
    mock_admin.guild_permissions.administrator = True
    mock_interaction.user = mock_admin

    with patch("apps.obx_tasks.bot.views.session_scope", lambda: mock_session_scope_for(db_session)):
        await view.approve_button.callback(mock_interaction)

    # Verify DB state still successfully committed
    db_session.refresh(sub)
    assert sub.status == SubmissionStatus.APPROVED
    assert sub.reward_amount == 30

    # Verify confirmation was still delivered to the admin
    mock_interaction.followup.send.assert_awaited_once()
    _, kwargs = mock_interaction.followup.send.call_args
    assert "Submission Approved" in kwargs["embed"].title


@pytest.mark.asyncio
async def test_task_review_view_repeated_click_fails_gracefully(db_session):
    """Repeated clicks on an already approved submission cannot double-credit."""
    service = TaskService(db_session)
    task = service.create_task(
        title="Double Click Task",
        description="Testing repeated click",
        task_type="LIKE",
        target_url="https://x.com/test3",
        reward_per_user=20,
        total_reward_pool=200,
        created_by="admin_999",
    )
    sub = service.submit_task(
        task_id=str(task.id),
        discord_user_id="submitter_666",
        x_username="carol_test",
        proof_url="https://x.com/carol/status/123",
        proof_text="Liked",
    )
    # Pre-approve submission once
    service.approve_submission(str(sub.id), reviewer_discord_id="admin_999")

    view = TaskReviewView(submission_id=str(sub.id), submitter_discord_id="submitter_666")

    mock_interaction = MagicMock(spec=discord.Interaction)
    mock_interaction.response = AsyncMock()
    mock_interaction.followup = AsyncMock()
    mock_message = AsyncMock()
    mock_interaction.message = mock_message
    mock_admin = MagicMock(spec=discord.Member)
    mock_admin.id = 999999
    mock_admin.guild_permissions.administrator = True
    mock_interaction.user = mock_admin

    # Click approve on already approved sub
    with patch("apps.obx_tasks.bot.views.session_scope", lambda: mock_session_scope_for(db_session)):
        await view.approve_button.callback(mock_interaction)

    # Verify idempotent response
    mock_interaction.followup.send.assert_awaited_once()
    _, kwargs = mock_interaction.followup.send.call_args
    assert "Submission Approved" in kwargs["embed"].title

    # Verify wallet balance only increased once (20 OBX, not 40 OBX)
    user = db_session.query(User).filter_by(discord_user_id="submitter_666").first()
    wallet = db_session.query(Wallet).filter_by(user_id=user.id).first()
    assert wallet.available_balance == 20


@pytest.mark.asyncio
async def test_task_review_rejection_flow(db_session):
    service = TaskService(db_session)
    task = service.create_task(
        title="Reject Task",
        description="Testing Rejection Modal",
        task_type="LIKE",
        target_url="https://x.com/test4",
        reward_per_user=20,
        total_reward_pool=200,
        created_by="admin_999",
    )
    sub = service.submit_task(
        task_id=str(task.id),
        discord_user_id="submitter_555",
        x_username="dave_test",
        proof_url="https://x.com/dave/status/123",
        proof_text="Proof text",
    )

    view = TaskReviewView(submission_id=str(sub.id), submitter_discord_id="submitter_555")
    modal = RejectReasonModal(submission_id=str(sub.id), parent_view=view)
    modal.reason._value = "Proof link was invalid or deleted."

    mock_interaction = MagicMock(spec=discord.Interaction)
    mock_interaction.response = AsyncMock()
    mock_interaction.followup = AsyncMock()
    mock_interaction.user = MagicMock(spec=discord.Member, id=999999)
    mock_message = AsyncMock()
    mock_interaction.message = mock_message

    with patch("apps.obx_tasks.bot.views.session_scope", lambda: mock_session_scope_for(db_session)):
        await modal.on_submit(mock_interaction)

    db_session.refresh(sub)
    assert sub.status == SubmissionStatus.REJECTED
    assert sub.rejection_reason == "Proof link was invalid or deleted."

    # Verify items are disabled
    for item in view.children:
        assert item.disabled is True
    mock_message.edit.assert_awaited_once_with(view=view)
