import discord
from packages.shared.config import get_settings
from packages.database.session import session_scope


def is_admin(interaction: discord.Interaction) -> bool:
    """Checks if the interaction user has administrator permissions or configured admin roles."""
    if interaction.user.guild_permissions.administrator:
        return True

    settings = get_settings()
    allowed_roles = set(settings.DISCORD_ADMIN_ROLE_IDS)
    if not allowed_roles:
        return interaction.user.guild_permissions.manage_guild

    if isinstance(interaction.user, discord.Member):
        user_role_ids = {str(role.id) for role in interaction.user.roles}
        if user_role_ids.intersection(allowed_roles):
            return True

    return False


def has_raider_role(interaction_or_member: discord.Interaction | discord.Member) -> bool:
    """Checks if the user has the unified ⚡ OBX Raider role or has administrator permissions."""
    if isinstance(interaction_or_member, discord.Interaction):
        if is_admin(interaction_or_member):
            return True
        member = interaction_or_member.user
    else:
        member = interaction_or_member

    # Guild administrators always have access
    if getattr(member, "guild_permissions", None) and member.guild_permissions.administrator:
        return True

    settings = get_settings()
    configured_ids = {
        str(settings.RAID_ROLE_ID) if settings.RAID_ROLE_ID else None,
        "1539356123553996913",
        "1544870040866787428",
    }
    configured_ids.discard(None)

    if isinstance(member, discord.Member):
        user_role_ids = {str(role.id) for role in member.roles}
        if user_role_ids.intersection(configured_ids):
            return True
        # Also support matching by role name
        for r in member.roles:
            r_name = r.name.lower().strip()
            if r_name in ("raid", "raids", "raider", "raiders", "obx raider", "⚡ obx raider") or "raid" in r_name or "raider" in r_name:
                return True

    return False


async def check_raider_access(interaction: discord.Interaction) -> bool:
    """Ensure the user has:
    1. has_raider_role == True
    2. twitter_handle exists in RaiderProfile

    If either is missing, shows the onboarding screen ephemerally and returns False.
    """
    if is_admin(interaction):
        return True

    from apps.obx_tasks.services.raider_service import RaiderService
    from apps.obx_tasks.bot.join_raid_views import handle_join_raid_click

    with session_scope() as session:
        r_service = RaiderService(session)
        profile = r_service.get_raider_profile(str(interaction.user.id))
        has_twitter = (profile is not None and bool(profile.twitter_handle))

    has_role = has_raider_role(interaction)

    if has_role and has_twitter:
        return True

    from apps.obx_tasks.bot.ui_theme import COLOR_GOLD
    from discord.ui import View, Button

    if not has_role:
        embed = discord.Embed(
            title="⚔️ JOIN THE OBX RAID",
            description=(
                "You need the **Raid** role to participate in missions and auctions.\n\n"
                "Click **JOIN RAID** below to get the role!"
            ),
            color=COLOR_GOLD,
        )
        view = View(timeout=None)
        view.add_item(Button(
            label="JOIN RAID",
            style=discord.ButtonStyle.success,
            custom_id="obx:join_raid:activate",
        ))
        view.add_item(Button(
            label="Set X Account",
            style=discord.ButtonStyle.primary,
            custom_id="obx:raider:set_twitter",
        ))
        if interaction.response.is_done():
            await interaction.followup.send(embed=embed, view=view, ephemeral=True)
        else:
            await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
        return False
    else:
        embed = discord.Embed(
            title="⚔️ CONNECT YOUR X ACCOUNT",
            description=(
                "Connect your X account to participate in community raids and whitelist auctions."
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
        return False
