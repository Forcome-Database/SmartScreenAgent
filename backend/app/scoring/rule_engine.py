from __future__ import annotations

from typing import Any, Callable

from backend.app.rules.schema import RuleDimension

# 全局方法注册表。方法模块（lookup/tiered_keyword/experience_years）在文件底部
# 被显式 import，触发各自的 @register 装饰器填充本字典。
METHODS: dict[str, Callable[[dict[str, Any], RuleDimension], dict[str, Any]]] = {}


def register(name: str):
    def deco(fn):
        METHODS[name] = fn
        return fn
    return deco


def score_dimensions(
    candidate: dict[str, Any], dims: list[RuleDimension]
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for d in dims:
        fn = METHODS.get(d.method)
        if fn is None:
            raise NotImplementedError(f"rule method {d.method} not registered")
        res = fn(candidate, d)
        out.append({"id": d.id, "name": d.name, "weight": d.weight, **res})
    return out


# Method modules: importing each runs its @register decorator → populates METHODS.
from backend.app.scoring.methods import lookup  # noqa: F401,E402
from backend.app.scoring.methods import tiered_keyword  # noqa: F401,E402
from backend.app.scoring.methods import experience_years  # noqa: F401,E402
