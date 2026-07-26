from decimal import Decimal, localcontext

import pytest

from backend.app.services.llm.pricing import (
    InvalidPriceBook,
    ModelPriceMissing,
    estimate_cost,
    parse_price_book,
)


def test_parse_price_book_normalizes_rates_and_estimates_exact_cost():
    prices = parse_price_book('{"m":{"input":1.25,"output":10}}')

    price = prices.require("m")
    assert price.input_cny_per_million == Decimal("1.250000")
    assert price.output_cny_per_million == Decimal("10.000000")
    assert estimate_cost(price, 1, 1) == Decimal("0.000011250000")


def test_parse_price_book_rejects_rate_that_overflows_decimal_storage():
    with pytest.raises(InvalidPriceBook):
        parse_price_book('{"m":{"input":1e100,"output":2}}')


def test_parse_price_book_rejects_extreme_json_exponent_as_typed_error():
    with pytest.raises(InvalidPriceBook):
        parse_price_book(
            '{"m":{"input":1e999999999999999999999999999999999999,"output":2}}'
        )


def test_parse_price_book_accepts_numeric_18_6_boundary():
    price = parse_price_book(
        '{"m":{"input":999999999999.999999,"output":999999999999.999999}}'
    ).require("m")

    assert price.input_cny_per_million == Decimal("999999999999.999999")
    assert price.output_cny_per_million == Decimal("999999999999.999999")


def test_parse_price_book_rejects_rate_above_numeric_18_6_range():
    with pytest.raises(InvalidPriceBook):
        parse_price_book('{"m":{"input":1000000000000,"output":2}}')


@pytest.mark.parametrize(
    "raw",
    [
        '{"m":',
        '{"m":{"input":1.0000001,"output":2}}',
        '{"m":{"input":-1,"output":2}}',
        '{"m":{"input":true,"output":2}}',
        '{"m":{"input":NaN,"output":2}}',
        "[]",
        "{}",
        '{" ":{"input":1,"output":2}}',
        '{"m":{"input":1}}',
        '{"m":{"input":1,"output":2,"cached":3}}',
        '{"m":{"input":1,"output":2},"m":{"input":3,"output":4}}',
    ],
)
def test_parse_price_book_rejects_invalid_json_or_rates(raw):
    with pytest.raises(InvalidPriceBook):
        parse_price_book(raw)


def test_require_raises_typed_error_for_missing_model():
    prices = parse_price_book('{"m":{"input":1,"output":2}}')

    with pytest.raises(ModelPriceMissing):
        prices.require("other")


def test_estimate_cost_returns_none_when_usage_is_unknown():
    price = parse_price_book('{"m":{"input":1,"output":2}}').require("m")

    assert estimate_cost(price, None, 1) is None
    assert estimate_cost(price, 1, None) is None


@pytest.mark.parametrize(
    "input_tokens,output_tokens",
    [
        (-1, 0),
        (0, -1),
        (True, 0),
        (0, False),
        (2_147_483_648, 0),
        (0, 2_147_483_648),
    ],
)
def test_estimate_cost_rejects_invalid_token_counts(input_tokens, output_tokens):
    price = parse_price_book('{"m":{"input":1,"output":2}}').require("m")

    with pytest.raises(InvalidPriceBook):
        estimate_cost(price, input_tokens, output_tokens)


def test_estimate_cost_rejects_unpersistable_max_rate_and_token_combination():
    price = parse_price_book(
        '{"m":{"input":999999999999.999999,"output":999999999999.999999}}'
    ).require("m")

    with localcontext() as context:
        context.prec = 6
        with pytest.raises(InvalidPriceBook):
            estimate_cost(price, 2_147_483_647, 2_147_483_647)


def test_estimate_cost_accepts_postgresql_integer_boundary_when_cost_fits():
    price = parse_price_book('{"m":{"input":0,"output":0}}').require("m")

    assert estimate_cost(price, 2_147_483_647, 2_147_483_647) == Decimal(
        "0.000000000000"
    )


def test_estimate_cost_accepts_numeric_24_12_boundary():
    price = parse_price_book(
        '{"m":{"input":999999000000.999999,"output":0}}'
    ).require("m")

    assert estimate_cost(price, 1_000_001, 0) == Decimal("999999999999.999999999999")


def test_estimate_cost_rejects_one_quantum_above_numeric_24_12_boundary():
    price = parse_price_book(
        '{"m":{"input":500000000000.000000,"output":0}}'
    ).require("m")

    with pytest.raises(InvalidPriceBook):
        estimate_cost(price, 2_000_000, 0)


def test_price_book_models_are_immutable():
    prices = parse_price_book('{"m":{"input":1,"output":2}}')

    with pytest.raises(TypeError):
        prices.models["other"] = prices.require("m")
