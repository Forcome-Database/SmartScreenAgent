import pytest
from pydantic import ValidationError

from backend.app.config import SYNC_HARD_TIME_LIMIT_SECONDS, Settings, get_settings


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
        # The upper bound, which only this field has. `le=10` is what stops a
        # typo turning the replay sweeper's bounded re-drive into an unbounded
        # one; a lower bound alone would leave it uncovered.
        ("SYNC_MAX_ITEM_ATTEMPTS", "11"),
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


def _settings_with(monkeypatch, **env: str) -> Settings:
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    get_settings.cache_clear()
    try:
        return get_settings()
    finally:
        get_settings.cache_clear()


@pytest.mark.parametrize(
    "interval",
    [
        # What design §14.3 used to recommend for rollout: "a low interval".
        "300",
        # The boundary. The limit must be EXCEEDED, not merely reached: equal
        # means the next tick is published at the instant the previous run is
        # being killed, which is the same race one second late.
        str(SYNC_HARD_TIME_LIMIT_SECONDS),
    ],
)
def test_enabled_sync_refuses_an_interval_the_hard_time_limit_can_outlive(
    monkeypatch, interval: str
) -> None:
    """A run that outlives its own interval stacks; that must fail at boot.

    The invariant used to be a comment plus a test asserting it of the
    CONFIGURED interval — which pins it exactly where it already holds and can
    never see the production misconfiguration. Only a validator sees that one.
    """
    monkeypatch.setenv("DINGTALK_SYNC_ENABLED", "true")
    monkeypatch.setenv("DINGTALK_SYNC_INTERVAL_SECONDS", interval)
    get_settings.cache_clear()
    try:
        with pytest.raises(ValidationError) as excinfo:
            get_settings()
    finally:
        get_settings.cache_clear()

    # Both settings by name: the interval alone is not wrong, and an operator
    # who cannot see which pair conflicts cannot tell which one to change.
    message = str(excinfo.value)
    assert "DINGTALK_SYNC_INTERVAL_SECONDS" in message
    assert "DINGTALK_SYNC_ENABLED" in message
    assert str(SYNC_HARD_TIME_LIMIT_SECONDS) in message


def test_enabled_sync_accepts_the_first_interval_above_the_hard_time_limit(
    monkeypatch,
) -> None:
    settings = _settings_with(
        monkeypatch,
        DINGTALK_SYNC_ENABLED="true",
        DINGTALK_SYNC_INTERVAL_SECONDS=str(SYNC_HARD_TIME_LIMIT_SECONDS + 1),
    )

    assert settings.DINGTALK_SYNC_ENABLED is True
    assert settings.DINGTALK_SYNC_INTERVAL_SECONDS == SYNC_HARD_TIME_LIMIT_SECONDS + 1


def test_a_short_interval_is_allowed_while_sync_is_disabled(monkeypatch) -> None:
    """The switch-off case must stay unblocked.

    With sync disabled no Beat entry exists, so the interval schedules nothing
    and cannot stack anything. Refusing to boot over a dormant value would take
    down deployments that never enabled the feature — and this suite, which
    pins `DINGTALK_SYNC_ENABLED=false`.
    """
    settings = _settings_with(
        monkeypatch,
        DINGTALK_SYNC_ENABLED="false",
        DINGTALK_SYNC_INTERVAL_SECONDS="300",
    )

    assert settings.DINGTALK_SYNC_ENABLED is False
    assert settings.DINGTALK_SYNC_INTERVAL_SECONDS == 300
