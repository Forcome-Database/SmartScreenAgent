from pathlib import Path

from backend.app.rules.excel_importer import import_workbook
from backend.app.rules.schema import RuleSchema

EXPECTED_CODES = {
    "FOREIGN_TRADE",
    "LOGISTICS",
    "SOURCING_PRODUCT",
    "QC",
    "SQE",
    "OEM_PROJECT",
}


def test_imports_all_six_position_sheets(rules_workbook: Path) -> None:
    rules = import_workbook(rules_workbook)
    sheet_jd_codes = {r.jd_code for r in rules}
    assert sheet_jd_codes == EXPECTED_CODES


def test_generated_workbook_has_expected_grade_minimums(rules_workbook: Path) -> None:
    rules = {rule.jd_code: rule for rule in import_workbook(rules_workbook)}

    assert [grade.min for grade in rules["FOREIGN_TRADE"].grade_thresholds] == [
        0.0,
        40.0,
        55.0,
        70.0,
        85.0,
    ]
    assert [grade.min for grade in rules["LOGISTICS"].grade_thresholds] == [
        0.0,
        40.0,
        70.0,
    ]


def test_foreign_trade_rule_has_age_hard_filter(rules_workbook: Path) -> None:
    rules = {r.jd_code: r for r in import_workbook(rules_workbook)}
    ft = rules["FOREIGN_TRADE"]
    age_filters = [h for h in ft.hard_filters if h.audit_tag == "AGE"]
    assert len(age_filters) == 1
    assert "45" in age_filters[0].rule


def test_each_rule_validates_against_schema(rules_workbook: Path) -> None:
    for rule in import_workbook(rules_workbook):
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
