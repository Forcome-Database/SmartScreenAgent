from backend.tests.test_bootstrap import TEST_ENV_DEFAULTS, apply_test_environment
from scripts.verify_external_contracts import configuration_errors


def test_external_contract_configuration_rejects_defaults() -> None:
    errors = configuration_errors(TEST_ENV_DEFAULTS)

    assert "MINERU_MODE must be http" in errors
    assert any("NEWAPI_API_KEY" in error for error in errors)


def test_external_contract_configuration_accepts_explicit_runtime_values() -> None:
    environ = {
        "MINERU_MODE": "http",
        "MINERU_BASE_URL": "https://mineru.internal",
        "NEWAPI_BASE_URL": "https://newapi.internal/v1",
        "NEWAPI_API_KEY": "sk-live-redacted",
        "LLM_MODEL_EXTRACT": "extract-primary",
        "LLM_MODEL_EXTRACT_FALLBACK": "extract-fallback",
        "LLM_MODEL_JUDGE": "judge-primary",
        "LLM_MODEL_JUDGE_FALLBACK": "judge-fallback",
    }

    assert configuration_errors(environ) == []


def test_external_contract_bootstrap_preserves_explicit_service_settings() -> None:
    environ = {
        "SMARTSCREEN_EXTERNAL_CONTRACT": "1",
        "MINERU_MODE": "http",
        "MINERU_BASE_URL": "https://mineru.internal",
        "NEWAPI_BASE_URL": "https://newapi.internal/v1",
    }

    apply_test_environment(environ)

    assert environ["MINERU_MODE"] == "http"
    assert environ["MINERU_BASE_URL"] == "https://mineru.internal"
    assert environ["NEWAPI_BASE_URL"] == "https://newapi.internal/v1"
