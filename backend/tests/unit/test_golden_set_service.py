# backend/tests/unit/test_golden_set_service.py
from backend.app.services.golden_set import (
    GoldenImportTooLarge,
    InvalidCSV,
    parse_golden_csv,
)


def _csv(*rows: str) -> bytes:
    return ("candidate_id,jd_code,label\n" + "\n".join(rows) + "\n").encode("utf-8")


def test_parse_valid_and_row_errors():
    parsed, errors = parse_golden_csv(
        _csv("1,FT,advance", "2,FT,reject", "x,FT,advance", "3,FT,bogus", "4,,advance"),
        max_rows=100,
    )
    assert [(p.candidate_id, p.jd_code, p.label) for p in parsed] == [
        (1, "FT", "advance"),
        (2, "FT", "reject"),
    ]
    reasons = {(e.row, e.reason) for e in errors}
    assert reasons == {(3, "invalid_candidate_id"), (4, "invalid_label"), (5, "missing_jd_code")}


def test_parse_missing_header_raises():
    import pytest

    with pytest.raises(InvalidCSV):
        parse_golden_csv(b"a,b,c\n1,2,3\n", max_rows=100)


def test_parse_row_cap():
    import pytest

    rows = [f"{i},FT,advance" for i in range(3)]
    with pytest.raises(GoldenImportTooLarge):
        parse_golden_csv(_csv(*rows), max_rows=2)
