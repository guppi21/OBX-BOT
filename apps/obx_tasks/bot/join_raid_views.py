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


def member_has_physical_raid_role(member: discord.Member, guild: discord.Guild) -> bool:
    """Check if the member physically has the raid role in their member.roles list."""
    settings = get_settings()
    configured_ids = {
        str(settings.RAID_ROLE_ID) if settings.RAID_ROLE_ID else None,
        "1539356123553996913",
        "1544870040866787428",
    }
    configured_ids.discard(None)

    for r in getattr(member, "roles", []):
        if str(r.id) in configured_ids:
            return True
        r_name = r.name.lower().strip()
        if r_name in ("raid", "raids", "raider", "raiders", "obx raider", "⚡ obx raider") or "raid" in r_name or "raider" in r_name:
            return True

    return False


async def grant_raider_role_to_member(guild: discord.Guild, member: discord.Member) -> Tuple[bool, str]:
    """Helper to assign the configured ⚡ OBX Raider or Raid role."""
    settings = get_settings()
    configured_ids = [
        settings.RAID_ROLE_ID,
        "1539356123553996913",
        "1544870040866787428",
    ]

    role = None
    for rid in configured_ids:
        if rid:
            try:
                role = guild.get_role(int(rid))
                if role:
                    break
            except (ValueError, TypeError):
                pass

    if not role:
        try:
            fetched_roles = await guild.fetch_roles()
            for r in fetched_roles:
                if str(r.id) in configured_ids:
                    role = r
                    break
        except Exception:
            pass

    if not role:
        for r in guild.roles:
            r_name = r.name.lower().strip()
            if r_name in ("raid", "raids", "raider", "raiders", "obx raider", "⚡ obx raider") or "raid" in r_name or "raider" in r_name:
                role = r
                break

    if not role:
        return False, "The raid role is not yet configured or could not be found. Please contact an administrator."

    try:
        await member.add_roles(role, reason="User joined the OBX Raid via onboarding flow")
        logger.info("Assigned raid role '%s' (%s) to user %s (%s)", role.name, role.id, member.name, member.id)
        return True, "Success"
    except discord.Forbidden:
        return False, f"Bot lacks permissions to assign the **@{role.name}** role. Please ensure the bot's role is positioned HIGHER than the **@{role.name}** role in Server Settings > Roles, and has the 'Manage Roles' permission enabled."
    except Exception as exc:
        return False, f"Error assigning raid role: {str(exc)}"


async def handle_join_raid_click(interaction: discord.Interaction):
    """Callback when a user clicks the [ JOIN THE RAID ] or [ JOIN RAID ] button."""
    guild = interaction.guild
    if not guild:
        await interaction.response.send_message("❌ This action must be performed within a server.", ephemeral=True)
        return

    member = interaction.user
    if not isinstance(member, discord.Member):
        member = guild.get_member(interaction.user.id)
        if not member:
            try:
                member = await guild.fetch_member(interaction.user.id)
            except Exception:
                await interaction.response.send_message("❌ Could not resolve your server member profile.", ephemeral=True)
                return

    has_role = member_has_physical_raid_role(member, guild)

    # If member does not physically have the raid role yet, assign it immediately!
    if not has_role:
        ok, err_msg = await grant_raider_role_to_member(guild, member)
        if not ok:
            if interaction.response.is_done():
                await interaction.followup.send(f"❌ {err_msg}", ephemeral=True)
            else:
                await interaction.response.send_message(f"❌ {err_msg}", ephemeral=True)
            return

    already_had_role = has_role

    # Fetch user's RaiderProfile (X handle)
    with session_scope() as session:
        r_service = RaiderService(session)
        profile = r_service.get_raider_profile(str(interaction.user.id))
        twitter_handle = profile.twitter_handle if profile else None

    if twitter_handle:
        desc_lines = [
            "Welcome to the OBX Raiders." if not already_had_role else "You are an active OBX Raider.",
            "",
            "𝕏 **CONNECTED ACCOUNT**",
            f"@{twitter_handle}",
            "",
            "You have the Raid role and access to:",
            "✦ Community Raids",
            "🎟️ Whitelist Auctions",
            "🏆 Results & Rewards",
        ]
        embed = discord.Embed(
            title="⚔️ OBX RAIDER ACTIVE" if already_had_role else "⚔️ YOU'RE IN",
            description="\n".join(desc_lines),
            color=COLOR_GREEN,
        )
        view = View(timeout=None)
        view.add_item(Button(
            label="EDIT TWITTER",
            style=discord.ButtonStyle.secondary,
            custom_id="obx:raider:set_twitter",
        ))
    else:
        desc_lines = [
            "Welcome to the OBX Raiders." if not already_had_role else "You already have the **Raid** role! ⚔️",
            "",
            "You have been granted the **Raid** role! ⚔️" if not already_had_role else "Connect your X account below to complete missions and participate in whitelist auctions.",
            "",
            "Connect your X account below to complete missions and participate in whitelist auctions." if not already_had_role else "",
        ]
        filtered_lines = [l for l in desc_lines if l is not None]
        embed = discord.Embed(
            title="⚔️ CONNECT YOUR X ACCOUNT" if already_had_role else "⚔️ YOU'RE IN",
            description="\n".join(filtered_lines),
            color=COLOR_GREEN if not already_had_role else COLOR_GOLD,
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


async def handle_activate_join_raid_click(interaction: discord.Interaction):
    """Callback when user clicks [ JOIN RAID ] from an onboarding screen."""
    await handle_join_raid_click(interaction)
