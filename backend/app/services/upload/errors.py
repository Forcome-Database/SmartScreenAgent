class UploadValidationError(ValueError):
    def __init__(self, *, status_code: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message

    @property
    def detail(self) -> dict[str, str]:
        return {"code": self.code, "message": self.message}
