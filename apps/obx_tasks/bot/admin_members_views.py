import math
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional, Tuple

import discord
from discord.ui import View, Button, Select
from sqlalchemy.orm import Session

from packages.database.session import session_scope
from packages.database.models.raider_profile import RaiderProfile
from packages.database.models.submission import TaskSubmission
from packages.database.models.auction import AuctionBid
from packages.database.models.user import User
from packages.shared.config import get_settings
from packages.shared.logging import get_logger
from apps.obx_tasks.bot.views import is_admin
from apps.obx_tasks.services.leaderboard_service import LeaderboardService

logger = get_logger("obx.tasks.bot.admin_members")

COLOR_GOLD = 0xF5A623
COLOR_BLUE = 0x3498DB


@dataclass
class RaiderMemberInfo:
    discord_user_id: str
    display_name: str
    x_handle: Optional[str]
    x_profile_url: Optional[str]
    x_avatar_url: Optional[str]
    balance: int
    task_earnings: int
    tasks_completed: int
    rank: Optional[int]
    status: str
    created_at: Optional[datetime]


def collect_all_raider_members(session: Session, guild: Optional[discord.Guild] = None) -> List[RaiderMemberInfo]:
    """Collect and aggregate all members who have interacted with the OBX raid system."""
    user_ids = set()

    # 1. Registered RaiderProfiles
    for row in session.query(RaiderProfile.discord_user_id).all():
        if row[0]:
            user_ids.add(str(row[0]))

    # 2. Task submissions
    for row in session.query(TaskSubmission.discord_user_id).distinct().all():
        if row[0]:
            user_ids.add(str(row[0]))

    # 3. Users in wallet / user table
    for row in session.query(User.discord_user_id).distinct().all():
        if row[0]:
            user_ids.add(str(row[0]))

    # 4. Auction bidders
    for row in session.query(AuctionBid.discord_user_id).distinct().all():
        if row[0]:
            user_ids.add(str(row[0]))

    # 5. Members with ⚡ OBX Raider role in guild
    if guild:
        raid_role_id = get_settings().RAID_ROLE_ID
        role = guild.get_role(int(raid_role_id)) if raid_role_id else None
        if not role:
            for r in guild.roles:
                if r.name in ("⚡ OBX Raider", "OBX Raider"):
                    role = r
                    break
        if role:
            for m in role.members:
                user_ids.add(str(m.id))

    # Fetch all profiles
    profiles = {
        p.discord_user_id: p
        for p in session.query(RaiderProfile).filter(RaiderProfile.discord_user_id.in_(list(user_ids))).all()
    } if user_ids else {}

    lb_service = LeaderboardService(session)
    settings = get_settings()
    configured_role_id = settings.RAID_ROLE_ID

    members_list = []
    for uid in user_ids:
        prof = profiles.get(uid)
        x_h = prof.twitter_handle if prof else None
        x_url = prof.twitter_profile_url if prof else None
        x_avatar = prof.twitter_avatar_url if prof else None

        # Leaderboard stats
        pos = lb_service.get_user_position(uid)
        bal = pos.total_balance
        earnings = pos.task_earnings
        count = pos.tasks_completed
        rank = pos.rank

        # Guild member lookup for display name and role status
        member = guild.get_member(int(uid)) if (guild and uid.isdigit()) else None
        if member:
            display_name = member.display_name or member.name
            has_role = any(
                r.name in ("⚡ OBX Raider", "OBX Raider") or str(r.id) == str(configured_role_id)
                for r in member.roles
            )
            status = "Active" if has_role else ("Active" if (prof or count > 0) else "Inactive")
            created_at = prof.created_at if prof else member.joined_at
        else:
            display_name = f"User {uid}"
            status = "Active" if (prof or count > 0) else "Inactive"
            created_at = prof.created_at if prof else None

        members_list.append(
            RaiderMemberInfo(
                discord_user_id=uid,
                display_name=display_name,
                x_handle=x_h,
                x_profile_url=x_url,
                x_avatar_url=x_avatar,
                balance=bal,
                task_earnings=earnings,
                tasks_completed=count,
                rank=rank,
                status=status,
                created_at=created_at,
            )
        )

    # Sort members: ranked first (by rank asc), then by balance desc, then tasks completed desc
    members_list.sort(
        key=lambda m: (
            0 if m.rank is not None else 1,
            m.rank if m.rank is not None else 0,
            -m.balance,
            -m.tasks_completed,
            m.display_name.lower(),
        )
    )
    return members_list


