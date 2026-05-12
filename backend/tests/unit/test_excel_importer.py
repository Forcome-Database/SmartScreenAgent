from pathlib import Path

import pytest

from backend.app.rules.excel_importer import (
    JD_CODE_BY_SHEET,
    import_workbook,
)
from backend.app.rules.schema import RuleSchema

XLSX = Path(__file__).parents[3] / "招聘JD整理-智能筛简历.xlsx"


@pytest.mark.skipif(not XLSX.exists(), reason="HR rule workbook not present")
def test_imports_all_six_position_sheets():
    rules = import_workbook(XLSX)
    sheet_jd_codes = {r.jd_code for r in rules}
    assert sheet_jd_codes == set(JD_CODE_BY_SHEET.values())


@pytest.mark.skipif(not XLSX.exists(), reason="HR rule workbook not present")
def test_foreign_trade_rule_has_age_hard_filter():
    rules = {r.jd_code: r for r in import_workbook(XLSX)}
    ft = rules["FOREIGN_TRADE"]
    age_filters = [h for h in ft.hard_filters if h.audit_tag == "AGE"]
    assert len(age_filters) == 1
    assert "45" in age_filters[0].rule


@pytest.mark.skipif(not XLSX.exists(), reason="HR rule workbook not present")
def test_each_rule_validates_against_schema():
    for rule in import_workbook(XLSX):
        RuleSchema.model_validate(rule.model_dump())


def test_keyword_split_handles_chinese_punctuation():
    from backend.app.rules.excel_importer import _split_keywords
    assert _split_keywords("北美市场、美国外贸,五金工具 / 手工具") == [
        "北美市场",
        "美国外贸",
        "五金工具",
        "手工具",
    ]


def test_score_parse_handles_units():
    from backend.app.rules.excel_importer import _parse_score
    assert _parse_score("4分") == 4.0
    assert _parse_score("14 分") == 14.0
    assert _parse_score("0.6分") == 0.6
    assert _parse_score("18分") == 18.0
