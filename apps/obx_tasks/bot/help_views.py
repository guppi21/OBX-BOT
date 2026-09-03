"""Persistent channel help cards and ephemeral 'How It Works' views for OBX channels."""

import discord
from discord.ui import View, Button
from packages.shared.typography import DIVIDER
from apps.obx_tasks.bot.ui_theme import COLOR_GOLD, COLOR_PURPLE, COLOR_CRYSTAL_BLUE


# ---------------------------------------------------------------------------
# Persistent Channel Intro Views (Attached to channel dashboards)
# ---------------------------------------------------------------------------

class TaskCenterIntroView(View):
    """Persistent action buttons on the Tasks channel intro card."""
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="How It Works",
        style=discord.ButtonStyle.primary,
        custom_id="obx:help:tasks",
        row=0,
    )
    async def how_it_works_btn(self, interaction: discord.Interaction, button: Button):
        await send_tasks_how_it_works(interaction)

    @discord.ui.button(
        label="Browse Missions",
        style=discord.ButtonStyle.secondary,
        custom_id="obx:help:browse_missions",
        row=0,
    )
    async def browse_missions_btn(self, interaction: discord.Interaction, button: Button):
        from apps.obx_tasks.bot.dashboard_views import handle_browse_tasks
        await handle_browse_tasks(interaction)


class AuctionCenterIntroView(View):
    """Persistent action buttons on the Auctions channel intro card."""
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="How It Works",
        style=discord.ButtonStyle.primary,
        custom_id="obx:help:auctions",
        row=0,
    )
    async def how_it_works_btn(self, interaction: discord.Interaction, button: Button):
        await send_auctions_how_it_works(interaction)

    @discord.ui.button(
        label="View Auctions",
        style=discord.ButtonStyle.secondary,
        custom_id="obx:help:view_auctions",
        row=0,
    )
    async def view_auctions_btn(self, interaction: discord.Interaction, button: Button):
        from apps.obx_tasks.bot.auction_views import handle_browse_auctions
        from packages.shared.enums import AuctionStatus
        await handle_browse_auctions(interaction, status=AuctionStatus.ACTIVE)


class WinnersCenterIntroView(View):
    """Persistent action buttons on the Winners channel intro card."""
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="How It Works",
        style=discord.ButtonStyle.primary,
        custom_id="obx:help:winners",
        row=0,
    )
    async def how_it_works_btn(self, interaction: discord.Interaction, button: Button):
        await send_winners_how_it_works(interaction)

    @discord.ui.button(
        label="View My Result",
        style=discord.ButtonStyle.secondary,
        custom_id="obx:help:view_result",
        row=0,
    )
    async def view_result_btn(self, interaction: discord.Interaction, button: Button):
        from apps.obx_tasks.bot.auction_views import handle_my_auction_activity
        await handle_my_auction_activity(interaction)


# ---------------------------------------------------------------------------
# Ephemeral "How It Works" Action Views
# ---------------------------------------------------------------------------

class TasksHelpView(View):
    """Actions attached to the ephemeral Tasks 'How It Works' guide."""
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="My Submissions",
        style=discord.ButtonStyle.primary,
        custom_id="obx:user:submissions",
        row=0,
    )
    async def submissions_btn(self, interaction: discord.Interaction, button: Button):
        from apps.obx_tasks.bot.dashboard_views import handle_my_submissions
        await handle_my_submissions(interaction)

    @discord.ui.button(
        label="Close",
        style=discord.ButtonStyle.secondary,
        custom_id="obx:help:close",
        row=0,
    )
    async def close_btn(self, interaction: discord.Interaction, button: Button):
        await handle_help_close(interaction)


class AuctionsHelpView(View):
    """Actions attached to the ephemeral Auctions 'How It Works' guide."""
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="My Wallet",
        style=discord.ButtonStyle.primary,
        custom_id="obx:user:wallet",
        row=0,
    )
    async def wallet_btn(self, interaction: discord.Interaction, button: Button):
        from apps.obx_tasks.bot.dashboard_views import handle_my_wallet
        await handle_my_wallet(interaction)

    @discord.ui.button(
        label="Close",
        style=discord.ButtonStyle.secondary,
        custom_id="obx:help:close",
        row=0,
    )
    async def close_btn(self, interaction: discord.Interaction, button: Button):
        await handle_help_close(interaction)


