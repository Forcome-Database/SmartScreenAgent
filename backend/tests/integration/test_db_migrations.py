import subprocess

import pytest


@pytest.mark.integration
def test_alembic_can_show_history() -> None:
    """Smoke: alembic 配置加载无报错。

    Task 3 阶段无 revision 文件，但 `alembic history` 仍应成功返回。
    """
    result = subprocess.run(
        ["uv", "run", "alembic", "history"],
        capture_output=True,
        text=True,
        timeout=60,
        cwd="E:/Project/ForcomeAiTools/SmartScreenAgent",
    )
    assert result.returncode == 0, f"alembic history failed: {result.stderr}"
