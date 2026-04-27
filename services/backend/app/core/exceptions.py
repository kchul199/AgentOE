"""Custom exception classes for AgentOE."""
from typing import Any


class AgentOEBaseError(Exception):
    """Base exception for all AgentOE errors."""
    http_status: int = 400
    code: str = "AGENTOE_ERROR"

    def __init__(self, message: str, details: dict[str, Any] | None = None) -> None:
        self.message = message
        self.details = details or {}
        super().__init__(message)


class SessionNotFoundError(AgentOEBaseError):
    http_status = 404
    code = "SESSION_NOT_FOUND"

    def __init__(self, session_id: str) -> None:
        super().__init__(f"Session '{session_id}' not found")


class SessionStateError(AgentOEBaseError):
    http_status = 409
    code = "SESSION_STATE_ERROR"


class PolicyViolationError(AgentOEBaseError):
    http_status = 403
    code = "POLICY_VIOLATION"

    def __init__(self, level: str, reason: str) -> None:
        super().__init__(
            f"Policy Gate {level} rejected: {reason}",
            details={"level": level, "reason": reason},
        )


class KillSwitchActiveError(AgentOEBaseError):
    http_status = 503
    code = "KILL_SWITCH_ACTIVE"


class ConnectorError(AgentOEBaseError):
    http_status = 502
    code = "CONNECTOR_ERROR"


class CircuitBreakerOpenError(AgentOEBaseError):
    http_status = 503
    code = "CIRCUIT_BREAKER_OPEN"


class TenantNotFoundError(AgentOEBaseError):
    http_status = 404
    code = "TENANT_NOT_FOUND"


class AuthenticationError(AgentOEBaseError):
    http_status = 401
    code = "AUTHENTICATION_ERROR"


class AuthorizationError(AgentOEBaseError):
    http_status = 403
    code = "AUTHORIZATION_ERROR"
