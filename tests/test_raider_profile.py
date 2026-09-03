import pytest
import uuid
from datetime import datetime, timezone
from unittest.mock import MagicMock, AsyncMock, patch
from contextlib import contextmanager
import discord

from packages.database.session import session_scope
from packages.database.models.task import Task
from packages.database.models.raider_profile import RaiderProfile
from packages.shared.enums import TaskStatus
from apps.obx_tasks.services.task_service import TaskService
from apps.obx_tasks.services.raider_service import RaiderService, normalize_twitter_input
from apps.obx_tasks.bot.views import TaskSubmitModal
from apps.obx_tasks.bot.join_raid_views import (
    handle_join_raid_click,
    handle_activate_join_raid_click,
    SetTwitterModal,
)
from apps.obx_tasks.bot.permissions import check_raider_access


@contextmanager
def mock_session_scope_for(session):
    yield session


def test_twitter_input_normalization():
    """All accepted formats normalize cleanly to handle and profile URL."""
    cases = [
        ("@BaconCheese", "BaconCheese", "https://x.com/BaconCheese"),
        ("BaconCheese", "BaconCheese", "https://x.com/BaconCheese"),
        ("x.com/BaconCheese", "BaconCheese", "https://x.com/BaconCheese"),
        ("twitter.com/BaconCheese", "BaconCheese", "https://x.com/BaconCheese"),
        ("https://x.com/BaconCheese", "BaconCheese", "https://x.com/BaconCheese"),
        ("https://twitter.com/BaconCheese", "BaconCheese", "https://x.com/BaconCheese"),
        ("https://x.com/@BaconCheese?s=20", "BaconCheese", "https://x.com/BaconCheese"),
        ("@@@BaconCheese", "BaconCheese", "https://x.com/BaconCheese"),
        ("https://fxtwitter.com/BaconCheese/", "BaconCheese", "https://x.com/BaconCheese"),
    ]

    for raw, exp_handle, exp_url in cases:
        h, u = normalize_twitter_input(raw)
        assert h == exp_handle, f"Failed for {raw}: got handle {h}"
        assert u == exp_url, f"Failed for {raw}: got url {u}"

    # Invalid inputs
    with pytest.raises(ValueError):
        normalize_twitter_input("")
    with pytest.raises(ValueError):
        normalize_twitter_input("   ")
    with pytest.raises(ValueError):
        normalize_twitter_input("invalid username with spaces")
    with pytest.raises(ValueError):
        normalize_twitter_input("!@#$%^&*")


def test_raider_service_crud_and_uniqueness(db_session):
    """Raider profile management, handle uniqueness, and admin override."""
    service = RaiderService(db_session)

    # 1. User 1 registers handle
    p1 = service.set_raider_twitter("discord_u1", "@SatoshiNakamoto")
    assert p1.twitter_handle == "SatoshiNakamoto"
    assert p1.twitter_profile_url == "https://x.com/SatoshiNakamoto"

    # 2. User 1 updates their own handle
    p1_updated = service.set_raider_twitter("discord_u1", "https://x.com/Satoshi_BTC")
    assert p1_updated.twitter_handle == "Satoshi_BTC"

    # 3. User 2 attempts to register same handle -> blocked
    with pytest.raises(ValueError) as exc:
        service.set_raider_twitter("discord_u2", "@Satoshi_BTC")
    assert "already registered" in str(exc.value)

    # 4. User 2 registers with admin override -> succeeds
    p2 = service.set_raider_twitter("discord_u2", "@Satoshi_BTC", admin_override=True)
    assert p2.twitter_handle == "Satoshi_BTC"
    assert p2.discord_user_id == "discord_u2"

    # User 1 handle was reassigned/removed
    p1_check = service.get_raider_profile("discord_u1")
    assert p1_check is None

    # 5. Remove profile
    assert service.remove_raider_twitter("discord_u2") is True
    assert service.get_raider_profile("discord_u2") is None


