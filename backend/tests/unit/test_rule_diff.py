from backend.app.services.read.rule_diff import diff_schemas


def _schema(**over):
    base = {
        "passing_threshold": 40,
        "total_score": 100,
        "hard_filters": [{"id": "age_max", "rule": "age <= 45", "action": "reject"}],
        "rule_dimensions": [{"id": "trade", "name": "Trade", "weight": 25}],
        "judge_dimensions": [{"id": "independence", "name": "Independence", "weight": 5}],
        "grade_thresholds": [{"grade": "L5", "min": 90}, {"grade": "L1", "min": 40}],
    }
    base.update(over)
    return base


def test_no_change_returns_empty():
    assert diff_schemas(_schema(), _schema()) == []


def test_reorder_is_no_change():
    a = _schema()
    b = _schema(grade_thresholds=[{"grade": "L1", "min": 40}, {"grade": "L5", "min": 90}])
    assert diff_schemas(a, b) == []


def test_scalar_change():
    changes = diff_schemas(_schema(), _schema(passing_threshold=50))
    assert {"path": "passing_threshold", "kind": "changed", "before": 40, "after": 50} in changes


def test_dimension_added_removed_changed():
    a = _schema()
    b = _schema(
        rule_dimensions=[
            {"id": "trade", "name": "Trade", "weight": 30},
            {"id": "edu", "name": "Edu", "weight": 12},
        ]
    )
    changes = diff_schemas(a, b)
    paths = {(c["path"], c["kind"]) for c in changes}
    assert ("rule_dimensions[trade]", "changed") in paths
    assert ("rule_dimensions[edu]", "added") in paths


def test_grade_removed():
    a = _schema()
    b = _schema(grade_thresholds=[{"grade": "L5", "min": 90}])
    changes = diff_schemas(a, b)
    assert ("grade_thresholds[L1]", "removed") in {(c["path"], c["kind"]) for c in changes}
