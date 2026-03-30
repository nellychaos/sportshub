"""Constraint engine — runs all applicable constraints against events."""

from uuid import UUID

import sqlalchemy as sa
import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from sportshub.db.tables import events_table
from sportshub.validation.constraints.base import Constraint, Violation
from sportshub.validation.constraints.lol import LoLTeamMatchSpacing
from sportshub.validation.constraints.nba import (
    NBANoSameDayDoubleHeader,
    NBASeasonDateRange,
)
from sportshub.validation.constraints.football import FIFATeamRestPeriod

logger = structlog.get_logger()


class ConstraintEngine:
    """Orchestrates schedule-pattern validation across all registered constraints.

    Constraints are auto-registered at init time. The engine filters applicable
    constraints by sport before running them.
    """

    def __init__(self) -> None:
        self._constraints: list[Constraint] = [
            NBANoSameDayDoubleHeader(),
            NBASeasonDateRange(),
            LoLTeamMatchSpacing(),
            FIFATeamRestPeriod(),
        ]

    @property
    def constraints(self) -> list[Constraint]:
        return list(self._constraints)

    def _applicable(self, sport: str) -> list[Constraint]:
        """Return constraints that apply to the given sport."""
        return [
            c for c in self._constraints
            if c.sport is None or c.sport.value == sport
        ]

    async def validate_event(
        self,
        event_id: UUID,
        session: AsyncSession,
    ) -> list[Violation]:
        """Run all applicable constraints against a single event.

        Stores violations in the event's metadata JSONB under the key
        ``"violations"``. Returns the list of violations found.
        """
        # Fetch event data
        stmt = sa.select(events_table).where(events_table.c.id == event_id)
        result = await session.execute(stmt)
        row = result.first()

        if row is None:
            logger.warning("validation_event_not_found", event_id=str(event_id))
            return []

        event_data = dict(row._mapping)
        sport = event_data["sport"]
        applicable = self._applicable(sport)

        violations: list[Violation] = []
        for constraint in applicable:
            try:
                violation = await constraint.check(event_id, event_data, session)
                if violation is not None:
                    violations.append(violation)
            except Exception:
                logger.exception(
                    "constraint_check_failed",
                    constraint=constraint.name,
                    event_id=str(event_id),
                )

        # Persist violations to metadata JSONB
        violation_dicts = [
            {
                "constraint_name": v.constraint_name,
                "severity": v.severity,
                "message": v.message,
                "details": v.details,
            }
            for v in violations
        ]

        # Merge into existing metadata (preserve other keys)
        current_meta = event_data.get("metadata") or {}
        current_meta["violations"] = violation_dicts

        update_stmt = (
            sa.update(events_table)
            .where(events_table.c.id == event_id)
            .values(metadata=current_meta)
        )
        await session.execute(update_stmt)

        if violations:
            logger.info(
                "validation_violations_found",
                event_id=str(event_id),
                count=len(violations),
                constraints=[v.constraint_name for v in violations],
            )

        return violations

    async def validate_batch(
        self,
        event_ids: list[UUID],
        session: AsyncSession,
    ) -> dict[UUID, list[Violation]]:
        """Validate a batch of events. Returns a mapping of event_id -> violations."""
        results: dict[UUID, list[Violation]] = {}
        for eid in event_ids:
            try:
                results[eid] = await self.validate_event(eid, session)
            except Exception:
                logger.exception(
                    "batch_validation_error",
                    event_id=str(eid),
                )
                results[eid] = []
        return results