def build_members_list_embed(members: List[RaiderMemberInfo], page: int = 0, page_size: int = 6) -> discord.Embed:
    """Build compact and easy-to-scan member directory embed."""
    total_members = len(members)
    total_pages = max(1, math.ceil(total_members / page_size))
    current_page = max(0, min(page, total_pages - 1))

    start_idx = current_page * page_size
    page_items = members[start_idx : start_idx + page_size]

    desc_lines = [
        "**OBX Raider & Member Directory**",
        f"Total Members: **{total_members}**  •  Page **{current_page + 1}/{total_pages}**",
        "",
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
    ]

    if not page_items:
        desc_lines.append("*No members have interacted with the raid system yet.*")
    else:
        for idx, m in enumerate(page_items, start=start_idx + 1):
            x_str = f"@{m.x_handle}" if m.x_handle else "—"
            rank_str = f"#{m.rank}" if m.rank else "—"
            desc_lines.append(
                f"**{idx}. {m.display_name}** (`{m.discord_user_id}`)\n"
                f"🐦 `{x_str}`  •  💰 `{m.balance:,} OBX`  •  🎯 `{m.tasks_completed} tasks`  •  🏆 `{rank_str}`  •  ⚡ `{m.status}`"
            )

    desc_lines.extend([
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        "*Select a member below to inspect full account and X profile details.*",
    ])

    embed = discord.Embed(
        title="👥 RAIDERS & MEMBERS",
        description="\n".join(desc_lines),
        color=COLOR_GOLD,
    )
    embed.set_footer(text="Strictly Restricted: Haveli Owner Admin Role Only • Authoritative Ledger")
    return embed


def build_member_detail_embed(member: RaiderMemberInfo) -> discord.Embed:
    """Build individual member detail view matching required layout."""
    rank_display = f"#{member.rank}" if member.rank else "Unranked"
    x_display = f"@{member.x_handle}" if member.x_handle else "Not Linked"
    if member.x_profile_url:
        x_display = f"@{member.x_handle}\n<{member.x_profile_url}>"

    created_str = member.created_at.strftime("%Y-%m-%d %H:%M UTC") if member.created_at else "Unknown"

    desc = (
        "Discord:\n"
        f"**{member.display_name}** (`{member.discord_user_id}`)\n\n"
        "🐦 **X ACCOUNT**\n"
        f"{x_display}\n\n"
        "💰 **BALANCE**\n"
        f"`{member.balance:,} OBX`\n"
        f"*(Total Approved Rewards: `{member.task_earnings:,} OBX`)*\n\n"
        "🎯 **TASKS COMPLETED**\n"
        f"`{member.tasks_completed}`\n\n"
        "🏆 **CURRENT RANK**\n"
        f"{rank_display}\n\n"
        "⚡ **RAID STATUS**\n"
        f"{member.status}\n\n"
        "📅 **JOINED / CREATED**\n"
        f"{created_str}"
    )

    embed = discord.Embed(
        title="👤 MEMBER",
        description=desc,
        color=COLOR_GOLD,
    )
    if member.x_avatar_url:
        embed.set_thumbnail(url=member.x_avatar_url)

    embed.set_footer(text=f"User ID: {member.discord_user_id} • Haveli Owner Admin Control")
    return embed


class AdminMemberSelect(Select):
    """Dropdown to select a specific member on the current page for detailed inspection."""
    def __init__(self, page_members: List[RaiderMemberInfo], current_page: int):
        options = []
        for m in page_members:
            x_desc = f"@{m.x_handle}" if m.x_handle else "No X"
            desc = f"{x_desc} • {m.balance:,} OBX • Rank #{m.rank or '—'}"[:50]
            options.append(
                discord.SelectOption(
                    label=m.display_name[:25],
                    description=desc,
                    value=str(m.discord_user_id),
                    emoji="👤",
                )
            )

        super().__init__(
            placeholder="Select a member to view full details...",
            min_values=1,
            max_values=1,
            options=options,
            row=0,
        )
        self.page_members = {m.discord_user_id: m for m in page_members}
        self.current_page = current_page

    async def callback(self, interaction: discord.Interaction):
        selected_id = self.values[0]
        member = self.page_members.get(selected_id)
        if not member:
            # Fallback lookup in database
            with session_scope() as session:
                all_m = collect_all_raider_members(session, interaction.guild)
                matches = [m for m in all_m if m.discord_user_id == selected_id]
                member = matches[0] if matches else None

        if not member:
            await interaction.response.send_message("❌ Member not found.", ephemeral=True)
            return

        embed = build_member_detail_embed(member)
        view = AdminMemberDetailView(member=member, page=self.current_page)
        await interaction.response.edit_message(embed=embed, view=view)


