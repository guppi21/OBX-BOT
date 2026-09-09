import os
import time
import json
import uuid
import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, AsyncMock, patch
from pathlib import Path

import discord
from packages.database.session import session_scope
from packages.database.models.user import User
from packages.database.models.wallet import Wallet
from packages.database.models.task import Task
from packages.database.models.submission import TaskSubmission
from packages.database.models.auction import Auction, AuctionBid
from packages.database.models.raider_profile import RaiderProfile
from packages.database.models.channel_config import GuildConfig
from packages.shared.enums import TaskStatus, SubmissionStatus, AuctionType, AuctionStatus
from apps.obx_tasks.services.backup_service import BackupService
from apps.obx_tasks.bot.permissions import check_raider_access, invalidate_raider_cache, _raider_access_cache


def _make_mock_session_scope(db_session):
    from contextlib import contextmanager

    @contextmanager
    def _scope():
        yield db_session

    return _scope


def test_backup_service_create_snapshot(db_session):
    # Setup test data
    u1 = User(discord_user_id="bk_test_u1")
    db_session.add(u1)
    db_session.flush()

    w1 = Wallet(user_id=u1.id, available_balance=500, locked_balance=100)
    rp1 = RaiderProfile(
        discord_user_id="bk_test_u1",
        twitter_handle="bk_twitter_1",
        twitter_profile_url="https://x.com/bk_twitter_1",
    )
    t1 = Task(
        title="Backup Task",
        description="Task desc",
        task_type="LIKE",
        target_url="https://x.com/post/1",
        reward_per_user=50,
        total_reward_pool=500,
        created_by="admin",
    )
    db_session.add_all([w1, rp1, t1])
    db_session.flush()

    sub1 = TaskSubmission(
        task_id=t1.id,
        discord_user_id="bk_test_u1",
        x_username="bk_twitter_1",
        proof_url="https://x.com/proof/1",
        proof_text="Proof text",
        status=SubmissionStatus.APPROVED,
    )
    auc1 = Auction(
        title="Test Whitelist Auction",
        reward_title="Whitelist Role",
        description="Bid for WL",
        auction_type=AuctionType.GTD,
        total_slots=3,
        price_or_min_bid=100,
        status=AuctionStatus.ACTIVE,
        created_by="admin",
    )
    db_session.add_all([sub1, auc1])
    db_session.flush()

    bid1 = AuctionBid(
        auction_id=auc1.id,
        discord_user_id="bk_test_u1",
        bid_amount=100,
        is_winner=True,
    )
    cfg1 = GuildConfig(
        guild_id="123456789",
        admin_channel_id="987654321",
    )
    db_session.add_all([bid1, cfg1])
    db_session.commit()

    # Generate snapshot
    snapshot = BackupService.create_database_snapshot(db_session)

    assert snapshot["version"] == "2.0"
    assert snapshot["total_users"] >= 1
    assert snapshot["total_wallets"] >= 1
    assert snapshot["total_obx_in_circulation"] >= 600
    assert snapshot["total_tasks"] >= 1
    assert snapshot["total_submissions"] >= 1
    assert snapshot["total_auctions"] >= 1
    assert snapshot["total_bids"] >= 1

    # Verify wallet structure
    user_wallets = [w for w in snapshot["wallets"] if w["discord_user_id"] == "bk_test_u1"]
    assert len(user_wallets) == 1
    assert user_wallets[0]["available_balance"] == 500
    assert user_wallets[0]["total_balance"] == 600

    # Verify raider profiles
    profiles = [p for p in snapshot["raider_profiles"] if p["discord_user_id"] == "bk_test_u1"]
    assert len(profiles) == 1
    assert profiles[0]["twitter_handle"] == "bk_twitter_1"


def test_backup_service_disk_save_and_rotation(tmp_path):
    backup_dir = tmp_path / "test_backups"

    payload = {
        "version": "2.0",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "total_users": 5,
        "total_wallets": 5,
        "total_obx_in_circulation": 1000,
        "total_tasks": 2,
        "total_submissions": 3,
        "total_auctions": 1,
        "total_bids": 2,
        "wallets": [],
        "users": [],
    }

    # Save initial snapshot
    saved_path, pruned = BackupService.save_snapshot_to_disk(payload, backup_dir=backup_dir, retention_hours=48)
    assert os.path.exists(saved_path)
    assert pruned == 0

    with open(saved_path, "r", encoding="utf-8") as f:
        loaded = json.load(f)
    assert loaded["total_obx_in_circulation"] == 1000

    # Simulate older snapshots
    old_file_1 = backup_dir / "obx_snapshot_2026-09-01_120000.json"
    old_file_2 = backup_dir / "obx_snapshot_2026-09-05_120000.json"
    recent_file = backup_dir / "obx_snapshot_2026-09-08_120000.json"

    for p in (old_file_1, old_file_2, recent_file):
        with open(p, "w") as f:
            json.dump(payload, f)

    # Set mtimes: old_file_1 = 100 hours ago, old_file_2 = 60 hours ago, recent_file = 10 hours ago
    now = time.time()
    os.utime(old_file_1, (now - 100 * 3600, now - 100 * 3600))
    os.utime(old_file_2, (now - 60 * 3600, now - 60 * 3600))
    os.utime(recent_file, (now - 10 * 3600, now - 10 * 3600))
    os.utime(saved_path, (now, now))

    # Run rotation with 48h retention
    pruned_count = BackupService.rotate_old_backups(backup_dir, retention_hours=48)
    assert pruned_count == 2
    assert not old_file_1.exists()
    assert not old_file_2.exists()
    assert recent_file.exists()
    assert Path(saved_path).exists()


