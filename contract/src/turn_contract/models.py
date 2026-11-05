"""Wire models for the gateway <-> agent HTTP turn contract."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class TurnRequest(BaseModel):
    session_id: str
    message_id: str
    text: str
    sender_name: str
    now: datetime


class ToolCall(BaseModel):
    name: str
    args: dict
    ms: int
    ok: bool


class Usage(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0


class TurnResponse(BaseModel):
    reply: str
    tool_calls: list[ToolCall] = Field(default_factory=list)
    usage: Usage = Field(default_factory=Usage)


class ErrorBody(BaseModel):
    code: Literal["monarch_auth", "timeout", "internal"]
    message: str


class ErrorResponse(BaseModel):
    error: ErrorBody


class Health(BaseModel):
    name: str
    framework: str
    model: str


ERROR_STATUS = {"monarch_auth": 502, "timeout": 504, "internal": 500}
