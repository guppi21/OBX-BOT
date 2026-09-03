import logging
from typing import Optional, Tuple
import discord
from discord.ui import View, Button, Modal, TextInput

from packages.shared.config import get_settings
from packages.database.session import session_scope
from apps.obx_tasks.services.raider_service import RaiderService
from apps.obx_tasks.bot.permissions import has_raider_role
from apps.obx_tasks.bot.ui_theme import COLOR_GOLD, COLOR_GREEN

logger = logging.getLogger("obx.tasks.bot.join_raid")


def build_join_raid_embed() -> discord.Embed:
    """Build the clean premium onboarding embed for #⚡・join-raid."""
    desc_lines = [
        "**One Role. Universal Access. The Entire OBX Ecosystem.**",
        "",
        "Become an official **⚡ OBX Raider** to participate in missions, earn OBX, compete in whitelist auctions, and claim community rewards.",
        "",
        "━━━━━━━━━━━━━━━━━━━━",
        "",
        "✦ **Community Raids & Tasks**",
        "Like, repost, and engage in high-value campaigns.",
        "",
        "🎟️ **Guaranteed Whitelist Auctions**",
        "Bid your earned OBX for verified whitelist spots.",
        "",
        "🏆 **Results & Vault Accounting**",
        "Track rewards backed by an auditable double-entry ledger.",
        "",
        "━━━━━━━━━━━━━━━━━━━━",
        "",
        "Set your X account and click **[ ⚔️ JOIN THE RAID ]** to begin.",
    ]

    embed = discord.Embed(
        title="⚡ JOIN THE OBX RAID",
        description="\n".join(desc_lines),
        color=COLOR_GOLD,
    )
    return embed


class JoinRaidView(View):
    """Persistent view with Join the Raid and Set X Account buttons."""
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(Button(
            label="JOIN THE RAID",
            style=discord.ButtonStyle.success,
            custom_id="obx:join_raid",
            row=0,
        ))
        self.add_item(Button(
            label="Set X Account",
            style=discord.ButtonStyle.primary,
            custom_id="obx:raider:set_twitter",
            row=0,
        ))


class SetTwitterModal(Modal, title="🐦 SET X ACCOUNT"):
    def __init__(self, is_edit: bool = False):
        super().__init__()
        self.is_edit = is_edit
        self.x_input = TextInput(
            label="X Handle or Profile URL",
            placeholder="@username or https://x.com/username",
            required=True,
            max_length=128,
        )
        self.add_item(self.x_input)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        raw_val = self.x_input.value.strip()

        avatar_url = None
        try:
            from apps.obx_tasks.services.raider_service import normalize_twitter_input
            handle_norm, prof_url = normalize_twitter_input(raw_val)
            from apps.obx_tasks.services.url_preview_service import UrlPreviewService
            preview_meta = await UrlPreviewService.fetch_preview(prof_url)
            if preview_meta and preview_meta.avatar_url:
                avatar_url = preview_meta.avatar_url
        except Exception:
            avatar_url = None

        try:
            with session_scope() as session:
                r_service = RaiderService(session)
                profile = r_service.set_raider_twitter(str(interaction.user.id), raw_val, avatar_url=avatar_url)
                handle = profile.twitter_handle
        except ValueError as val_err:
            await interaction.followup.send(f"❌ {str(val_err)}", ephemeral=True)
            return
        except Exception as exc:
            logger.error("Error saving X account for %s: %s", interaction.user.id, exc)
            await interaction.followup.send("❌ An unexpected error occurred while saving your X account.", ephemeral=True)
            return

        embed = discord.Embed(
            title="🐦 X ACCOUNT CONNECTED",
            description=(
                f"@{handle}\n\n"
                f"Your raid account is ready."
            ),
            color=COLOR_GREEN,
        )
        if avatar_url:
            embed.set_thumbnail(url=avatar_url)
        await interaction.followup.send(embed=embed, ephemeral=True)


async def grant_raider_role_to_member(guild: discord.Guild, member: discord.Member) -> Tuple[bool, str]:
    """Helper to assign the configured ⚡ OBX Raider role."""
    settings = get_settings()
    raid_role_id = settings.RAID_ROLE_ID

    role = None
    if raid_role_id:
        try:
            role = guild.get_role(int(raid_role_id))
        except (ValueError, TypeError):
            pass

    if not role:
        for r in guild.roles:
            if r.name in ("⚡ OBX Raider", "OBX Raider"):
                role = r
                break

    if not role:
        return False, "The **⚡ OBX Raider** role is not yet configured or could not be found. Please contact an administrator."

    try:
        await member.add_roles(role, reason="User joined the OBX Raid via onboarding flow")
        logger.info("Assigned ⚡ OBX Raider role (%s) to user %s (%s)", role.id, member.name, member.id)
        return True, "Success"
    except discord.Forbidden:
        return False, "Bot lacks permissions to assign the **⚡ OBX Raider** role. Please ensure the bot's role is positioned higher in server settings."
    except Exception as exc:
        return False, f"Error assigning raid role: {str(exc)}"


