"""HTTP request and error bodies for API v1."""

from pydantic import BaseModel, ConfigDict, Field


class ErrorResponse(BaseModel):
    """JSON error body."""

    model_config = ConfigDict(extra="forbid")

    detail: str


class HealthResponse(BaseModel):
    """Process health, without calling Polaris."""

    model_config = ConfigDict(extra="forbid")

    status: str


class QueryRequest(BaseModel):
    """SQL text for POST /api/v1/query."""

    model_config = ConfigDict(extra="forbid")

    sql: str = Field(min_length=1)


class KernelRead(BaseModel):
    """A running notebook kernel. Secrets stay on the kernel process."""

    model_config = ConfigDict(extra="forbid")

    kernel_id: str
    ticket: str
    session_token: str
    ws_path: str
    engines: list[str]
    spark_catalogs: list[str]
    warnings: list[str]


class KernelStatus(BaseModel):
    """Result of interrupt or restart."""

    model_config = ConfigDict(extra="forbid")

    kernel_id: str
    status: str


class KernelTicketRead(BaseModel):
    """A fresh channel ticket for a kernel the caller already owns."""

    model_config = ConfigDict(extra="forbid")

    ticket: str
    ws_path: str
