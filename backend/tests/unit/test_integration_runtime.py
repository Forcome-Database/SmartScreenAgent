import pytest

from backend.tests.integration.runtime import require_service, strict_exit_status


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
    monkeypatch.setenv("SMARTSCREEN_REQUIRE_INTEGRATION", "1")
    assert strict_exit_status(current_status=0, skipped_count=1) == 1


@pytest.mark.parametrize(
    "current_status",
    [
        pytest.ExitCode.TESTS_FAILED,
        pytest.ExitCode.INTERRUPTED,
        pytest.ExitCode.INTERNAL_ERROR,
        pytest.ExitCode.USAGE_ERROR,
        pytest.ExitCode.NO_TESTS_COLLECTED,
    ],
)
def test_strict_mode_preserves_nonzero_exit_status(
    monkeypatch: pytest.MonkeyPatch,
    current_status: pytest.ExitCode,
) -> None:
    monkeypatch.setenv("SMARTSCREEN_REQUIRE_INTEGRATION", "1")
    assert strict_exit_status(current_status=int(current_status), skipped_count=1) == int(
        current_status
    )


def test_non_strict_mode_allows_a_run_with_skips(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SMARTSCREEN_REQUIRE_INTEGRATION", raising=False)
    assert strict_exit_status(current_status=0, skipped_count=1) == 0
