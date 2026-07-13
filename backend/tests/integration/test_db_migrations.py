import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration
REPO_ROOT = Path(__file__).resolve().parents[3]


def _alembic(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["uv", "run", "alembic", *args],
        capture_output=True,
        text=True,
        timeout=90,
        cwd=REPO_ROOT,
    )


def test_alembic_round_trip_from_base() -> None:
    downgrade = _alembic("downgrade", "base")
    assert downgrade.returncode == 0, downgrade.stderr
    upgrade = _alembic("upgrade", "head")
    assert upgrade.returncode == 0, upgrade.stderr
    current = _alembic("current")
    assert current.returncode == 0, current.stderr
    assert "3884ec28fea9" in current.stdout
