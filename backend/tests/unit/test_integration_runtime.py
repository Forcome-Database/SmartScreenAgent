import pytest

from backend.tests.integration.runtime import require_service


def test_unavailable_service_skips_in_local_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SMARTSCREEN_REQUIRE_INTEGRATION", raising=False)
    with pytest.raises(pytest.skip.Exception, match="PostgreSQL not reachable"):
        require_service("PostgreSQL", reachable=False)


def test_unavailable_service_fails_in_strict_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SMARTSCREEN_REQUIRE_INTEGRATION", "1")
    with pytest.raises(pytest.fail.Exception, match="PostgreSQL not reachable"):
        require_service("PostgreSQL", reachable=False)


def test_available_service_returns_normally(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SMARTSCREEN_REQUIRE_INTEGRATION", "1")
    require_service("PostgreSQL", reachable=True)


def test_strict_mode_fails_a_run_with_skips(monkeypatch: pytest.MonkeyPatch) -> None:
    from backend.tests.integration.runtime import strict_exit_status

    monkeypatch.setenv("SMARTSCREEN_REQUIRE_INTEGRATION", "1")
    assert strict_exit_status(current_status=0, skipped_count=1) == 1