async def handle_join_raid_click(interaction: discord.Interaction):
    """Callback when a user clicks the [ ⚔️ JOIN THE RAID ] or [ ⚔️ JOIN RAID ] button."""
    guild = interaction.guild
    if not guild:
        await interaction.response.send_message("❌ This action must be performed within a server.", ephemeral=True)
        return

    # Fetch user's RaiderProfile
    with session_scope() as session:
        r_service = RaiderService(session)
        profile = r_service.get_raider_profile(str(interaction.user.id))
        twitter_handle = profile.twitter_handle if profile else None

    has_role = has_raider_role(interaction)

    # State 1: Already has role AND has Twitter handle
    if has_role and twitter_handle:
        embed = discord.Embed(
            title="⚔️ OBX RAIDER ACTIVE",
            description=(
                f"𝕏 **CONNECTED ACCOUNT**\n"
                f"@{twitter_handle}"
            ),
            color=COLOR_GREEN,
        )
        view = View(timeout=None)
        view.add_item(Button(
            label="EDIT TWITTER",
            style=discord.ButtonStyle.secondary,
            custom_id="obx:raider:set_twitter",
        ))
        if interaction.response.is_done():
            await interaction.followup.send(embed=embed, view=view, ephemeral=True)
        else:
            await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
        return

    # State 2: Has role BUT missing Twitter handle
    if has_role and not twitter_handle:
        embed = discord.Embed(
            title="⚔️ CONNECT YOUR X ACCOUNT",
            description=(
                "Set your X account to participate in community raids and whitelist auctions."
            ),
            color=COLOR_GOLD,
        )
        view = View(timeout=None)
        view.add_item(Button(
            label="Set X Account",
            style=discord.ButtonStyle.primary,
            custom_id="obx:raider:set_twitter",
        ))
        if interaction.response.is_done():
            await interaction.followup.send(embed=embed, view=view, ephemeral=True)
        else:
            await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
        return

    # State 3: Missing role AND missing Twitter handle
    if not has_role and not twitter_handle:
        embed = discord.Embed(
            title="⚔️ JOIN THE OBX RAID",
            description=(
                "Set your X account first, then join the raider program."
            ),
            color=COLOR_GOLD,
        )
        view = View(timeout=None)
        view.add_item(Button(
            label="Set X Account",
            style=discord.ButtonStyle.primary,
            custom_id="obx:raider:set_twitter",
        ))
        view.add_item(Button(
            label="JOIN RAID",
            style=discord.ButtonStyle.success,
            custom_id="obx:join_raid:activate",
        ))
        if interaction.response.is_done():
            await interaction.followup.send(embed=embed, view=view, ephemeral=True)
        else:
            await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
        return

    # State 4: Missing role BUT has Twitter handle -> Grant role!
    member = interaction.user
    if not isinstance(member, discord.Member):
        member = guild.get_member(interaction.user.id)
        if not member:
            try:
                member = await guild.fetch_member(interaction.user.id)
            except Exception:
                await interaction.response.send_message("❌ Could not resolve your server member profile.", ephemeral=True)
                return

    ok, err_msg = await grant_raider_role_to_member(guild, member)
    if not ok:
        if interaction.response.is_done():
            await interaction.followup.send(f"❌ {err_msg}", ephemeral=True)
        else:
            await interaction.response.send_message(f"❌ {err_msg}", ephemeral=True)
        return

    desc_lines = [
        "Welcome to the OBX Raiders.",
        "",
        "𝕏 **X ACCOUNT**",
        f"@{twitter_handle}",
        "",
        "You now have access to:",
        "",
        "✦ Community Raids",
        "🎟️ Whitelist Auctions",
        "🏆 Results & Rewards",
    ]
    embed = discord.Embed(
        title="⚔️ YOU'RE IN",
        description="\n".join(desc_lines),
        color=COLOR_GREEN,
    )
    view = View(timeout=None)
    view.add_item(Button(
        label="EDIT TWITTER",
        style=discord.ButtonStyle.secondary,
        custom_id="obx:raider:set_twitter",
    ))
    if interaction.response.is_done():
        await interaction.followup.send(embed=embed, view=view, ephemeral=True)
    else:
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)


async def handle_activate_join_raid_click(interaction: discord.Interaction):
    """Callback when user clicks [ ⚔️ JOIN RAID ] from an onboarding screen."""
    with session_scope() as session:
        r_service = RaiderService(session)
        profile = r_service.get_raider_profile(str(interaction.user.id))

    if not profile or not profile.twitter_handle:
        await interaction.response.send_message(
            "⚠️ Set your X account first. Click **[ 🐦 Set X Account ]** to connect your X account before joining the raid.",
            ephemeral=True,
        )
        return

    await handle_join_raid_click(interaction)
