class OperationError(Exception):
    def __init__(self, message: str, code: str = "INVALID_OPERATION", status: int = 409):
        super().__init__(message)
        self.code = code
        self.status = status
