import asyncio
import os
import sys
from datetime import datetime, timezone, timedelta
import discord

from packages.shared.config import get_settings
from packages.database.session import session_scope
from packages.database.models.task import Task
from packages.database.models.auction import Auction, AuctionBid
from packages.database.models.channel_config import PublishedMessage
from packages.shared.enums import TaskStatus, TaskType, AuctionType, AuctionStatus
from apps.obx_tasks.services.task_service import TaskService
from apps.obx_tasks.services.channel_service import ChannelService
from apps.obx_tasks.services.auction_service import AuctionService
from apps.obx_tasks.bot.announcement_service import (
    announce_task,
    announce_auction,
    announce_auction_winners,
    build_task_announcement_embed,
)

settings = get_settings()

async def main():
    print("=== LIVE DISCORD REDESIGN MANUAL ACCEPTANCE TEST ===")
    intents = discord.Intents.default()
    client = discord.Client(intents=intents)

    await client.login(settings.DISCORD_BOT_TOKEN)
    asyncio.create_task(client.connect())
    await client.wait_until_ready()
    print(f"Logged in as: {client.user.name} ({client.user.id})")

    guild = client.get_guild(int(settings.DISCORD_GUILD_ID))
    print(f"Connected to guild: {guild.name} ({guild.id})")

    tasks_ch = guild.get_channel(1544530661908545668)
    auctions_ch = guild.get_channel(1544530705919246406)
    winners_ch = guild.get_channel(1544530794373193748)
    admin_ch = guild.get_channel(1544530861569871983)

    print(f"Tasks channel: #{tasks_ch.name} ({tasks_ch.id})")
    print(f"Auctions channel: #{auctions_ch.name} ({auctions_ch.id})")
    print(f"Winners channel: #{winners_ch.name} ({winners_ch.id})")

    # TEST 1: Create 5-minute LIKE task
    print("\n[TEST 1] Creating a 5-minute LIKE task...")
    with session_scope() as session:
        service = TaskService(session)
        now = datetime.now(timezone.utc)
        ends_5m = now + timedelta(minutes=5)
        task = service.create_task(
            title="Like Utopia Announcement",
            description="Like the tweet and earn OBX.",
            task_type=TaskType.LIKE,
            target_url="https://x.com/obx/status/1234567890",
            reward_per_user=10,
            total_reward_pool=100,
            created_by=str(client.user.id),
            ends_at=ends_5m,
            platform="X",
        )
        task_id = task.id
        print(f"Created LIKE task: ID={task_id}, Type={task.task_type.value}, Ends={task.ends_at}")

        # Announce task
        ok, msg = await announce_task(task, guild, client)
        print(f"Task announce result: ok={ok}, msg='{msg}'")

    # Fetch published card message
    with session_scope() as session:
        pub = ChannelService(session).get_published_message(str(guild.id), "TASK_ANNOUNCEMENT", source_id=str(task_id))
        task_card_msg_id = int(pub.message_id)

    task_card_msg = await tasks_ch.fetch_message(task_card_msg_id)
    print("\n--- TASK CARD VERIFICATION ---")
    print(f"1. Message Content: '{task_card_msg.content}'")
    assert "@everyone" in task_card_msg.content
    print("   -> Confirmed @everyone present in initial notification!")

    assert len(task_card_msg.embeds) == 1
    embed = task_card_msg.embeds[0]
    print(f"2. Embed Title: '{embed.title}'")
    assert embed.title == "❤️ NEW LIKE TASK"
    print("   -> Confirmed Title is exactly: ❤️ NEW LIKE TASK")

    print(f"3. Embed Description:\n{embed.description}")
    assert "Like the tweet and earn OBX." in embed.description
    assert "🟢 **ACTIVE**" in embed.description
    assert "💎 **Reward:** **10 OBX**" in embed.description
    assert "📦 **Available:** 10 / 10" in embed.description
    assert "𝕏 **Platform:** X (Twitter)" in embed.description
    assert "HOW TO COMPLETE" in embed.description
    print("   -> Confirmed clean hierarchy and core information!")

    print(f"4. Embed Footer: '{embed.footer.text if embed.footer else 'None'}'")
    assert "Double-Entry Vault" not in str(embed.footer.text)
    assert str(task_id) not in str(embed.footer.text)
    assert str(task_id) not in embed.description
    assert embed.footer.text == "OBX Community Rewards"
    print("   -> Confirmed ZERO Task ID, ZERO UUID, and ZERO technical footer chatter!")

    button_labels = [b.label for b in task_card_msg.components[0].children]
    print(f"5. Button Controls: {button_labels}")
    assert button_labels == ["Open Task", "Verify Completion"]
    print("   -> Confirmed exact buttons: [ 🔗 Open Task ] [ 📝 Verify Completion ]")

    # Check standalone URL message
    recent_msgs = [m async for m in tasks_ch.history(limit=3)]
    url_msg = next((m for m in recent_msgs if m.content == "https://x.com/obx/status/1234567890"), None)
    if url_msg:
        print(f"6. Standalone URL message detected: Msg ID={url_msg.id}")

    # TEST 2: In-place edit of task -> verify NO duplicate @everyone ping
    print("\n[TEST 2] Editing task in place and re-announcing...")
    with session_scope() as session:
        service = TaskService(session)
        updated = service.edit_task(
            task_id=task_id,
            changed_by=str(client.user.id),
            title="Like Utopia Announcement [EDITED]",
            description="Like the post and tag 1 friend to earn OBX.",
            reward_per_user=15,
            total_reward_pool=150,
        )
        ok_upd, msg_upd = await announce_task(updated, guild, client)
        print(f"Edit announce result: ok={ok_upd}, msg='{msg_upd}'")

    edited_card_msg = await tasks_ch.fetch_message(task_card_msg_id)
    print(f"Edited Message Content: '{edited_card_msg.content}'")
    assert edited_card_msg.content == "" or edited_card_msg.content is None
    print("   -> Confirmed: In-place edit stripped @everyone ping! Zero duplicate mention!")
    print(f"Edited Embed Description has 15 OBX: {'15 OBX' in edited_card_msg.embeds[0].description}")

    # TEST 3: Create & Announce Auction
    print("\n[TEST 3] Creating & Announcing Whitelist Auction...")
    with session_scope() as session:
        auc_service = AuctionService(session)
        auc = auc_service.create_auction(
            title="Monad Whitelist Access",
            reward_title="Guaranteed WL Spot",
            description="Compete for exclusive whitelist spots.",
            auction_type=AuctionType.GTD,
            total_slots=20,
            price_or_min_bid=100,
            created_by=str(client.user.id),
            ends_at=now + timedelta(hours=3),
        )
        auc_id = auc.id
        print(f"Created GTD auction: ID={auc_id}, Title='{auc.title}'")

        ok_auc, msg_auc = await announce_auction(auc, guild, client)
        print(f"Auction announce result: ok={ok_auc}, msg='{msg_auc}'")

    with session_scope() as session:
        pub_auc = ChannelService(session).get_published_message(str(guild.id), "AUCTION_ANNOUNCEMENT", source_id=str(auc_id))
        auc_card_msg_id = int(pub_auc.message_id)

    auc_card_msg = await auctions_ch.fetch_message(auc_card_msg_id)
    print("\n--- AUCTION CARD VERIFICATION ---")
    print(f"1. Message Content: '{auc_card_msg.content}'")
    assert "@everyone" in auc_card_msg.content
    print("   -> Confirmed @everyone 🔨 A NEW AUCTION IS LIVE!")

    embed_auc = auc_card_msg.embeds[0]
    print(f"2. Embed Title: '{embed_auc.title}'")
    assert "MONAD WHITELIST ACCESS" in embed_auc.title
    print(f"3. Embed Description:\n{embed_auc.description}")
    assert "Compete for exclusive whitelist spots." in embed_auc.description
    assert "🎟 **WL Spots:** **20**" in embed_auc.description
    assert "💎 **Minimum Bid:** **100 OBX**" in embed_auc.description
    assert "Double-Entry Vault" not in str(embed_auc.footer.text)
    assert embed_auc.footer.text == "OBX Whitelist Auctions"

    auc_buttons = [b.label for b in auc_card_msg.components[0].children]
    print(f"4. Auction Buttons: {auc_buttons}")
    assert "View Auction" in auc_buttons
    assert "Place / Update Bid" in auc_buttons
    assert "Open Auction Center" not in auc_buttons
    print("   -> Confirmed unified buttons: [ 📊 View Auction ] [ 💰 Place / Update Bid ]")

    # TEST 4: Auction Result Card
    print("\n[TEST 4] Announcing Auction Results in #winners...")
    with session_scope() as session:
        # Simulate winners
        w1 = AuctionBid(auction_id=auc_id, discord_user_id=str(client.user.id), bid_amount=1250, is_winner=True)
        ok_w, msg_w = await announce_auction_winners(auc, [w1], 5, guild, client)
        print(f"Winners announce result: ok={ok_w}, msg='{msg_w}'")

    with session_scope() as session:
        pub_w = ChannelService(session).get_published_message(str(guild.id), "AUCTION_RESULTS", source_id=str(auc_id))
        win_msg_id = int(pub_w.message_id)

    win_msg = await winners_ch.fetch_message(win_msg_id)
    print("\n--- WINNERS CARD VERIFICATION ---")
    print(f"1. Message Content: '{win_msg.content}'")
    assert "@everyone" in win_msg.content
    print("   -> Confirmed @everyone 🏆 AUCTION RESULTS ARE IN!")

    embed_win = win_msg.embeds[0]
    print(f"2. Embed Title: '{embed_win.title}'")
    assert "AUCTION RESULTS" in embed_win.title
    print(f"3. Embed Description:\n{embed_win.description}")
    assert "Winning Cutoff:** **1,250 OBX**" in embed_win.description
    assert "Congratulations to the winners!" in embed_win.description
    win_btn_labels = [b.label for b in win_msg.components[0].children]
    print(f"4. Result Buttons: {win_btn_labels}")
    assert win_btn_labels == ["View My Result"]
    print("   -> Confirmed button: [ 🏆 View My Result ]")

    # Clean up test messages
    print("\n[CLEANUP] Removing test messages from Discord channels...")
    try:
        await task_card_msg.delete()
        if url_msg:
            await url_msg.delete()
        await auc_card_msg.delete()
        await win_msg.delete()
        print("Test messages cleaned up successfully.")
    except Exception as exc:
        print(f"Cleanup note: {exc}")

    await client.close()
    print("\n=== ALL MANUAL ACCEPTANCE TESTS PASSED WITH 100% SUCCESS ===")

if __name__ == "__main__":
    asyncio.run(main())