def test_backup_service_never_prunes_single_remaining_backup(tmp_path):
    backup_dir = tmp_path / "single_backup"
    backup_dir.mkdir(parents=True, exist_ok=True)

    single_file = backup_dir / "obx_snapshot_2020-01-01_000000.json"
    with open(single_file, "w") as f:
        f.write("{}")

    # Set file age to 5 years ago
    ancient = time.time() - (5 * 365 * 86400)
    os.utime(single_file, (ancient, ancient))

    # Rotation should NOT delete the sole backup
    pruned = BackupService.rotate_old_backups(backup_dir, retention_hours=48)
    assert pruned == 0
    assert single_file.exists()


@pytest.mark.asyncio
async def test_post_snapshot_to_admin_channel(tmp_path, db_session):
    test_file = tmp_path / "obx_snapshot_test.json"
    with open(test_file, "w") as f:
        f.write("{}")

    mock_guild = MagicMock(spec=discord.Guild)
    mock_guild.id = 123456
    mock_guild.name = "Test Guild"

    mock_channel = MagicMock(spec=discord.TextChannel)
    mock_channel.name = "admin-logs"
    mock_perms = MagicMock()
    mock_perms.send_messages = True
    mock_perms.attach_files = True
    mock_channel.permissions_for.return_value = mock_perms
    mock_channel.send = AsyncMock()

    mock_guild.get_channel.return_value = mock_channel
    mock_guild.text_channels = [mock_channel]

    mock_bot = MagicMock(spec=discord.Client)
    mock_bot.user.id = 999999

    payload = {
        "total_users": 10,
        "total_wallets": 10,
        "total_obx_in_circulation": 5000,
        "total_tasks": 4,
        "total_submissions": 8,
        "total_auctions": 2,
        "total_bids": 6,
    }

    with patch("apps.obx_tasks.services.backup_service.session_scope", lambda: _make_mock_session_scope(db_session)()):
        ok = await BackupService.post_snapshot_to_admin_channel(
            guild=mock_guild,
            filepath=str(test_file),
            payload=payload,
            bot=mock_bot,
        )

    assert ok is True
    assert mock_channel.send.called
    send_kwargs = mock_channel.send.call_args[1]
    assert "file" in send_kwargs
    assert "embed" in send_kwargs
    assert "Daily OBX System Snapshot" in send_kwargs["embed"].title
    assert "5,000 OBX" in send_kwargs["embed"].description


@pytest.mark.asyncio
async def test_raider_access_cache_prevents_redundant_queries():
    invalidate_raider_cache("cached_user_1")

    mock_interaction = MagicMock(spec=discord.Interaction)
    mock_interaction.user = MagicMock()
    mock_interaction.user.id = 777888
    mock_interaction.user.roles = [MagicMock(name="Raid")]
    mock_interaction.user.guild_permissions.administrator = False

    # First call: hits DB mock
    mock_profile = MagicMock(twitter_handle="cached_x_user")
    with patch("apps.obx_tasks.bot.permissions.has_raider_role", return_value=True), \
         patch("apps.obx_tasks.bot.permissions.is_admin", return_value=False), \
         patch("apps.obx_tasks.services.raider_service.RaiderService.get_raider_profile", return_value=mock_profile) as mock_get_prof:
        allowed_1 = await check_raider_access(mock_interaction)
        assert allowed_1 is True
        assert mock_get_prof.call_count == 1

        # Second call: served from in-memory cache, 0 DB lookups!
        allowed_2 = await check_raider_access(mock_interaction)
        assert allowed_2 is True
        assert mock_get_prof.call_count == 1  # Not incremented!

    # Invalidate cache
    invalidate_raider_cache(str(mock_interaction.user.id))

    # Third call: now hits DB again
    with patch("apps.obx_tasks.bot.permissions.has_raider_role", return_value=True), \
         patch("apps.obx_tasks.bot.permissions.is_admin", return_value=False), \
         patch("apps.obx_tasks.services.raider_service.RaiderService.get_raider_profile", return_value=mock_profile) as mock_get_prof_2:
        allowed_3 = await check_raider_access(mock_interaction)
        assert allowed_3 is True
        assert mock_get_prof_2.call_count == 1
