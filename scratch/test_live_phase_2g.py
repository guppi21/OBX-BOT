import asyncio
import os
import sys
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock

# Ensure repo root in sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import discord
from packages.database.session import session_scope
from packages.database.models.task import Task
from packages.database.models.auction import Auction, AuctionBid
from packages.shared.enums import TaskStatus, TaskType, AuctionType, AuctionStatus
from packages.shared.config import get_settings
from apps.obx_tasks.services.task_service import TaskService
from apps.obx_tasks.services.channel_service import ChannelService
from apps.obx_tasks.services.auction_service import AuctionService
from apps.obx_tasks.bot.announcement_service import (
    announce_task,
    announce_auction,
    announce_auction_winners,
)

settings = get_settings()

async def run_live_verification():
    print("=== LIVE DISCORD PHASE 2G MANUAL ACCEPTANCE TEST ===", flush=True)
    intents = discord.Intents.default()
    client = discord.Client(intents=intents)

    await client.login(settings.DISCORD_BOT_TOKEN)
    asyncio.create_task(client.connect())
    await client.wait_until_ready()

    guild_id = int(settings.DISCORD_GUILD_ID)
    guild = client.get_guild(guild_id)
    print(f"Logged in as: {client.user.name} ({client.user.id})", flush=True)
    print(f"Connected to guild: {guild.name} ({guild.id})", flush=True)

    # -------------------------------------------------------------
    # 1. TEST TASK ANNOUNCEMENT (Phase 2G Single-Card Architecture)
    # -------------------------------------------------------------
    print("\n[TEST 1] Testing Single-Card Task Announcement...", flush=True)
    with session_scope() as session:
        task_service = TaskService(session)
        task = task_service.create_task(
            title="Phase 2G Live Verification Mission",
            description="Like this tweet to verify Phase 2G OBX Crystal UI",
            task_type="LIKE",
            target_url="https://x.com/obx/status/999888777",
            reward_per_user=20,
            total_reward_pool=200,
            created_by="phase_2g_tester",
            ends_at=datetime.now(timezone.utc) + timedelta(minutes=5),
        )
        task_id = task.id

    ok, msg = await announce_task(task, guild, client)
    print(f"Publish task result: {ok} -> {msg}", flush=True)
    assert ok, f"Failed to announce task: {msg}"

    with session_scope() as session:
        ch_service = ChannelService(session)
        pub = ch_service.get_published_message(str(guild.id), "TASK_ANNOUNCEMENT", source_id=str(task_id))
        assert pub, "PublishedMessage record missing!"
        channel_id = int(pub.channel_id)
        message_id = int(pub.message_id)

    channel = guild.get_channel(channel_id)
    discord_msg = await channel.fetch_message(message_id)
    print(f"✅ Task Card Published: Msg ID {discord_msg.id} in #{channel.name}", flush=True)
    print(f"   Content: {discord_msg.content!r}", flush=True)
    assert "@everyone" in discord_msg.content
    assert "🔵 **NEW OBX MISSION AVAILABLE**" in discord_msg.content

    embed = discord_msg.embeds[0]
    print(f"   Title: {embed.title!r}", flush=True)
    assert "❤️ NEW LIKE MISSION" in embed.title
    assert "Phase 2G Live Verification Mission" in embed.description
    assert "💎 **REWARD**" in embed.description
    assert "**20 OBX**" in embed.description
    assert "⏳ **TIME REMAINING**" in embed.description
    assert "👥 **SPOTS**" in embed.description
    assert "🔗 **PLATFORM**" in embed.description
    assert "X (Twitter)" in embed.description
    assert embed.footer.text == "OBX Community Missions"

    # Action buttons
    view_items = discord_msg.components
    btn_labels = [c.label for row in view_items for c in row.children]
    print(f"   Buttons: {btn_labels}", flush=True)
    assert "Open Task" in btn_labels
    assert "Complete Mission" in btn_labels

    # -------------------------------------------------------------
    # 2. TEST IN-PLACE EDIT (Zero repeated ping)
    # -------------------------------------------------------------
    print("\n[TEST 2] Testing In-Place Edit (Zero Ping)...", flush=True)
    with session_scope() as session:
        task_service = TaskService(session)
        task_service.edit_task(task_id, changed_by="phase_2g_tester", title="Phase 2G Live Mission (EDITED)")

    with session_scope() as session:
        updated_task = session.query(Task).filter_by(id=task_id).first()

    ok_edit, msg_edit = await announce_task(updated_task, guild, client)
    print(f"In-place edit result: {ok_edit} -> {msg_edit}", flush=True)
    assert ok_edit

    discord_msg_edited = await channel.fetch_message(message_id)
    print(f"   Edited Content: {discord_msg_edited.content!r}", flush=True)
    assert discord_msg_edited.content == "", "Content must be cleared on edit to prevent duplicate pings!"
    assert "Phase 2G Live Mission (EDITED)" in discord_msg_edited.embeds[0].description
    print("✅ In-place edit updated card in place with zero ping!", flush=True)

    await discord_msg.delete()
    print("✅ Cleaned up live task message.", flush=True)

    # -------------------------------------------------------------
    # 3. TEST AUCTION ANNOUNCEMENT
    # -------------------------------------------------------------
    print("\n[TEST 3] Testing Auction Announcement...", flush=True)
    with session_scope() as session:
        auc_service = AuctionService(session)
        auc = auc_service.create_auction(
            title="Live Phase 2G Monad WL",
            reward_title="Genesis Whitelist Pass",
            description="Phase 2G live test ranked auction",
            auction_type=AuctionType.GTD,
            total_slots=5,
            price_or_min_bid=100,
            created_by="phase_2g_tester",
            ends_at=datetime.now(timezone.utc) + timedelta(hours=12),
        )
        auc_id = auc.id

    ok_auc, msg_auc = await announce_auction(auc, guild, client)
    print(f"Publish auction result: {ok_auc} -> {msg_auc}", flush=True)
    assert ok_auc

    with session_scope() as session:
        ch_service = ChannelService(session)
        pub_auc = ch_service.get_published_message(str(guild.id), "AUCTION_ANNOUNCEMENT", source_id=str(auc_id))
        assert pub_auc
        auc_ch_id = int(pub_auc.channel_id)
        auc_msg_id = int(pub_auc.message_id)

    auc_channel = guild.get_channel(auc_ch_id)
    auc_msg = await auc_channel.fetch_message(auc_msg_id)
    print(f"✅ Auction Card Published: Msg ID {auc_msg.id} in #{auc_channel.name}", flush=True)
    print(f"   Content: {auc_msg.content!r}", flush=True)
    assert "@everyone" in auc_msg.content
    assert "🔨 **NEW OBX AUCTION IS LIVE**" in auc_msg.content

    auc_embed = auc_msg.embeds[0]
    print(f"   Title: {auc_embed.title!r}", flush=True)
    assert "🔨 OBX AUCTION LIVE" in auc_embed.title
    assert "🎟️ **WHITELIST SPOTS**" in auc_embed.description
    assert "💎 **CURRENT / MINIMUM BID**" in auc_embed.description
    assert "🔥 **PARTICIPATION**" in auc_embed.description
    assert "⏳ **ENDS**" in auc_embed.description

    auc_buttons = [b.label for row in auc_msg.components for b in row.children]
    print(f"   Buttons: {auc_buttons}", flush=True)
    assert "Place Bid" in auc_buttons
    assert "View My Position" in auc_buttons
    assert "Home" not in auc_buttons

    await auc_msg.delete()
    print("✅ Cleaned up live auction message.", flush=True)

    # -------------------------------------------------------------
    # 4. TEST WINNER ANNOUNCEMENT
    # -------------------------------------------------------------
    print("\n[TEST 4] Testing Winner Announcement...", flush=True)
    mock_bid = MagicMock(spec=AuctionBid, discord_user_id="1542982329603985489", bid_amount=350)
    ok_win, msg_win = await announce_auction_winners(auc, [mock_bid], 1, guild, client)
    print(f"Publish winners result: {ok_win} -> {msg_win}", flush=True)
    assert ok_win

    with session_scope() as session:
        ch_service = ChannelService(session)
        pub_win = ch_service.get_published_message(str(guild.id), "AUCTION_RESULTS", source_id=str(auc_id))
        assert pub_win
        win_ch_id = int(pub_win.channel_id)
        win_msg_id = int(pub_win.message_id)

    win_channel = guild.get_channel(win_ch_id)
    win_msg = await win_channel.fetch_message(win_msg_id)
    print(f"✅ Winner Card Published: Msg ID {win_msg.id} in #{win_channel.name}", flush=True)
    print(f"   Content: {win_msg.content!r}", flush=True)
    assert "@everyone" in win_msg.content
    assert "🏆 **OBX RESULTS ARE IN**" in win_msg.content

    win_embed = win_msg.embeds[0]
    print(f"   Title: {win_embed.title!r}", flush=True)
    assert "AUCTION RESULTS" in win_embed.title
    assert "🎟️ **WHITELIST SPOTS CONFIRMED**" in win_embed.description
    assert "💎 **WINNING CUTOFF**" in win_embed.description
    assert "🎉 **WINNERS**" in win_embed.description
    assert win_embed.footer.text == "OBX Official Results"

    win_buttons = [b.label for row in win_msg.components for b in row.children]
    print(f"   Buttons: {win_buttons}", flush=True)
    assert "View My Result" in win_buttons

    await win_msg.delete()
    print("✅ Cleaned up live winner message.", flush=True)

    print("\n🎉 ALL PHASE 2G LIVE ACCEPTANCE TESTS PASSED ON DISCORD!", flush=True)
    await client.close()

if __name__ == "__main__":
    asyncio.run(run_live_verification())
