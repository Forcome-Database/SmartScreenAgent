import pytest
from pydantic import ValidationError

from backend.app.config import get_settings


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
