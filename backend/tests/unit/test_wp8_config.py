import pytest
from pydantic import ValidationError

from backend.app.config import Settings, get_settings


def test_mcp_defaults_ship_closed() -> None:
    """The shipped defaults, not the test environment's overrides.

    `TEST_ENV_DEFAULTS` supplies a usable token so the resolver can be tested,
    which would mask a production default that authenticates somebody. Read the
    field defaults directly instead.
    """
    fields = Settings.model_fields

    assert fields["MCP_ENABLED"].default is False
    assert fields["MCP_SERVICE_TOKEN"].default == ""
    assert fields["MCP_SERVICE_ROLE"].default == "mcp_service"


def test_sync_defaults_are_off_and_bounded() -> None:
    settings = get_settings()

    assert settings.DINGTALK_SYNC_ENABLED is False
    assert settings.DINGTALK_SYNC_INTERVAL_SECONDS == 1800
    assert settings.SYNC_OVERLAP_SECONDS == 300
    assert settings.SYNC_MAX_ITEMS_PER_RUN == 200
    assert settings.SYNC_MAX_ITEM_ATTEMPTS == 3
    assert settings.SYNC_REPLAY_INTERVAL_SECONDS == 3600


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("SYNC_OVERLAP_SECONDS", "-1"),
        ("SYNC_MAX_ITEMS_PER_RUN", "0"),
        ("SYNC_MAX_ITEM_ATTEMPTS", "0"),
        ("DINGTALK_SYNC_INTERVAL_SECONDS", "0"),
        ("SYNC_REPLAY_INTERVAL_SECONDS", "0"),
    ],
)
def test_out_of_range_sync_settings_are_rejected(monkeypatch, key: str, value: str) -> None:
    monkeypatch.setenv(key, value)
    get_settings.cache_clear()
    try:
        with pytest.raises(ValidationError):
            get_settings()
    finally:
        get_settings.cache_clear()
