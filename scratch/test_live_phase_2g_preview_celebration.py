import asyncio
import os
import sys
from datetime import datetime, timezone, timedelta

# Ensure repo root in sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import discord
from packages.database.session import session_scope
from packages.database.models.task import Task
from packages.database.models.submission import TaskSubmission
from packages.database.models.channel_config import PublishedMessage
from packages.shared.enums import TaskStatus, TaskType, SubmissionStatus
from packages.shared.config import get_settings
from apps.obx_tasks.services.task_service import TaskService
from apps.obx_tasks.services.channel_service import ChannelService
from apps.obx_tasks.bot.announcement_service import (
    announce_task,
    build_task_announcement_embed,
)
from apps.obx_tasks.bot.notification_service import (
    send_reward_notification,
    DismissRewardCelebrationView,
)

settings = get_settings()

async def run_live_test():
    print("=== LIVE DISCORD PREVIEW & DISMISSIBLE CELEBRATION TEST ===", flush=True)
    intents = discord.Intents.default()
    client = discord.Client(intents=intents)

    await client.login(settings.DISCORD_BOT_TOKEN)
    asyncio.create_task(client.connect())
    await client.wait_until_ready()

    guild_id = int(settings.DISCORD_GUILD_ID)
    guild = client.get_guild(guild_id)
    print(f"Logged in as: {client.user.name} ({client.user.id})", flush=True)
    print(f"Connected to guild: {guild.name} ({guild.id})", flush=True)

    tasks_channel_id = 1544530661908545668
    channel = guild.get_channel(tasks_channel_id)
    print(f"Tasks channel: #{channel.name} ({channel.id})", flush=True)

    # -------------------------------------------------------------
    # 1. TEST X TASK WITH IN-CARD PREVIEW & HIDDEN TARGET URL
    # -------------------------------------------------------------
    print("\n[TEST 1] Testing X Task with In-Card Preview...", flush=True)
    target_x_url = "https://x.com/monad_xyz/status/1888888888"
    with session_scope() as session:
        task_service = TaskService(session)
        x_task = task_service.create_task(
            title="Monad Ecosystem Drop",
            description="Engage with the official Monad tweet",
            task_type="LIKE",
            target_url=target_x_url,
            reward_per_user=15,
            total_reward_pool=150,
            created_by=str(client.user.id),
            ends_at=datetime.now(timezone.utc) + timedelta(minutes=10),
            preview_platform="X",
            preview_author="@monad_xyz",
            preview_title="Post on X",
            preview_description="Monad testnet is now open for ecosystem developers!",
            preview_image_url="https://pbs.twimg.com/media/banner_test.jpg",
        )
        x_task_id = x_task.id

    ok, msg = await announce_task(x_task, guild, client)
    print(f"Announce X task result: {ok} -> {msg}", flush=True)
    assert ok

    with session_scope() as session:
        ch_service = ChannelService(session)
        pub_x = ch_service.get_published_message(str(guild.id), "TASK_ANNOUNCEMENT", source_id=str(x_task_id))
        assert pub_x

    discord_x_msg = await channel.fetch_message(int(pub_x.message_id))
    print(f"✅ Published X Card Msg ID: {discord_x_msg.id}", flush=True)

    # Verify raw URL is absent from content and embed description
    assert target_x_url not in discord_x_msg.content
    embed_x = discord_x_msg.embeds[0]
    assert target_x_url not in embed_x.description
    print("✅ Raw target URL is completely HIDDEN from message content and embed description.")

    # Verify In-Card Content Preview box
    assert "╭──────────────────────────────╮" in embed_x.description
    assert "│ 𝕏 @monad_xyz" in embed_x.description
    assert "Monad testnet is now open for ecosystem developers!" in embed_x.description
    assert "╰──────────────────────────────╯" in embed_x.description
    print("✅ In-card content preview box successfully rendered inside single card.")

    # Verify Link Button
    btn_components = [b for row in discord_x_msg.components for b in row.children]
    open_btns = [b for b in btn_components if getattr(b, "url", None) == target_x_url]
    assert len(open_btns) == 1, "Open Task link button not found with target URL!"
    assert open_btns[0].label == "Open Task"
    print("✅ [ 🔗 Open Task ] button directly links to target URL.")

    # Clean up X test message
    await discord_x_msg.delete()
    print("✅ Cleaned up X test message.", flush=True)

    # -------------------------------------------------------------
    # 2. TEST WEBSITE TASK WITH OPEN GRAPH PREVIEW
    # -------------------------------------------------------------
    print("\n[TEST 2] Testing Website Task with OpenGraph Preview...", flush=True)
    target_web_url = "https://ethereum.org/en/developers/"
    with session_scope() as session:
        task_service = TaskService(session)
        web_task = task_service.create_task(
            title="Ethereum Developer Documentation",
            description="Explore builder guides and tutorials",
            task_type="CUSTOM_TASK",
            target_url=target_web_url,
            reward_per_user=10,
            total_reward_pool=100,
            created_by=str(client.user.id),
            preview_platform="Ethereum",
            preview_author="Ethereum Foundation",
            preview_title="Builder Documentation",
            preview_description="Guides, resources and tutorials for smart contract developers.",
        )
        web_task_id = web_task.id

    ok_web, msg_web = await announce_task(web_task, guild, client)
    print(f"Announce Web task result: {ok_web} -> {msg_web}", flush=True)
    assert ok_web

    with session_scope() as session:
        ch_service = ChannelService(session)
        pub_web = ch_service.get_published_message(str(guild.id), "TASK_ANNOUNCEMENT", source_id=str(web_task_id))
        assert pub_web

    discord_web_msg = await channel.fetch_message(int(pub_web.message_id))
    embed_web = discord_web_msg.embeds[0]
    assert target_web_url not in embed_web.description
    assert "╭──────────────────────────────╮" in embed_web.description
    assert "Ethereum Foundation" in embed_web.description
    assert "Builder Documentation" in embed_web.description or "smart contract developers" in embed_web.description
    print("✅ Web task OpenGraph preview rendered cleanly with hidden URL.")

    await discord_web_msg.delete()
    print("✅ Cleaned up Web test message.", flush=True)

    # -------------------------------------------------------------
    # 3. TEST TASK WITHOUT PREVIEW IMAGE
    # -------------------------------------------------------------
    print("\n[TEST 3] Testing Task without Preview Image...", flush=True)
    with session_scope() as session:
        task_service = TaskService(session)
        no_img_task = task_service.create_task(
            title="Clean Discord Community Task",
            description="Join our Discord channel",
            task_type="JOIN_DISCORD",
            target_url="https://discord.gg/testcommunity",
            reward_per_user=5,
            total_reward_pool=50,
            created_by=str(client.user.id),
            preview_platform="Discord",
            preview_author="Community Server",
            preview_image_url=None,
        )
        no_img_id = no_img_task.id

    ok_no_img, msg_no_img = await announce_task(no_img_task, guild, client)
    print(f"Announce No-Image task result: {ok_no_img} -> {msg_no_img}", flush=True)
    assert ok_no_img

    with session_scope() as session:
        ch_service = ChannelService(session)
        pub_no_img = ch_service.get_published_message(str(guild.id), "TASK_ANNOUNCEMENT", source_id=str(no_img_id))
        assert pub_no_img

    discord_no_img_msg = await channel.fetch_message(int(pub_no_img.message_id))
    assert discord_no_img_msg.embeds[0].image.url is None or discord_no_img_msg.embeds[0].image.url == ""
    print("✅ Task without image renders cleanly and premium.")

    await discord_no_img_msg.delete()
    print("✅ Cleaned up No-Image test message.", flush=True)

    # -------------------------------------------------------------
    # 4. TEST PART 6A: DISMISSIBLE IN-SERVER REWARD CELEBRATION
    # -------------------------------------------------------------
    print("\n[TEST 4] Testing Part 6A Dismissible In-Server Reward Celebration...", flush=True)
    test_user_id = str(client.user.id)
    with session_scope() as session:
        task_service = TaskService(session)
        celeb_task = task_service.create_task(
            title="Live Celebration Mission",
            description="Submit and celebrate",
            task_type="CUSTOM_TASK",
            target_url="https://example.com/live",
            reward_per_user=50,
            total_reward_pool=500,
            created_by="admin_test",
        )
        # Create submission
        sub = task_service.submit_task(
            task_id=celeb_task.id,
            discord_user_id=test_user_id,
            x_username="tester_handle",
            proof_url="https://x.com/tester_handle/status/12345",
            proof_text="Verified proof for celebration test",
        )
        sub_id = sub.id

        # Approve submission
        approved_sub = task_service.approve_submission(
            submission_id=sub_id,
            reviewer_discord_id="admin_test",
        )

    # Post celebration in-server
    ok_celeb = await send_reward_notification(
        bot=client,
        task=celeb_task,
        submission=approved_sub,
        new_balance=1000,
        guild=guild,
    )
    print(f"Send reward celebration result: {ok_celeb}", flush=True)
    assert ok_celeb

    with session_scope() as session:
        ch_service = ChannelService(session)
        pub_celeb = ch_service.get_published_message(str(guild.id), "REWARD_CELEBRATION", source_id=str(sub_id))
        assert pub_celeb
        celeb_msg_id = int(pub_celeb.message_id)

    celeb_msg = await channel.fetch_message(celeb_msg_id)
    print(f"✅ Celebration Message Published: Msg ID {celeb_msg.id} in #{channel.name}", flush=True)
    print(f"   Content: {celeb_msg.content!r}", flush=True)
    assert f"<@{test_user_id}>" in celeb_msg.content
    assert "🎉 **MISSION COMPLETE!**" in celeb_msg.content

    celeb_embed = celeb_msg.embeds[0]
    print(f"   Embed Title: {celeb_embed.title!r}", flush=True)
    assert "MISSION COMPLETE!" in celeb_embed.title
    assert "50 OBX" in celeb_embed.description

    celeb_btns = [b for row in celeb_msg.components for b in row.children]
    dismiss_btn = [b for b in celeb_btns if "Dismiss" in b.label]
    assert len(dismiss_btn) == 1
    print(f"   Button: {dismiss_btn[0].label} ({dismiss_btn[0].custom_id})", flush=True)
    assert f"obx:celebrate:dismiss:{sub_id}:{test_user_id}" == dismiss_btn[0].custom_id

    # Test Dismissal deletion
    await celeb_msg.delete()
    print("✅ Celebration message successfully dismissed/deleted!", flush=True)

    print("\n🎉 ALL LIVE VERIFICATION CHECKS FOR PREVIEW & CELEBRATION PASSED!", flush=True)
    await client.close()

if __name__ == "__main__":
    asyncio.run(run_live_test())
