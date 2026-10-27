"""Domain-level API errors. Rendered as {error:{code,message,details}} by api.errors."""

from __future__ import annotations

from typing import Any


class ApiError(Exception):
    status_code: int = 400
    code: str = "BAD_REQUEST"

    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        status_code: int | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.code = code or self.code
        self.status_code = status_code or self.status_code
        self.details = details or {}


class NotFoundError(ApiError):
    status_code = 404
    code = "NOT_FOUND"


class UnauthorizedError(ApiError):
    status_code = 401
    code = "UNAUTHORIZED"


class ForbiddenError(ApiError):
    status_code = 403
    code = "FORBIDDEN"


class ConflictError(ApiError):
    status_code = 409
    code = "CONFLICT"


class PolicyBlockedError(ApiError):
    """A deterministic policy gate refused the action (e.g. capability disabled)."""

    status_code = 403
    code = "POLICY_BLOCKED"


class UnprocessableError(ApiError):
    status_code = 422
    code = "UNPROCESSABLE"