@pytest.mark.asyncio
async def test_simplified_task_submit_modal(db_session):
    """TaskSubmitModal asks only for Proof Link, auto-attaches registered X handle."""
    # Seed task
    task_service = TaskService(db_session)
    task = task_service.create_task(
        title="Minimal Raid Task",
        description="Like the post",
        task_type="LIKE",
        target_url="https://x.com/sample/status/1",
        reward_per_user=20,
        total_reward_pool=100,
        created_by="admin_test",
    )

    # Seed Raider Profile
    r_service = RaiderService(db_session)
    r_service.set_raider_twitter("u_submitter_1", "@SubMaster")

    # Open modal
    modal = TaskSubmitModal(task_id=str(task.id), task_title=task.title)
    # Only 1 input in the UI
    assert len(modal.children) == 1
    assert modal.proof_url.label == "Proof Link"

    modal.proof_url._value = "https://x.com/SubMaster/status/999888"

    mock_intr = MagicMock(spec=discord.Interaction)
    mock_intr.user.id = "u_submitter_1"
    mock_intr.user.name = "SubMasterDiscord"
    mock_intr.guild = None
    mock_intr.response = AsyncMock()
    mock_intr.followup = AsyncMock()

    with patch("apps.obx_tasks.bot.views.session_scope", lambda: mock_session_scope_for(db_session)):
        await modal.on_submit(mock_intr)

    mock_intr.followup.send.assert_awaited_once()
    send_kw = mock_intr.followup.send.call_args[1]
    embed = send_kw["embed"]

    # Minimal Embed verification
    assert embed.title == "📨 PROOF SUBMITTED"
    assert "Thank you for your submission!" in embed.description
    assert "Task:" not in embed.description
    assert "Estimated Reward: 20 OBX" in embed.description
    assert "A moderator will review your proof." in embed.description
    assert "You'll receive a DM when approved." in embed.description

    # Zero database IDs or transaction hashes visible
    assert str(task.id) not in embed.description
    assert "Transaction ID" not in embed.description
    assert "Ledger ID" not in embed.description

    # No unnecessary buttons
    assert "view" not in send_kw or send_kw["view"] is None


@pytest.mark.asyncio
async def test_join_raid_onboarding_state_machine(db_session):
    """Verify all 4 states of the Join Raid flow."""
    mock_guild = MagicMock(spec=discord.Guild, id="g_test")
    mock_role = MagicMock(id=998877, name="⚡ OBX Raider")
    mock_guild.get_role.return_value = mock_role
    mock_guild.roles = [mock_role]

    mock_member = MagicMock(spec=discord.Member, id="u_onboarding", roles=[])
    mock_member.add_roles = AsyncMock()
    mock_guild.get_member.return_value = mock_member

    mock_intr = MagicMock(spec=discord.Interaction)
    mock_intr.user = mock_member
    mock_intr.guild = mock_guild
    mock_intr.response = MagicMock()
    mock_intr.response.send_message = AsyncMock()
    mock_intr.response.is_done = MagicMock(return_value=False)
    mock_intr.followup = AsyncMock()

    r_service = RaiderService(db_session)

    # State 1: No role, No Twitter -> shows Set X Account and JOIN RAID
    with patch("apps.obx_tasks.bot.join_raid_views.session_scope", lambda: mock_session_scope_for(db_session)), \
         patch("apps.obx_tasks.bot.join_raid_views.has_raider_role", return_value=False):
        await handle_join_raid_click(mock_intr)

    mock_intr.response.send_message.assert_awaited_once()
    kw1 = mock_intr.response.send_message.call_args[1]
    assert kw1["embed"].title == "⚔️ JOIN THE OBX RAID"
    labels1 = [b.label for b in kw1["view"].children if hasattr(b, "label")]
    assert "Set X Account" in labels1
    assert "JOIN RAID" in labels1

    # State 2: User clicks Join Raid before setting Twitter -> blocked
    mock_intr.response.reset_mock()
    with patch("apps.obx_tasks.bot.join_raid_views.session_scope", lambda: mock_session_scope_for(db_session)):
        await handle_activate_join_raid_click(mock_intr)
    assert "Set your X account first" in mock_intr.response.send_message.call_args[0][0]

    # State 3: User registers Twitter -> now has Twitter, but not role yet
    r_service.set_raider_twitter("u_onboarding", "@RaiderAlpha")
    mock_intr.response.reset_mock()
    with patch("apps.obx_tasks.bot.join_raid_views.session_scope", lambda: mock_session_scope_for(db_session)), \
         patch("apps.obx_tasks.bot.join_raid_views.has_raider_role", return_value=False), \
         patch("apps.obx_tasks.bot.join_raid_views.get_settings") as s:
        s.return_value.RAID_ROLE_ID = "998877"
        await handle_activate_join_raid_click(mock_intr)

    # Role granted!
    mock_member.add_roles.assert_awaited_once()
    kw3 = mock_intr.response.send_message.call_args[1]
    assert kw3["embed"].title == "⚔️ YOU'RE IN"
    assert "@RaiderAlpha" in kw3["embed"].description
    labels3 = [b.label for b in kw3["view"].children if hasattr(b, "label")]
    assert "EDIT TWITTER" in labels3

    # State 4: User already has role and has Twitter
    mock_member.roles = [mock_role]
    mock_intr.response.reset_mock()
    with patch("apps.obx_tasks.bot.join_raid_views.session_scope", lambda: mock_session_scope_for(db_session)), \
         patch("apps.obx_tasks.bot.join_raid_views.has_raider_role", return_value=True):
        await handle_join_raid_click(mock_intr)

    kw4 = mock_intr.response.send_message.call_args[1]
    assert kw4["embed"].title == "⚔️ OBX RAIDER ACTIVE"
    assert "@RaiderAlpha" in kw4["embed"].description
    labels4 = [b.label for b in kw4["view"].children if hasattr(b, "label")]
    assert "EDIT TWITTER" in labels4
    assert "JOIN RAID" not in labels4


