import pytest
from packages.shared.config import Settings


def test_settings_aliases_and_role_id_merging():
    s = Settings(
        _env_file=None,
        OBX_CORE_URL="http://core.internal:8000",
        INTERNAL_API_TOKEN="custom_token_456",
        DISCORD_ADMIN_ROLE_ID="123456789012345678",
    )
    assert s.OBX_CORE_API_URL == "http://core.internal:8000"
    assert s.OBX_CORE_URL == "http://core.internal:8000"
    assert s.OBX_CORE_INTERNAL_AUTH_TOKEN == "custom_token_456"
    assert s.INTERNAL_API_TOKEN == "custom_token_456"
    assert "123456789012345678" in s.DISCORD_ADMIN_ROLE_IDS
    assert s.DISCORD_ADMIN_ROLE_ID == "123456789012345678"


def test_settings_snowflake_validation():
    # Valid numeric snowflake strings
    s = Settings(
        _env_file=None,
        DISCORD_GUILD_ID="999888777666555444",
        DISCORD_ADMIN_ROLE_IDS=["111222333444555666", "222333444555666777"],
    )
    assert s.DISCORD_GUILD_ID == "999888777666555444"
    assert len(s.DISCORD_ADMIN_ROLE_IDS) == 2

    # Invalid non-numeric snowflake
    with pytest.raises(ValueError) as exc:
        Settings(_env_file=None, DISCORD_GUILD_ID="invalid_guild_string")
    assert "must be a numeric Discord snowflake ID" in str(exc.value)


def test_validate_for_discord_bot_fails_on_placeholder_token():
    s = Settings(_env_file=None, DISCORD_BOT_TOKEN="placeholder_token")
    with pytest.raises(ValueError) as exc:
        s.validate_for_discord_bot()
    assert "DISCORD_BOT_TOKEN is missing or set to placeholder" in str(exc.value)
