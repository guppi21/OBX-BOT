import asyncio
import os
import sys
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import discord
from packages.database.session import session_scope
from packages.shared.config import get_settings
from apps.obx_tasks.services.task_service import TaskService
from apps.obx_tasks.services.channel_service import ChannelService
from apps.obx_tasks.bot.announcement_service import announce_task

settings = get_settings()

async def run_live_test():
    print("=== LIVE DISCORD PHASE 2H VERIFICATION ===", flush=True)
    intents = discord.Intents.default()
    client = discord.Client(intents=intents)

    await client.login(settings.DISCORD_BOT_TOKEN)
    asyncio.create_task(client.connect())
    await client.wait_until_ready()

    guild_id = int(settings.DISCORD_GUILD_ID)
    guild = client.get_guild(guild_id)
    tasks_channel_id = 1544530661908545668
    channel = guild.get_channel(tasks_channel_id)

    print(f"Logged in: {client.user.name} | Guild: {guild.name} | Channel: #{channel.name}", flush=True)

    # -------------------------------------------------------------
    # CASE 1: X POST WITH TEXT
    # -------------------------------------------------------------
    print("\n--- CASE 1: X Post with Text ---", flush=True)
    with session_scope() as session:
        task_service = TaskService(session)
        task1 = task_service.create_task(
            title="Ecosystem Announcement",
            description="Like the latest update",
            task_type="LIKE",
            target_url="https://x.com/BaconCheese21/status/1888000000",
            reward_per_user=15,
            total_reward_pool=150,
            created_by=str(client.user.id),
            ends_at=datetime.now(timezone.utc) + timedelta(minutes=15),
            preview_platform="X",
            preview_author="BaconCheese\n   @BaconCheese21",
            preview_title="Post on X",
            preview_description="Excited to announce our new community incentives program on OBX!",
        )
        task1_id = task1.id

    ok, msg = await announce_task(task1, guild, client)
    assert ok, f"Publish failed: {msg}"

    with session_scope() as session:
        ch_service = ChannelService(session)
        pub1 = ch_service.get_published_message(str(guild.id), "TASK_ANNOUNCEMENT", source_id=str(task1_id))
        assert pub1

    msg1 = await channel.fetch_message(int(pub1.message_id))
    embed1 = msg1.embeds[0]
    print(f"Title: {embed1.title}")
    print(f"Description:\n{embed1.description}")

    # Assertions
    assert "❤️ NEW LIKE MISSION" in embed1.title
    assert "🟢 ACTIVE" not in embed1.description
    assert "TIME REMAINING" not in embed1.description
    assert "┌─────────────────────────────────────┐" in embed1.description
    assert "│ 𝕏  BaconCheese" in embed1.description
    assert "@BaconCheese21" in embed1.description
    assert "Excited to announce our new community incentives program on OBX!" in embed1.description
    assert "└─────────────────────────────────────┘" in embed1.description
    assert "💎 **15 OBX**" in embed1.description
    assert "10 spots remaining" in embed1.description
    assert embed1.footer.text == "✦ OBX COMMUNITY MISSIONS"
    print("✅ CASE 1 PASSED: X post with text rendered author, username, post text, and compact strip.")

    # -------------------------------------------------------------
    # CASE 2: X POST WITH IMAGE
    # -------------------------------------------------------------
    print("\n--- CASE 2: X Post with Image ---", flush=True)
    with session_scope() as session:
        task_service = TaskService(session)
        task2 = task_service.create_task(
            title="Visual Media Showcase",
            description="Repost the artwork drop",
            task_type="RETWEET",
            target_url="https://x.com/BaconCheese21/status/1888000001",
            reward_per_user=25,
            total_reward_pool=250,
            created_by=str(client.user.id),
            preview_platform="X",
            preview_author="BaconCheese\n   @BaconCheese21",
            preview_title="Artwork Reveal",
            preview_description="Check out the newest NFT visual generation for the OBX treasury.",
            preview_image_url="https://raw.githubusercontent.com/PokeAPI/sprites/master/sprites/pokemon/other/official-artwork/25.png",
        )
        task2_id = task2.id

    ok, msg = await announce_task(task2, guild, client)
    assert ok

    with session_scope() as session:
        ch_service = ChannelService(session)
        pub2 = ch_service.get_published_message(str(guild.id), "TASK_ANNOUNCEMENT", source_id=str(task2_id))
        assert pub2

    msg2 = await channel.fetch_message(int(pub2.message_id))
    embed2 = msg2.embeds[0]
    assert embed2.image.url is not None
    assert "https://raw.githubusercontent.com" in embed2.image.url
    assert "BaconCheese" in embed2.description
    assert "Check out the newest NFT visual generation" in embed2.description
    print("✅ CASE 2 PASSED: X post with image rendered post text and embed image preview.")

    # -------------------------------------------------------------
    # CASE 3: WEBSITE WITH OPENGRAPH METADATA
    # -------------------------------------------------------------
    print("\n--- CASE 3: Website with OpenGraph Metadata ---", flush=True)
    with session_scope() as session:
        task_service = TaskService(session)
        task3 = task_service.create_task(
            title="Read Developer Documentation",
            description="Explore builder tutorials",
            task_type="CUSTOM_TASK",
            target_url="https://ethereum.org/en/developers/",
            reward_per_user=10,
            total_reward_pool=100,
            created_by=str(client.user.id),
            preview_platform="Ethereum Foundation",
            preview_author="Ethereum Foundation",
            preview_title="Ethereum Developer Documentation",
            preview_description="Learn smart contract development, toolchains, and protocols.",
        )
        task3_id = task3.id

    ok, msg = await announce_task(task3, guild, client)
    assert ok

    with session_scope() as session:
        ch_service = ChannelService(session)
        pub3 = ch_service.get_published_message(str(guild.id), "TASK_ANNOUNCEMENT", source_id=str(task3_id))
        assert pub3

    msg3 = await channel.fetch_message(int(pub3.message_id))
    embed3 = msg3.embeds[0]
    assert "│ 🌐  Ethereum Foundation" in embed3.description
    assert "Learn smart contract development" in embed3.description
    print("✅ CASE 3 PASSED: Website OpenGraph metadata rendered title and description.")

    # -------------------------------------------------------------
    # CASE 4: MISSING METADATA / OFFLINE FALLBACK (NEVER EMPTY!)
    # -------------------------------------------------------------
    print("\n--- CASE 4: Missing Metadata Fallback (Guaranteed Non-Empty) ---", flush=True)
    with session_scope() as session:
        task_service = TaskService(session)
        task4 = task_service.create_task(
            title="Fallback Resilience Mission",
            description="Interact with the target link to complete the task",
            task_type="CUSTOM_TASK",
            target_url="https://x.com/unknown_user_9999/status/9999999999",
            reward_per_user=20,
            total_reward_pool=200,
            created_by=str(client.user.id),
            preview_platform="X",
            preview_author="@unknown_user_9999",
            preview_title=None,
            preview_description=None,  # No description available
            preview_image_url=None,
        )
        task4_id = task4.id

    ok, msg = await announce_task(task4, guild, client)
    assert ok

    with session_scope() as session:
        ch_service = ChannelService(session)
        pub4 = ch_service.get_published_message(str(guild.id), "TASK_ANNOUNCEMENT", source_id=str(task4_id))
        assert pub4

    msg4 = await channel.fetch_message(int(pub4.message_id))
    embed4 = msg4.embeds[0]
    # Verify box is NOT empty
    assert "┌─────────────────────────────────────┐" in embed4.description
    assert "│ 𝕏  @unknown_user_9999" in embed4.description
    assert "Interact with the target link" in embed4.description
    assert "└─────────────────────────────────────┘" in embed4.description
    print("✅ CASE 4 PASSED: Missing metadata fallback is rich and never empty.")

    # -------------------------------------------------------------
    # CASE 5: ADMIN PREVIEW OVERRIDE
    # -------------------------------------------------------------
    print("\n--- CASE 5: Admin Preview Overrides ---", flush=True)
    with session_scope() as session:
        task_service = TaskService(session)
        task5 = task_service.create_task(
            title="Special Partner Campaign",
            description="Partner drop mission",
            task_type="LIKE",
            target_url="https://x.com/partner/status/123",
            reward_per_user=100,
            total_reward_pool=1000,
            created_by=str(client.user.id),
            preview_author="@partner",
            preview_description="Original auto text",
            preview_author_override="Official OBX Partner\n   @obx_partner",
            preview_text_override="Exclusive partner announcement: Mint passes now available for verified OBX holders!",
            preview_image_override="https://raw.githubusercontent.com/PokeAPI/sprites/master/sprites/pokemon/other/official-artwork/1.png",
        )
        task5_id = task5.id

    ok, msg = await announce_task(task5, guild, client)
    assert ok

    with session_scope() as session:
        ch_service = ChannelService(session)
        pub5 = ch_service.get_published_message(str(guild.id), "TASK_ANNOUNCEMENT", source_id=str(task5_id))
        assert pub5

    msg5 = await channel.fetch_message(int(pub5.message_id))
    embed5 = msg5.embeds[0]
    assert "Official OBX Partner" in embed5.description
    assert "@obx_partner" in embed5.description
    assert "Exclusive partner announcement: Mint passes now available" in embed5.description
    assert "Original auto text" not in embed5.description
    assert "https://raw.githubusercontent.com" in embed5.image.url
    print("✅ CASE 5 PASSED: Admin preview overrides took highest priority.")

    # Clean up test messages from live Discord channel
    for m in [msg1, msg2, msg3, msg4, msg5]:
        await m.delete()
    print("✅ Cleaned up all 5 test messages from #1-🎯・tasks.")

    print("\n🎉 ALL 5 LIVE DISCORD VERIFICATION CASES FOR PHASE 2H PASSED PERFECTLY!", flush=True)
    await client.close()

if __name__ == "__main__":
    asyncio.run(run_live_test())
