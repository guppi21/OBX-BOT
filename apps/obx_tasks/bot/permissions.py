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
    raid_role_id = settings.RAID_ROLE_ID

    if isinstance(member, discord.Member):
        if raid_role_id:
            user_role_ids = {str(role.id) for role in member.roles}
            if str(raid_role_id) in user_role_ids:
                return True
        # Also support matching by role name
        for r in member.roles:
            if r.name in ("⚡ OBX Raider", "OBX Raider"):
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

    await handle_join_raid_click(interaction)
    return False
