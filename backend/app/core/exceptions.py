"""Typed application errors. The global handler maps each to a JSON error envelope."""


class AppError(Exception):
    """Base class: carries a machine-readable code and an HTTP status."""

    code: str = "APP_ERROR"
    status_code: int = 500

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class NotFoundError(AppError):
    code = "NOT_FOUND"
    status_code = 404


class ConflictError(AppError):
    """The request is valid but cannot be applied to the current state."""

    code = "CONFLICT"
    status_code = 409


class InvalidEventError(AppError):
    """An injected event references assets or values that do not exist."""

    code = "INVALID_EVENT"
    status_code = 422


class SolverError(AppError):
    """The optimiser failed to return any usable solution."""

    code = "SOLVER_ERROR"
    status_code = 500
