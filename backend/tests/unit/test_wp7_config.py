import pytest
from pydantic import ValidationError

from backend.app.config import get_settings
from backend.tests.test_bootstrap import TEST_ENV_DEFAULTS


@pytest.fixture(autouse=True)
def _isolate_settings(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_wp7_settings_defaults(monkeypatch):
    names = (
        "LLM_BUDGET_WARN_RATIO",
        "LLM_BUDGET_RECONCILE_MAX_PERIODS_PER_RUN",
        "LLM_USAGE_PENDING_TIMEOUT_SECONDS",
        "LLM_USAGE_FINALIZE_MAX_RETRIES",
        "QUALITY_F1_TARGET",
        "QUALITY_EVIDENCE_COVERAGE_TARGET",
        "QUALITY_CONFIDENCE_MIN_BUCKET_SIZE",
        "CROSS_ENGINE_MODEL",
        "CROSS_ENGINE_SAMPLE_PERCENT",
        "CROSS_ENGINE_LOW_CONFIDENCE",
        "CROSS_ENGINE_DIFF_THRESHOLD",
        "CROSS_ENGINE_MAX_ATTEMPTS",
        "CROSS_ENGINE_LEASE_SECONDS",
        "CROSS_ENGINE_SWEEP_INTERVAL_SECONDS",
        "CROSS_ENGINE_BACKFILL_MAX",
    )
    for name in names:
        monkeypatch.delenv(name, raising=False)

    settings = get_settings()

    assert settings.LLM_BUDGET_WARN_RATIO == 0.80
    assert settings.LLM_BUDGET_RECONCILE_MAX_PERIODS_PER_RUN == 31
    assert settings.LLM_USAGE_PENDING_TIMEOUT_SECONDS == 600
    assert settings.LLM_USAGE_FINALIZE_MAX_RETRIES == 3
    assert settings.QUALITY_F1_TARGET == 0.75
    assert settings.QUALITY_EVIDENCE_COVERAGE_TARGET == 0.95
    assert settings.QUALITY_CONFIDENCE_MIN_BUCKET_SIZE == 10
    assert settings.CROSS_ENGINE_MODEL == ""
    assert settings.CROSS_ENGINE_SAMPLE_PERCENT == 10
    assert settings.CROSS_ENGINE_LOW_CONFIDENCE == 0.60
    assert settings.CROSS_ENGINE_DIFF_THRESHOLD == 10
    assert settings.CROSS_ENGINE_MAX_ATTEMPTS == 3
    assert settings.CROSS_ENGINE_LEASE_SECONDS == 900
    assert settings.CROSS_ENGINE_SWEEP_INTERVAL_SECONDS == 60
    assert settings.CROSS_ENGINE_BACKFILL_MAX == 500


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("LLM_BUDGET_WARN_RATIO", "0"),
        ("QUALITY_F1_TARGET", "1.1"),
        ("QUALITY_CONFIDENCE_MIN_BUCKET_SIZE", "0"),
        ("CROSS_ENGINE_SAMPLE_PERCENT", "101"),
        ("CROSS_ENGINE_LEASE_SECONDS", "600"),
        ("CROSS_ENGINE_MAX_ATTEMPTS", "0"),
        ("CROSS_ENGINE_DIFF_THRESHOLD", "inf"),
        ("LLM_BUDGET_WARN_RATIO", "NaN"),
        ("DAILY_LLM_BUDGET_CNY", "-1"),
        ("MONTHLY_LLM_BUDGET_CNY", "inf"),
    ],
)
def test_wp7_settings_reject_invalid_values(monkeypatch, name, value):
    monkeypatch.setenv(name, value)

    with pytest.raises(ValidationError):
        get_settings()


def test_enabled_cross_engine_model_must_differ_from_primary_judge(monkeypatch):
    primary_model = "explicit-primary-judge"
    monkeypatch.setenv("LLM_MODEL_JUDGE", primary_model)
    monkeypatch.setenv("CROSS_ENGINE_MODEL", primary_model)

    with pytest.raises(ValidationError):
        get_settings()


