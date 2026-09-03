import pytest
from unittest.mock import MagicMock
import discord

from apps.obx_tasks.bot.dashboard_views import AdminCreateTaskModal, AdminQueueRejectModal
from apps.obx_tasks.bot.auction_views import (
    AdminCreateAuctionModal,
    AdminEditAuctionModal,
    GTDBidModal,
    ManualDescriptionModal,
    AdminGrantRewardModal,
)
from apps.obx_tasks.bot.task_management_views import (
    AdminEditTaskMetadataModal,
    AdminEditTaskRewardModal,
    AdminEditTaskPreviewModal,
)
from apps.obx_tasks.bot.views import TaskSubmitModal, RejectReasonModal
from apps.obx_tasks.bot.join_raid_views import SetTwitterModal


def test_all_modals_have_hardcoded_inbuilt_titles():
    """Every modal in the bot must have a hardcoded, inbuilt title."""
    mock_task = MagicMock(
        id="123",
        title="Sample Task",
        description="Sample Desc",
        task_type=MagicMock(value="LIKE"),
        target_url="https://x.com/sample",
        ends_at=None,
        reward_per_user=10,
        total_reward_pool=100,
        notification_type="DEFAULT",
        custom_notification_template=None,
        preview_author=None,
        preview_author_override=None,
        preview_text_override=None,
        preview_description=None,
        preview_image_override=None,
        preview_image_url=None,
    )
    mock_auction = MagicMock(
        id="456",
        title="Sample Auction",
        description="Sample Desc",
        project_x_url="https://x.com/sample",
        total_slots=5,
        price_or_min_bid=10,
    )

    modals = [
        ("Task creation", AdminCreateTaskModal(), "⚡ CREATE TASK"),
        ("Auction creation", AdminCreateAuctionModal(), "🎟️ LAUNCH WHITELIST AUCTION"),
        ("Proof submission", TaskSubmitModal(task_id="t1", task_title="Task"), "📎 SUBMIT PROOF"),
        ("Set X Account", SetTwitterModal(), "🐦 SET X ACCOUNT"),
        ("GTD Bid", GTDBidModal(auction_id="a1", auction_title="Auction", min_bid=10), "💎 PLACE / UPDATE BID"),
        ("Edit auction", AdminEditAuctionModal(auction=mock_auction), "✏️ EDIT WHITELIST AUCTION"),
        ("Edit task metadata", AdminEditTaskMetadataModal(task=mock_task), "✏️ EDIT TASK"),
        ("Edit task reward", AdminEditTaskRewardModal(task=mock_task), "💎 EDIT TASK ECONOMICS"),
        ("Edit task preview", AdminEditTaskPreviewModal(task=mock_task), "🖼️ EDIT TASK PREVIEW"),
        ("Manual description", ManualDescriptionModal(payload={}), "✏️ PROJECT DESCRIPTION"),
        ("Grant custom reward", AdminGrantRewardModal(), "🎁 GRANT CUSTOM REWARD"),
        ("Queue reject", AdminQueueRejectModal(queue_view=MagicMock(), submission_id="s1", submitter_id="u1"), "❌ REJECT SUBMISSION"),
        ("Review reject", RejectReasonModal(submission_id="s1"), "❌ REJECT SUBMISSION"),
    ]

    for name, modal, expected_title in modals:
        assert modal.title == expected_title, f"{name} modal title mismatch: expected {expected_title!r}, got {modal.title!r}"


def test_no_modal_has_user_editable_title_field():
    """No bot modal may ask the user for a Title, Task Title, Auction Title, Form Title, or Heading."""
    mock_task = MagicMock(
        id="123",
        title="Sample Task",
        description="Sample Desc",
        task_type=MagicMock(value="LIKE"),
        target_url="https://x.com/sample",
        ends_at=None,
        reward_per_user=10,
        total_reward_pool=100,
        notification_type="DEFAULT",
        custom_notification_template=None,
        preview_author=None,
        preview_author_override=None,
        preview_text_override=None,
        preview_description=None,
        preview_image_override=None,
        preview_image_url=None,
    )
    mock_auction = MagicMock(
        id="456",
        title="Sample Auction",
        description="Sample Desc",
        project_x_url="https://x.com/sample",
        total_slots=5,
        price_or_min_bid=10,
    )

    all_modals = [
        AdminCreateTaskModal(),
        AdminCreateAuctionModal(),
        TaskSubmitModal(task_id="t1", task_title="Task"),
        SetTwitterModal(),
        GTDBidModal(auction_id="a1", auction_title="Auction", min_bid=10),
        AdminEditAuctionModal(auction=mock_auction),
        AdminEditTaskMetadataModal(task=mock_task),
        AdminEditTaskRewardModal(task=mock_task),
        AdminEditTaskPreviewModal(task=mock_task),
        ManualDescriptionModal(payload={}),
        AdminGrantRewardModal(),
        AdminQueueRejectModal(queue_view=MagicMock(), submission_id="s1", submitter_id="u1"),
        RejectReasonModal(submission_id="s1"),
    ]

    disallowed_keywords = ["title", "heading", "form title"]

    for modal in all_modals:
        for child in modal.children:
            label = getattr(child, "label", "").lower()
            for keyword in disallowed_keywords:
                assert keyword not in label, f"Modal {modal.title} has forbidden editable naming field: label={child.label!r}"
