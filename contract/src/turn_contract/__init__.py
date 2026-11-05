from turn_contract.errors import install_error_handlers
from turn_contract.messages import format_user_message
from turn_contract.models import (
    ERROR_STATUS,
    ErrorBody,
    ErrorResponse,
    Health,
    ToolCall,
    TurnRequest,
    TurnResponse,
    Usage,
)

AUTH_ERROR_MARKER = "MONARCH_AUTH_REQUIRED"

__all__ = [
    "AUTH_ERROR_MARKER",
    "ERROR_STATUS",
    "ErrorBody",
    "ErrorResponse",
    "Health",
    "ToolCall",
    "TurnRequest",
    "TurnResponse",
    "Usage",
    "format_user_message",
    "install_error_handlers",
]
