# backend/tests/unit/test_golden_metrics.py
from backend.app.services.golden_set import metric_stats


def test_metric_stats_and_zero_denominator():
    s = metric_stats(3, 1, 4, 2)  # tp, fp, tn, fn
    assert s["confusion"] == {"tp": 3, "fp": 1, "tn": 4, "fn": 2}
    assert s["precision"] == 0.75  # 3/(3+1)
    assert s["recall"] == 0.6  # 3/(3+2)
    assert s["f1"] == 2 * 3 / (2 * 3 + 1 + 2)  # 0.666...
    assert s["accuracy"] == (3 + 4) / (3 + 1 + 4 + 2)
    empty = metric_stats(0, 0, 0, 0)
    assert empty["precision"] is None and empty["recall"] is None
    assert empty["f1"] is None and empty["accuracy"] is None