@pytest.mark.asyncio
async def test_check_raider_access_enforces_role_and_twitter(db_session):
    """check_raider_access verifies both role and twitter before every action."""
    mock_member = MagicMock(spec=discord.Member, id="u_gate_test")
    mock_intr = MagicMock(spec=discord.Interaction)
    mock_intr.user = mock_member
    mock_intr.guild = MagicMock()
    mock_intr.response = MagicMock()
    mock_intr.response.send_message = AsyncMock()
    mock_intr.response.is_done = MagicMock(return_value=False)

    r_service = RaiderService(db_session)

    # 1. Missing both
    with patch("apps.obx_tasks.bot.permissions.session_scope", lambda: mock_session_scope_for(db_session)), \
         patch("apps.obx_tasks.bot.permissions.has_raider_role", return_value=False), \
         patch("apps.obx_tasks.bot.join_raid_views.has_raider_role", return_value=False), \
         patch("apps.obx_tasks.bot.permissions.is_admin", return_value=False), \
         patch("apps.obx_tasks.bot.join_raid_views.session_scope", lambda: mock_session_scope_for(db_session)):
        allowed = await check_raider_access(mock_intr)
        assert allowed is False
        mock_intr.response.send_message.assert_awaited_once()
        assert "JOIN THE OBX RAID" in mock_intr.response.send_message.call_args[1]["embed"].title

    # 2. Has role, missing Twitter
    mock_intr.response.reset_mock()
    with patch("apps.obx_tasks.bot.permissions.session_scope", lambda: mock_session_scope_for(db_session)), \
         patch("apps.obx_tasks.bot.permissions.has_raider_role", return_value=True), \
         patch("apps.obx_tasks.bot.join_raid_views.has_raider_role", return_value=True), \
         patch("apps.obx_tasks.bot.permissions.is_admin", return_value=False), \
         patch("apps.obx_tasks.bot.join_raid_views.session_scope", lambda: mock_session_scope_for(db_session)):
        allowed = await check_raider_access(mock_intr)
        assert allowed is False
        mock_intr.response.send_message.assert_awaited_once()
        assert "CONNECT YOUR X ACCOUNT" in mock_intr.response.send_message.call_args[1]["embed"].title

    # 3. Missing role, has Twitter
    r_service.set_raider_twitter("u_gate_test", "@GateHolder")
    mock_intr.response.reset_mock()
    with patch("apps.obx_tasks.bot.permissions.session_scope", lambda: mock_session_scope_for(db_session)), \
         patch("apps.obx_tasks.bot.permissions.has_raider_role", return_value=False), \
         patch("apps.obx_tasks.bot.join_raid_views.has_raider_role", return_value=False), \
         patch("apps.obx_tasks.bot.permissions.is_admin", return_value=False), \
         patch("apps.obx_tasks.bot.join_raid_views.session_scope", lambda: mock_session_scope_for(db_session)), \
         patch("apps.obx_tasks.bot.join_raid_views.grant_raider_role_to_member", AsyncMock(return_value=(True, "OK"))):
        allowed = await check_raider_access(mock_intr)
        assert allowed is False

    # 4. Has both role and Twitter -> Access Allowed!
    with patch("apps.obx_tasks.bot.permissions.session_scope", lambda: mock_session_scope_for(db_session)), \
         patch("apps.obx_tasks.bot.permissions.has_raider_role", return_value=True), \
         patch("apps.obx_tasks.bot.permissions.is_admin", return_value=False):
        allowed = await check_raider_access(mock_intr)
        assert allowed is True


