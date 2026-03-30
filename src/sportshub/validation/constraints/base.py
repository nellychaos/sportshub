"""Base classes for schedule pattern validation constraints."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Literal
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from sportshub.models.common import Sport


@dataclass
class Violation:
    """A single constraint violation found during validation."""

    constraint_name: str
    severity: Literal["error", "warning"]
    message: str
    event_id: UUID
    details: dict = field(default_factory=dict)


class Constraint(ABC):
    """Abstract base for schedule pattern constraints.

    Each constraint checks a single rule against an event and its context.
    Subclasses must set ``name``, ``sport``, ``severity`` and implement ``check``.
    """

    name: str
    sport: Sport | None  # None means "applies to all sports"
    severity: Literal["error", "warning"]

    @abstractmethod
    async def check(
        self,
        event_id: UUID,
        event_data: dict,
        session: AsyncSession,
    ) -> Violation | None:
        """Evaluate the constraint for a single event.

        Parameters
        ----------
        event_id:
            Primary key of the canonical event.
        event_data:
            Dict-style row data for the event (sport, home_team_id, away_team_id,
            scheduled_at, competition_id, etc.).
        session:
            Active async SQLAlchemy session for any DB lookups.

        Returns
        -------
        A ``Violation`` if the constraint is violated, otherwise ``None``.
        """
