"""Shared enums, pagination, and common types."""

from enum import Enum
from typing import Generic, TypeVar

from pydantic import BaseModel, Field


class Sport(str, Enum):
    NBA = "nba"
    LOL = "lol"
    FOOTBALL = "football"


class EventStatus(str, Enum):
    SCHEDULED = "scheduled"
    POSTPONED = "postponed"
    CANCELLED = "cancelled"
    COMPLETED = "completed"


class MatchFormat(str, Enum):
    SINGLE = "single"
    BEST_OF_3 = "bo3"
    BEST_OF_5 = "bo5"
    BEST_OF_7 = "bo7"


class PaginationParams(BaseModel):
    """Query parameters for paginated endpoints."""

    page: int = Field(default=1, ge=1)
    per_page: int = Field(default=25, ge=1, le=100)


class PaginationMeta(BaseModel):
    """Pagination metadata in responses."""

    page: int
    per_page: int
    total: int
    total_pages: int


T = TypeVar("T")


class PaginatedResponse(BaseModel, Generic[T]):
    """Generic paginated response wrapper."""

    data: list[T]
    pagination: PaginationMeta