@pytest.mark.asyncio
async def test_set_twitter_modal_saves_and_confirms(db_session):
    """SetTwitterModal normalizes handle, saves to DB, and returns minimal confirmation."""
    modal = SetTwitterModal()
    assert len(modal.children) == 1
    assert modal.x_input.label == "X Handle or Profile URL"

    modal.x_input._value = "https://x.com/StarRaider_99"

    mock_intr = MagicMock(spec=discord.Interaction)
    mock_intr.user.id = "u_twitter_modal_test"
    mock_intr.response = AsyncMock()
    mock_intr.followup = AsyncMock()

    with patch("apps.obx_tasks.bot.join_raid_views.session_scope", lambda: mock_session_scope_for(db_session)):
        await modal.on_submit(mock_intr)

    # Verify saved in database
    r_service = RaiderService(db_session)
    prof = r_service.get_raider_profile("u_twitter_modal_test")
    assert prof is not None
    assert prof.twitter_handle == "StarRaider_99"

    # Verify confirmation embed
    mock_intr.followup.send.assert_awaited_once()
    send_kw = mock_intr.followup.send.call_args[1]
    embed = send_kw["embed"]
    assert embed.title == "🐦 X ACCOUNT CONNECTED"
    assert "@StarRaider_99" in embed.description
    assert "Your raid account is ready." in embed.description