def test_all_configured_primary_models_must_have_prices(monkeypatch):
    monkeypatch.setenv(
        "LLM_PRICE_CNY_PER_MILLION_JSON",
        '{"test-extract":{"input":1.000000,"output":2.000000}}',
    )

    with pytest.raises(ValidationError, match="price"):
        get_settings()


def test_rate_overflow_is_a_settings_validation_error(monkeypatch):
    price_json = TEST_ENV_DEFAULTS["LLM_PRICE_CNY_PER_MILLION_JSON"].replace(
        "1.000000", "1e100", 1
    )
    monkeypatch.setenv("LLM_PRICE_CNY_PER_MILLION_JSON", price_json)

    with pytest.raises(ValidationError, match="price"):
        get_settings()


def test_extreme_rate_exponent_is_a_settings_validation_error(monkeypatch):
    price_json = TEST_ENV_DEFAULTS["LLM_PRICE_CNY_PER_MILLION_JSON"].replace(
        "1.000000", "1e999999999999999999999999999999999999", 1
    )
    monkeypatch.setenv("LLM_PRICE_CNY_PER_MILLION_JSON", price_json)

    with pytest.raises(ValidationError, match="price"):
        get_settings()


def test_enabled_secondary_model_must_have_a_price(monkeypatch):
    primary_judge = "explicit-primary-judge"
    monkeypatch.setenv("LLM_MODEL_JUDGE", primary_judge)
    monkeypatch.setenv("CROSS_ENGINE_MODEL", "missing-secondary")
    monkeypatch.setenv(
        "LLM_PRICE_CNY_PER_MILLION_JSON",
        (
            '{"test-extract":{"input":1.000000,"output":2.000000},'
            '"test-extract-fallback":{"input":1.000000,"output":2.000000},'
            f'"{primary_judge}":{{"input":1.000000,"output":2.000000}},'
            '"test-judge-fallback":{"input":1.000000,"output":2.000000},'
            '"test-light":{"input":1.000000,"output":2.000000}}'
        ),
    )

    with pytest.raises(ValidationError, match="price"):
        get_settings()


def test_bootstrap_has_deterministic_wp7_environment():
    price = (
        '{"test-extract":{"input":1.000000,"output":2.000000},'
        '"test-extract-fallback":{"input":1.000000,"output":2.000000},'
        '"test-judge":{"input":1.000000,"output":2.000000},'
        '"test-judge-fallback":{"input":1.000000,"output":2.000000},'
        '"test-light":{"input":1.000000,"output":2.000000},'
        '"test-secondary":{"input":1.000000,"output":2.000000}}'
    )
    assert TEST_ENV_DEFAULTS["LLM_PRICE_CNY_PER_MILLION_JSON"] == price
    assert {
        "LLM_BUDGET_WARN_RATIO": "0.80",
        "LLM_BUDGET_RECONCILE_MAX_PERIODS_PER_RUN": "31",
        "LLM_USAGE_PENDING_TIMEOUT_SECONDS": "600",
        "LLM_USAGE_FINALIZE_MAX_RETRIES": "3",
        "QUALITY_F1_TARGET": "0.75",
        "QUALITY_EVIDENCE_COVERAGE_TARGET": "0.95",
        "QUALITY_CONFIDENCE_MIN_BUCKET_SIZE": "10",
        "CROSS_ENGINE_MODEL": "test-secondary",
        "CROSS_ENGINE_SAMPLE_PERCENT": "10",
        "CROSS_ENGINE_LOW_CONFIDENCE": "0.60",
        "CROSS_ENGINE_DIFF_THRESHOLD": "10",
        "CROSS_ENGINE_MAX_ATTEMPTS": "3",
        "CROSS_ENGINE_LEASE_SECONDS": "900",
        "CROSS_ENGINE_SWEEP_INTERVAL_SECONDS": "60",
        "CROSS_ENGINE_BACKFILL_MAX": "500",
    }.items() <= TEST_ENV_DEFAULTS.items()
