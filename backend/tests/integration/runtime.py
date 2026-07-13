import os

import pytest

STRICT_INTEGRATION_ENV = "SMARTSCREEN_REQUIRE_INTEGRATION"


def require_service(name: str, *, reachable: bool) -> None:
    if reachable:
        return
    message = f"{name} not reachable"
    if os.getenv(STRICT_INTEGRATION_ENV) == "1":
        pytest.fail(message)
    pytest.skip(message)


def strict_exit_status(*, current_status: int, skipped_count: int) -> int:
    if (
        os.getenv(STRICT_INTEGRATION_ENV) == "1"
        and current_status == int(pytest.ExitCode.OK)
        and skipped_count
    ):
        return int(pytest.ExitCode.TESTS_FAILED)
    return current_status
