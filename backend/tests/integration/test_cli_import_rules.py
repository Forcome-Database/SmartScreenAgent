from pathlib import Path

import pytest
from sqlalchemy import select
from typer.testing import CliRunner

from backend.app.cli.import_rules import cli
from backend.app.models import JD, RuleVersion

XLSX = Path(__file__).parents[3] / "招聘JD整理-智能筛简历.xlsx"
EXPECTED_CODES = {"FOREIGN_TRADE", "LOGISTICS", "SOURCING_PRODUCT", "QC", "SQE", "OEM_PROJECT"}


@pytest.mark.integration
@pytest.mark.skipif(not XLSX.exists(), reason="xlsx missing")
@pytest.mark.asyncio
async def test_cli_import_rules_creates_rule_versions(db_session):
    """Run the CLI; verify 6 JDs + 6 RuleVersions persisted and JDs point at active rule."""
    runner = CliRunner()
    result = runner.invoke(cli, ["import-rules", str(XLSX)])
    assert result.exit_code == 0, result.stdout
    assert "FOREIGN_TRADE" in result.stdout

    # The CLI used its own AsyncSession (separate from db_session) — both point at same DB.
    # Verify via db_session query.
    jds = (await db_session.execute(select(JD))).scalars().all()
    rvs = (await db_session.execute(select(RuleVersion))).scalars().all()
    jd_codes = {j.code for j in jds}
    assert EXPECTED_CODES.issubset(jd_codes)
    assert len(rvs) >= 6
    # Every JD should have an active rule version
    for jd in jds:
        if jd.code in EXPECTED_CODES:
            assert jd.active_rule_version_id is not None