@pytest.mark.asyncio
async def test_send_approval_dm_format_and_idempotence(db_session):
    """send_approval_dm sends minimal DM and prevents duplicate sends."""
    from apps.obx_tasks.bot.notification_service import send_approval_dm
    from packages.database.models.submission import TaskSubmission
    from packages.shared.enums import SubmissionStatus

    task_service = TaskService(db_session)
    task = task_service.create_task(
        title="Raid Approval Test",
        description="Like and RT",
        task_type="RETWEET",
        target_url="https://x.com/test/1",
        reward_per_user=50,
        total_reward_pool=500,
        created_by="admin",
    )

    r_service = RaiderService(db_session)
    r_service.set_raider_twitter("1234567890", "RaidWinner")

    sub = task_service.submit_task(
        task_id=str(task.id),
        discord_user_id="1234567890",
        x_username="RaidWinner",
        proof_url="https://x.com/RaidWinner/status/123",
        proof_text="Proof Link",
    )
    sub.reward_amount = 50
    sub.status = SubmissionStatus.APPROVED
    db_session.commit()

    mock_user = MagicMock(spec=discord.User)
    mock_user.send = AsyncMock()

    mock_bot = MagicMock(spec=discord.Client)
    mock_bot.get_user = MagicMock(return_value=mock_user)
    mock_bot.fetch_user = AsyncMock(return_value=mock_user)

    with patch("apps.obx_tasks.bot.notification_service.session_scope", lambda: mock_session_scope_for(db_session)):
        sent1 = await send_approval_dm(mock_bot, sub, new_balance=150)
        assert sent1 is True

        mock_user.send.assert_awaited_once()
        dm_embed = mock_user.send.call_args[1]["embed"]
        assert dm_embed.title == "🎉 CONGRATULATIONS!"
        assert "Your submission has been approved." in dm_embed.description
        assert "Raid Approval Test" not in dm_embed.description
    assert "+50 OBX" in dm_embed.description
    assert "150 OBX" in dm_embed.description
    assert "Keep raiding. ⚡" in dm_embed.description

    # Ensure no internal IDs or task titles leaked
    assert str(sub.id) not in dm_embed.description
    assert str(task.id) not in dm_embed.description

    # Second call for the same submission must be idempotent (no duplicate DM)
    mock_user.send.reset_mock()
    with patch("apps.obx_tasks.bot.notification_service.session_scope", lambda: mock_session_scope_for(db_session)):
        sent2 = await send_approval_dm(mock_bot, sub, new_balance=150)
        assert sent2 is False
    mock_user.send.assert_not_awaited()


@pytest.mark.asyncio
async def test_send_approval_dm_forbidden_handled(db_session):
    """send_approval_dm safely handles discord.Forbidden when user DMs are closed."""
    from apps.obx_tasks.bot.notification_service import send_approval_dm
    from packages.shared.enums import SubmissionStatus

    task_service = TaskService(db_session)
    task = task_service.create_task(
        title="Raid DM Closed Test",
        description="Like",
        task_type="LIKE",
        target_url="https://x.com/test/2",
        reward_per_user=25,
        total_reward_pool=250,
        created_by="admin",
    )

    r_service = RaiderService(db_session)
    r_service.set_raider_twitter("9999999999", "ClosedDMUser")

    sub = task_service.submit_task(
        task_id=str(task.id),
        discord_user_id="9999999999",
        x_username="ClosedDMUser",
        proof_url="https://x.com/ClosedDMUser/status/456",
        proof_text="Proof Link",
    )
    sub.reward_amount = 25
    sub.status = SubmissionStatus.APPROVED
    db_session.commit()

    mock_user = MagicMock(spec=discord.User)
    mock_user.send = AsyncMock(side_effect=discord.Forbidden(MagicMock(), "Cannot send messages to this user"))

    mock_bot = MagicMock(spec=discord.Client)
    mock_bot.get_user = MagicMock(return_value=mock_user)
    mock_bot.fetch_user = AsyncMock(return_value=mock_user)

    with patch("apps.obx_tasks.bot.notification_service.session_scope", lambda: mock_session_scope_for(db_session)):
        sent = await send_approval_dm(mock_bot, sub, new_balance=25)
        # Should return False without raising exception
        assert sent is False


def test_format_task_display_name_clean():
    from apps.obx_tasks.bot.views import format_task_display_name

    class MockTask:
        def __init__(self, title, task_type):
            self.title = title
            self.task_type = task_type

    assert format_task_display_name(MockTask("CUSTOM_TASK", "CUSTOM_TASK")) == "Custom Task"
    assert format_task_display_name(MockTask("", "CUSTOM_TASK")) == "Custom Task"
    assert format_task_display_name(MockTask(None, "CUSTOM_TASK")) == "Custom Task"
    assert format_task_display_name(MockTask("Community Meme Raid", "CUSTOM_TASK")) == "Community Meme Raid"
    assert format_task_display_name(MockTask("LIKE", "LIKE")) == "Like Task"
    assert format_task_display_name(MockTask("Like the post", "LIKE")) == "Like the post"
    assert format_task_display_name(MockTask("RETWEET", "RETWEET")) == "Repost Task"
    assert format_task_display_name(None) == "Custom Task"


