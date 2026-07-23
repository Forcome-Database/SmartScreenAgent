from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, DecimalException, localcontext
from json import JSONDecodeError
from types import MappingProxyType
from typing import Any

from backend.app.services.llm.errors import ModelPriceMissing

RATE_QUANTUM = Decimal("0.000001")
COST_QUANTUM = Decimal("0.000000000001")
MAX_COST = Decimal("999999999999.999999999999")
_TOKENS_PER_MILLION = Decimal("1000000")
_MAX_RATE_CNY_PER_MILLION = Decimal("999999999999.999999")
_MAX_TOKEN_COUNT = 2_147_483_647
_COST_CALCULATION_PRECISION = 40


class InvalidPriceBook(ValueError):
    """The configured model price JSON is invalid."""


@dataclass(frozen=True)
class ModelPrice:
    input_cny_per_million: Decimal
    output_cny_per_million: Decimal


@dataclass(frozen=True)
class PriceBook:
    models: Mapping[str, ModelPrice]

    def __post_init__(self) -> None:
        object.__setattr__(self, "models", MappingProxyType(dict(self.models)))

    def require(self, model: str) -> ModelPrice:
        try:
            return self.models[model]
        except KeyError as exc:
            raise ModelPriceMissing(model) from exc


def _reject_nonfinite(value: str) -> None:
    raise InvalidPriceBook(f"non-finite JSON constant: {value}")


def _object_without_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise InvalidPriceBook(f"duplicate model/rate key: {key}")
        result[key] = value
    return result


def _normalize_rate(value: object) -> Decimal:
    if type(value) is not Decimal:
        raise InvalidPriceBook("rates must be JSON numbers")
    if not value.is_finite() or value < 0:
        raise InvalidPriceBook("rates must be finite and nonnegative")
    exponent = value.as_tuple().exponent
    if not isinstance(exponent, int) or exponent < -6:
        raise InvalidPriceBook("rates must have scale no greater than six decimals")
    if value > _MAX_RATE_CNY_PER_MILLION:
        raise InvalidPriceBook("rates must fit Numeric(18,6)")
    try:
        with localcontext() as context:
            context.prec = 18
            return value.quantize(RATE_QUANTUM)
    except DecimalException as exc:
        raise InvalidPriceBook("rates must fit Numeric(18,6)") from exc


def parse_price_book(raw: str) -> PriceBook:
    try:
        parsed = json.loads(
            raw,
            parse_float=Decimal,
            parse_int=Decimal,
            parse_constant=_reject_nonfinite,
            object_pairs_hook=_object_without_duplicates,
        )
    except InvalidPriceBook:
        raise
    except JSONDecodeError as exc:
        raise InvalidPriceBook("invalid price JSON") from exc
    except DecimalException as exc:
        raise InvalidPriceBook("invalid price JSON number") from exc

    if not isinstance(parsed, dict) or not parsed:
        raise InvalidPriceBook("price book must be a non-empty object")

    models: dict[str, ModelPrice] = {}
    for model, rates in parsed.items():
        if not isinstance(model, str) or not model.strip():
            raise InvalidPriceBook("model names must be non-empty strings")
        if not isinstance(rates, dict) or set(rates) != {"input", "output"}:
            raise InvalidPriceBook("each model requires only input/output rates")
        models[model] = ModelPrice(
            input_cny_per_million=_normalize_rate(rates["input"]),
            output_cny_per_million=_normalize_rate(rates["output"]),
        )
    return PriceBook(models)


def estimate_cost(
    price: ModelPrice,
    input_tokens: int | None,
    output_tokens: int | None,
) -> Decimal | None:
    if input_tokens is None or output_tokens is None:
        return None
    if type(input_tokens) is not int or type(output_tokens) is not int:
        raise InvalidPriceBook("token counts must be integers")
    if not 0 <= input_tokens <= _MAX_TOKEN_COUNT or not 0 <= output_tokens <= _MAX_TOKEN_COUNT:
        raise InvalidPriceBook("token counts must be between 0 and 2147483647")
    with localcontext() as context:
        context.prec = _COST_CALCULATION_PRECISION
        cost = (
            Decimal(input_tokens) * price.input_cny_per_million
            + Decimal(output_tokens) * price.output_cny_per_million
        ) / _TOKENS_PER_MILLION
        normalized_cost = cost.quantize(COST_QUANTUM, rounding=ROUND_HALF_UP)
        if normalized_cost > MAX_COST:
            raise InvalidPriceBook("estimated cost must fit Numeric(24,12)")
        return normalized_cost
