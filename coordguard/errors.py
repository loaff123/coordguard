"""Stable error categories for bounded, non-executable inputs."""

class ValidationError(ValueError):
    code = 'invalid_input'


class ResourceLimit(RuntimeError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
