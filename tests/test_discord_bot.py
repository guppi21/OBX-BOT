import pytest
from unittest.mock import MagicMock
import discord
from apps.obx_tasks.bot.permissions import is_admin
from apps.obx_tasks.bot.client import create_discord_bot


def test_discord_bot_initialization():
    bot = create_discord_bot()
    assert bot is not None
    commands = [cmd.name for cmd in bot.tree.get_commands()]
    assert "tasks" in commands
    assert "task" in commands
    assert "submit" in commands
    assert "my-submissions" in commands
    assert "admin-create-task" in commands
    assert "admin-submissions" in commands
    assert "admin-review" in commands
    assert "admin-task-edit" in commands
    assert "admin-edit-task" in commands
    assert "admin-task-status" in commands
    assert "admin-task-history" in commands
    assert "admin-health" in commands
    assert len(commands) >= 11


def test_permission_admin_check():
    # Admin member
    mock_interaction = MagicMock(spec=discord.Interaction)
    mock_user = MagicMock(spec=discord.Member)
    mock_user.guild_permissions.administrator = True
    mock_user.guild_permissions.manage_guild = True
    mock_interaction.user = mock_user

    assert is_admin(mock_interaction) is True

    # Non-admin member
    mock_non_admin = MagicMock(spec=discord.Member)
    mock_non_admin.guild_permissions.administrator = False
    mock_non_admin.guild_permissions.manage_guild = False
    mock_non_admin.roles = []
    mock_interaction.user = mock_non_admin

    assert is_admin(mock_interaction) is False
