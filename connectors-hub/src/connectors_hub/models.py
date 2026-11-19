"""Public data models for the local API."""

from __future__ import annotations

import re
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ConnectorId(str, Enum):
    DROPBOX = "dropbox"
    GITHUB = "github"
    GMAIL = "gmail"
    GOOGLE_CALENDAR = "google_calendar"
    GOOGLE_DRIVE = "google_drive"
    GOOGLE_DOCS = "google_docs"
    GOOGLE_SHEETS = "google_sheets"
    GOOGLE_SLIDES = "google_slides"
    LINEAR = "linear"
    NOTION = "notion"
    SUPABASE = "supabase"
    VERCEL = "vercel"


class ConnectorState(str, Enum):
    UNCONFIGURED = "unconfigured"
    CONFIGURED = "configured"
    NEEDS_RESOURCE_CONFIG = "needs_resource_config"


class ConnectorInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: ConnectorId
    name: str
    state: ConnectorState
    auth_group: str
    search_supported: bool
    read_supported: bool
    notes: list[str] = Field(default_factory=list)


class SearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=1, max_length=500)
    connectors: list[ConnectorId] | None = None
    limit: int = Field(default=10, ge=1, le=50)
    # Supabase needs an explicit allowlisted table; it has no safe global search.
    resource: str | None = Field(default=None, min_length=1, max_length=100)

    @field_validator("query")
    @classmethod
    def query_must_not_be_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("query must contain non-whitespace characters")
        return value


class SearchResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    connector: ConnectorId
    id: str
    title: str
    url: str | None = None
    snippet: str | None = None
    updated_at: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class ReadRequest(BaseModel):
    """Read one explicitly identified resource through a connector-safe route."""

    model_config = ConfigDict(extra="forbid")

    connector: ConnectorId
    resource_type: Literal[
        "file",
        "issue",
        "repository",
        "message",
        "event",
        "document",
        "spreadsheet",
        "presentation",
        "page",
        "row",
        "project",
    ]
    resource_id: str = Field(min_length=1, max_length=500)
    # Supabase row reads require the allowlisted table name as well as its row id.
    resource: str | None = Field(default=None, min_length=1, max_length=100)

    @field_validator("resource_id")
    @classmethod
    def resource_id_must_not_be_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("resource_id must contain non-whitespace characters")
        if any(ord(ch) < 32 or ord(ch) == 127 for ch in value):
            raise ValueError("resource_id must not contain control characters")
        return value


class ReadResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    connector: ConnectorId
    resource_type: str
    resource_id: str
    data: Any


class ReasoningRequest(BaseModel):
    """One bounded model inference call; remote processing is never implicit."""

    model_config = ConfigDict(extra="forbid")

    provider: Literal["local", "nvidia"] = "local"
    mode: Literal["general", "gaming_case_study", "life_lessons", "tech_as_tool"] = (
        "general"
    )
    prompt: str = Field(min_length=1, max_length=60_000)
    system_prompt: str | None = Field(default=None, max_length=4_000)
    model: str | None = Field(default=None, min_length=1, max_length=120)
    temperature: float = Field(default=0.2, ge=0.0, le=1.5)
    max_tokens: int = Field(default=512, ge=1, le=2_048)
    allow_remote_processing: bool = False

    @field_validator("prompt")
    @classmethod
    def prompt_must_not_be_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("prompt must contain non-whitespace characters")
        return value

    @field_validator("model")
    @classmethod
    def model_name_must_be_simple(cls, value: str | None) -> str | None:
        if value is not None and not re.fullmatch(
            r"[A-Za-z0-9][A-Za-z0-9._/-]{0,119}", value
        ):
            raise ValueError("model must be a simple provider model identifier")
        return value


class ReasoningResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: Literal["local", "nvidia"]
    mode: Literal["general", "gaming_case_study", "life_lessons", "tech_as_tool"]
    model: str
    text: str
    usage: dict[str, int] | None = None


class SearchFailure(BaseModel):
    model_config = ConfigDict(extra="forbid")

    connector: ConnectorId
    code: str
    message: str


class SearchResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str
    results: list[SearchResult] = Field(default_factory=list)
    failures: list[SearchFailure] = Field(default_factory=list)
    skipped: list[ConnectorId] = Field(default_factory=list)
    searched: list[ConnectorId] = Field(default_factory=list)


class ActionProposalRequest(BaseModel):
    """A raw provider API mutation proposal; never executed by an HTTP route."""

    model_config = ConfigDict(extra="forbid")

    connector: ConnectorId
    summary: str = Field(min_length=8, max_length=240)
    method: Literal["POST", "PUT", "PATCH", "DELETE"]
    path: str = Field(min_length=2, max_length=512)
    query: dict[str, str] = Field(default_factory=dict, max_length=30)
    payload: dict[str, Any] | list[Any] | None = None
    expires_in_minutes: int = Field(default=10, ge=1, le=30)


class ActionProposalResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    connector: ConnectorId
    summary: str
    method: str
    path: str
    status: str
    request_hash: str
    created_at: str
    expires_at: str
    approval_instructions: str


class ActionStatusResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    connector: ConnectorId
    summary: str
    method: str
    path: str
    status: str
    request_hash: str
    created_at: str
    expires_at: str
    response_status: int | None = None
    completed_at: str | None = None
