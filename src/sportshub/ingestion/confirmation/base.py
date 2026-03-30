"""Abstract base for external confirmation signal sources."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID

from sportshub.models.common import Sport


@dataclass
class ConfirmationResult:
    """Result of checking whether an event exists on an external source."""

    exists: bool
    confidence_boost: float
    raw_data: dict = field(default_factory=dict)
    checked_at: datetime = field(default_factory=datetime.utcnow)


class ConfirmationSource(ABC):
    """Interface for external confirmation signal providers (e.g. odds APIs)."""

    @property
    @abstractmethod
    def source_id(self) -> str:
        """Unique identifier for this confirmation source."""

    @abstractmethod
    async def check_event_exists(
        self,
        sport: Sport,
        home_team: str,
        away_team: str,
        scheduled_at: datetime,
    ) -> ConfirmationResult:
        """Check whether a single event exists on this external source."""

    @abstractmethod
    async def check_batch(
        self,
        events: list[dict],
    ) -> dict[UUID, ConfirmationResult]:
        """Check multiple events efficiently.

        Each dict in *events* must contain keys:
            id (UUID), sport (Sport), home_team (str),
            away_team (str), scheduled_at (datetime).

        Returns a mapping from event id to its confirmation result.
        """
