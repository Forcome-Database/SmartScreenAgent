class LLMError(RuntimeError):
    """Base class for stable LLM gateway and structured-output failures."""


class LLMUnavailableError(LLMError):
    """A retryable provider transport, rate-limit, or server failure."""


class LLMConfigurationError(LLMError):
    """A non-retryable authentication, authorization, or request configuration failure."""


class LLMInvalidResponseError(LLMError):
    """The provider response is structurally incomplete before schema validation."""


class LLMInvalidOutputError(LLMError):
    """The model completion does not satisfy local deterministic validation."""
