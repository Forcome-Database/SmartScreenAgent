from __future__ import annotations

from typing import Any

_SCALARS = ("passing_threshold", "total_score")
_KEYED = (
    ("hard_filters", "id"),
    ("rule_dimensions", "id"),
    ("judge_dimensions", "id"),
    ("grade_thresholds", "grade"),
)


def _index(items: list[dict] | None, key: str) -> dict[Any, dict]:
    return {item[key]: item for item in (items or []) if key in item}


def diff_schemas(from_schema: dict, to_schema: dict) -> list[dict]:
    """Structural diff of two rule schema_json objects. Pure, deterministic;
    collections are matched by id/grade so reordering alone yields no change."""
    changes: list[dict] = []

    for path in _SCALARS:
        before, after = from_schema.get(path), to_schema.get(path)
        if before != after:
            changes.append({"path": path, "kind": "changed", "before": before, "after": after})

    for collection, key in _KEYED:
        before_index = _index(from_schema.get(collection), key)
        after_index = _index(to_schema.get(collection), key)
        for missing in sorted(set(before_index) - set(after_index), key=str):
            changes.append(
                {"path": f"{collection}[{missing}]", "kind": "removed",
                 "before": before_index[missing], "after": None}
            )
        for added in sorted(set(after_index) - set(before_index), key=str):
            changes.append(
                {"path": f"{collection}[{added}]", "kind": "added",
                 "before": None, "after": after_index[added]}
            )
        for common in sorted(set(before_index) & set(after_index), key=str):
            if before_index[common] != after_index[common]:
                changes.append(
                    {"path": f"{collection}[{common}]", "kind": "changed",
                     "before": before_index[common], "after": after_index[common]}
                )

    return changes