class AdminMembersListView(View):
    """Paginated list view of all raid system members."""
    def __init__(self, members: List[RaiderMemberInfo], page: int = 0, page_size: int = 6):
        super().__init__(timeout=None)
        self.members = members
        self.page = page
        self.page_size = page_size
        self.total_pages = max(1, math.ceil(len(members) / page_size))

        # Add member select if items exist
        start_idx = self.page * self.page_size
        page_items = self.members[start_idx : start_idx + self.page_size]
        if page_items:
            self.add_item(AdminMemberSelect(page_items, self.page))

        # Row 1: Pagination
        prev_btn = Button(
            label="PREVIOUS",
            style=discord.ButtonStyle.secondary,
            disabled=(self.page <= 0),
            row=1,
        )
        prev_btn.callback = self.prev_page
        self.add_item(prev_btn)

        page_btn = Button(
            label=f"Page {self.page + 1}/{self.total_pages}",
            style=discord.ButtonStyle.secondary,
            disabled=True,
            row=1,
        )
        self.add_item(page_btn)

        next_btn = Button(
            label="NEXT",
            style=discord.ButtonStyle.secondary,
            disabled=(self.page >= self.total_pages - 1),
            row=1,
        )
        next_btn.callback = self.next_page
        self.add_item(next_btn)

        # Row 2: Controls
        refresh_btn = Button(
            label="Refresh",
            style=discord.ButtonStyle.secondary,
            row=2,
        )
        refresh_btn.callback = self.refresh
        self.add_item(refresh_btn)

        home_btn = Button(
            label="Admin Hub",
            style=discord.ButtonStyle.success,
            row=2,
        )
        home_btn.callback = self.go_home
        self.add_item(home_btn)

    async def prev_page(self, interaction: discord.Interaction):
        if not is_admin(interaction):
            await interaction.response.send_message("❌ Permission Denied.", ephemeral=True)
            return
        new_page = max(0, self.page - 1)
        embed = build_members_list_embed(self.members, page=new_page, page_size=self.page_size)
        view = AdminMembersListView(self.members, page=new_page, page_size=self.page_size)
        await interaction.response.edit_message(embed=embed, view=view)

    async def next_page(self, interaction: discord.Interaction):
        if not is_admin(interaction):
            await interaction.response.send_message("❌ Permission Denied.", ephemeral=True)
            return
        new_page = min(self.total_pages - 1, self.page + 1)
        embed = build_members_list_embed(self.members, page=new_page, page_size=self.page_size)
        view = AdminMembersListView(self.members, page=new_page, page_size=self.page_size)
        await interaction.response.edit_message(embed=embed, view=view)

    async def refresh(self, interaction: discord.Interaction):
        if not is_admin(interaction):
            await interaction.response.send_message("❌ Permission Denied.", ephemeral=True)
            return
        with session_scope() as session:
            refreshed_members = collect_all_raider_members(session, interaction.guild)
        embed = build_members_list_embed(refreshed_members, page=self.page, page_size=self.page_size)
        view = AdminMembersListView(refreshed_members, page=self.page, page_size=self.page_size)
        await interaction.response.edit_message(embed=embed, view=view)

    async def go_home(self, interaction: discord.Interaction):
        if not is_admin(interaction):
            await interaction.response.send_message("❌ Permission Denied.", ephemeral=True)
            return
        from apps.obx_tasks.bot.dashboard_views import create_admin_hub_embed, OBXAdminHubView
        embed = create_admin_hub_embed()
        view = OBXAdminHubView()
        await interaction.response.edit_message(embed=embed, view=view)


class AdminMemberDetailView(View):
    """Detail inspection view for an individual member."""
    def __init__(self, member: RaiderMemberInfo, page: int = 0):
        super().__init__(timeout=None)
        self.member = member
        self.page = page

        back_btn = Button(
            label="Back to Members",
            style=discord.ButtonStyle.primary,
            row=0,
        )
        back_btn.callback = self.go_back
        self.add_item(back_btn)

        home_btn = Button(
            label="Admin Hub",
            style=discord.ButtonStyle.success,
            row=0,
        )
        home_btn.callback = self.go_home
        self.add_item(home_btn)

    async def go_back(self, interaction: discord.Interaction):
        if not is_admin(interaction):
            await interaction.response.send_message("❌ Permission Denied.", ephemeral=True)
            return
        with session_scope() as session:
            members = collect_all_raider_members(session, interaction.guild)
        embed = build_members_list_embed(members, page=self.page)
        view = AdminMembersListView(members, page=self.page)
        await interaction.response.edit_message(embed=embed, view=view)

    async def go_home(self, interaction: discord.Interaction):
        if not is_admin(interaction):
            await interaction.response.send_message("❌ Permission Denied.", ephemeral=True)
            return
        from apps.obx_tasks.bot.dashboard_views import create_admin_hub_embed, OBXAdminHubView
        embed = create_admin_hub_embed()
        view = OBXAdminHubView()
        await interaction.response.edit_message(embed=embed, view=view)


async def handle_admin_members(interaction: discord.Interaction, page: int = 0):
    """Entry point for the Admin Members / Raiders panel."""
    if not is_admin(interaction):
        if hasattr(interaction, "response") and not interaction.response.is_done():
            await interaction.response.send_message("❌ Permission Denied: Administrator role required.", ephemeral=True)
        else:
            await interaction.followup.send("❌ Permission Denied: Administrator role required.", ephemeral=True)
        return

    if hasattr(interaction, "response") and not interaction.response.is_done():
        await interaction.response.defer(ephemeral=True)

    with session_scope() as session:
        members = collect_all_raider_members(session, interaction.guild)

    embed = build_members_list_embed(members, page=page)
    view = AdminMembersListView(members, page=page)

    if hasattr(interaction, "followup"):
        await interaction.followup.send(embed=embed, view=view, ephemeral=True)
    elif hasattr(interaction, "edit_original_response"):
        await interaction.edit_original_response(embed=embed, view=view)
    else:
        await interaction.response.edit_message(embed=embed, view=view)