class WinnersHelpView(View):
    """Actions attached to the ephemeral Winners 'How It Works' guide."""
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="View My Result",
        style=discord.ButtonStyle.primary,
        custom_id="obx:help:view_result",
        row=0,
    )
    async def view_result_btn(self, interaction: discord.Interaction, button: Button):
        from apps.obx_tasks.bot.auction_views import handle_my_auction_activity
        await handle_my_auction_activity(interaction)

    @discord.ui.button(
        label="Close",
        style=discord.ButtonStyle.secondary,
        custom_id="obx:help:close",
        row=0,
    )
    async def close_btn(self, interaction: discord.Interaction, button: Button):
        await handle_help_close(interaction)


# ---------------------------------------------------------------------------
# Ephemeral Dispatchers
# ---------------------------------------------------------------------------

async def send_tasks_how_it_works(interaction: discord.Interaction):
    desc = [
        "# COMPLETE MISSIONS. EARN OBX.",
        "",
        "**① Find a mission**",
        "Choose an active mission in this channel.",
        "",
        "**② Complete the action**",
        "Follow the mission instructions.",
        "",
        "**③ Submit proof**",
        "Click **⚡ Complete Mission** and provide the required proof.",
        "",
        "**④ Wait for review**",
        "An OBX administrator verifies your submission.",
        "",
        "**⑤ Earn your reward**",
        "Once approved, OBX is credited directly to your wallet.",
        "",
        DIVIDER,
        "",
        "💎 **Complete missions → Earn OBX → Build your stack**",
    ]
    embed = discord.Embed(
        title="🎯 HOW OBX MISSIONS WORK",
        description="\n".join(desc),
        color=COLOR_GOLD,
    )
    embed.set_footer(text="✦ OBX COMMUNITY MISSIONS")
    view = TasksHelpView()
    await interaction.response.send_message(embed=embed, view=view, ephemeral=True)


async def send_auctions_how_it_works(interaction: discord.Interaction):
    desc = [
        "# SECURE YOUR SPOT.",
        "",
        "**① Find an active auction**",
        "Browse current whitelist or opportunity auctions.",
        "",
        "**② Choose your bid**",
        "Enter the amount of OBX you want to commit.",
        "",
        "**③ Your OBX is reserved**",
        "Your bid remains safely locked while the auction is active.",
        "",
        "**④ Check your position**",
        "Use **📍 View My Position** to see where you stand.",
        "",
        "**⑤ Results are announced**",
        "When the auction ends, winners are confirmed.",
        "",
        DIVIDER,
        "",
        "🏆 **Highest valid bids win the available spots.**",
    ]
    embed = discord.Embed(
        title="🔨 HOW OBX AUCTIONS WORK",
        description="\n".join(desc),
        color=COLOR_PURPLE,
    )
    embed.set_footer(text="✦ OBX COMMUNITY AUCTIONS")
    view = AuctionsHelpView()
    await interaction.response.send_message(embed=embed, view=view, ephemeral=True)


async def send_winners_how_it_works(interaction: discord.Interaction):
    desc = [
        "# SEE WHO WON.",
        "",
        "**① Wait for the auction to close**",
        "Bidding or claims end when the timer reaches zero.",
        "",
        "**② Winners are calculated**",
        "The system determines the successful participants.",
        "",
        "**③ Results are published**",
        "Winning users and relevant public results appear here.",
        "",
        "**④ Check your personal result**",
        "Use **📍 View My Result**.",
        "",
        "**⑤ Claim your reward or whitelist**",
        "Follow the instructions provided by the campaign.",
        "",
        DIVIDER,
        "",
        "🏆 **Win the auction. Secure the opportunity.**",
    ]
    embed = discord.Embed(
        title="🏆 HOW RESULTS WORK",
        description="\n".join(desc),
        color=COLOR_CRYSTAL_BLUE,
    )
    embed.set_footer(text="✦ OBX RESULTS")
    view = WinnersHelpView()
    await interaction.response.send_message(embed=embed, view=view, ephemeral=True)


async def handle_help_close(interaction: discord.Interaction):
    """Closes an ephemeral help card."""
    try:
        if hasattr(interaction, "delete_original_response"):
            await interaction.delete_original_response()
        else:
            await interaction.response.send_message("✕ Dismissed.", ephemeral=True)
    except Exception:
        pass
