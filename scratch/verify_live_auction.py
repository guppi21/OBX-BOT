import asyncio
import os
import sys
from datetime import datetime, timezone, timedelta
import discord

from packages.database.session import session_scope
from packages.database.models.auction import Auction
from packages.shared.enums import AuctionType, AuctionStatus
from packages.shared.config import get_settings
from apps.obx_tasks.services.auction_service import AuctionService
from apps.obx_tasks.services.channel_service import ChannelService
from apps.obx_tasks.bot.announcement_service import announce_auction

async def main():
    settings = get_settings()
    token = settings.DISCORD_BOT_TOKEN
    intents = discord.Intents.default()
    intents.message_content = True
    intents.guilds = True

    client = discord.Client(intents=intents)

    @client.event
    async def on_ready():
        print(f"Logged in as {client.user.name} ({client.user.id})")
        guild_id = 1542965409383321660
        guild = client.get_guild(guild_id)
        if not guild:
            print(f"Error: Guild {guild_id} not found.")
            await client.close()
            return

        with session_scope() as session:
            ch_service = ChannelService(session)
            auc_service = AuctionService(session)

            # Ensure auctions channel is set
            config = ch_service.get_or_create_guild_config(str(guild.id))
            print(f"Auctions channel configured: {config.auctions_channel_id}")

            # 1. Create 1-spot auction
            auc_1 = auc_service.create_auction(
                title="Monad Alpha Key 1/1",
                reward_title="Genesis Whitelist",
                description="Ranked bidding for a single 1-of-1 allocation. Highest bidder wins at close.",
                total_slots=1,
                price_or_min_bid=10,
                ends_at=datetime.now(timezone.utc) + timedelta(hours=2),
                created_by="live_test_admin",
            )
            print(f"Created 1-spot auction: ID={auc_1.id}, Type={auc_1.auction_type.value}, Spots={auc_1.total_slots}")

            # 2. Create 5-spot auction
            auc_5 = auc_service.create_auction(
                title="Berachain VIP Passes",
                reward_title="Guaranteed WL Allocation",
                description="Ranked bidding for 5 allocation slots. Top 5 bids win at close.",
                total_slots=5,
                price_or_min_bid=25,
                ends_at=datetime.now(timezone.utc) + timedelta(hours=4),
                created_by="live_test_admin",
            )
            print(f"Created 5-spot auction: ID={auc_5.id}, Type={auc_5.auction_type.value}, Spots={auc_5.total_slots}")

        # Publish both auctions
        ok1, msg1 = await announce_auction(auc_1, guild, client)
        print(f"Announce 1-spot: ok={ok1}, msg={msg1}")

        ok2, msg2 = await announce_auction(auc_5, guild, client)
        print(f"Announce 5-spot: ok={ok2}, msg={msg2}")

        channel = guild.get_channel(int(config.auctions_channel_id))
        messages = [msg async for msg in channel.history(limit=5)]
        print(f"Fetched {len(messages)} recent messages from {channel.name}")

        for m in messages[:2]:
            print(f"\n--- Message ID: {m.id} ---")
            print(f"Content: {m.content!r}")
            if m.embeds:
                emb = m.embeds[0]
                print(f"Embed Title: {emb.title!r}")
                print(f"Embed Description:\n{emb.description}")
            if m.components:
                for row in m.components:
                    for child in row.children:
                        print(f"Button: label={child.label!r}, style={child.style}, custom_id={getattr(child, 'custom_id', None)}")

        await client.close()

    await client.start(token)

if __name__ == "__main__":
    asyncio.run(main())
