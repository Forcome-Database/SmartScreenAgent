class MinerUError(RuntimeError):
    """Base class for stable MinerU adapter failures."""


class MinerUUnavailableError(MinerUError):
    """MinerU cannot be reached or cannot complete within the configured deadline."""


class MinerUContractError(MinerUError):
    """MinerU returned a response that violates the supported protocol contract."""


class MinerUTaskError(MinerUError):
    """MinerU accepted a task but failed to produce a usable result."""


class MinerUResultError(MinerUTaskError):
    """A downloaded MinerU result archive is unsafe or structurally invalid."""
